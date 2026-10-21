"""A local Python sandbox for the chat agent's arithmetic tool.

Two runners, one contract. The default is a subprocess with a clean
environment, a temporary working directory, CPU/address-space/file-size
limits and a timeout. It is a resource boundary, not a security boundary: the
code can still read world-readable files and open sockets. Set
``PYTHON_SANDBOX_IMAGE`` to run the same script in a Docker container with no
network, a read-only root and a tmpfs instead.

The subprocess runner starts Python with ``-I -S``: no environment, no user
site, no third-party packages and no ``mirobody`` import, so code that runs
here cannot read the app's config or credentials.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SandboxResult:
    """What one run returned, already capped for the model."""

    output: str
    exit_code: int | None = None
    timed_out: bool = False
    truncated: bool = False


_RUNNER = """\
import json
import resource
import runpy


def _limit(which, soft, hard=None):
    try:
        resource.setrlimit(which, (soft, hard if hard is not None else soft))
    except (AttributeError, OSError, ValueError):
        pass


_limit(resource.RLIMIT_CPU, %(cpu)d, %(cpu)d + 1)
_limit(resource.RLIMIT_AS, %(memory)d)
_limit(resource.RLIMIT_FSIZE, 8 * 1024 * 1024)
_limit(resource.RLIMIT_NOFILE, 64)
_limit(resource.RLIMIT_NPROC, 32)

with open("data.json", encoding="utf-8") as handle:
    data = json.load(handle)

runpy.run_path("main.py", run_name="__main__", init_globals={"data": data})
"""


class PythonSandbox:
    """Run model-written Python with a timeout, a clean environment and caps.

    ``image`` selects Docker. Empty means the local subprocess. A Docker run
    needs the app's host to have the ``docker`` command and access to its
    daemon; the compose stack does not mount the daemon by default.
    """

    def __init__(self, *, image: str = "", timeout: float = 15.0, max_output: int = 4000) -> None:
        self.image = image.strip()
        self.timeout = float(timeout)
        self.max_output = int(max_output)

    def run(self, code: str, data: list[dict] | None = None,
            timeout: float | None = None) -> SandboxResult:
        seconds = float(timeout if timeout is not None else self.timeout)
        with tempfile.TemporaryDirectory(prefix="mirobody-python-") as tmp:
            Path(tmp, "data.json").write_text(
                json.dumps(data or [], ensure_ascii=False), encoding="utf-8")
            Path(tmp, "main.py").write_text(code, encoding="utf-8")
            Path(tmp, "runner.py").write_text(
                _RUNNER % {"cpu": max(1, int(seconds)), "memory": 512 * 1024 * 1024},
                encoding="utf-8")
            if self.image:
                return self._docker(tmp, seconds)
            return self._subprocess(tmp, seconds)

    def _result(self, stdout: str | None, stderr: str | None,
                exit_code: int | None) -> SandboxResult:
        if exit_code == -getattr(signal, "SIGXCPU", 24):
            return SandboxResult("error: timed out", exit_code, timed_out=True)
        text = ((stdout or "") + (stderr or "")).strip()
        truncated = len(text) > self.max_output
        text = text[: self.max_output]
        if exit_code not in (0, None):
            text = f"error (exit {exit_code}): {text or '(no output)'}"
        return SandboxResult(text or "(no output; use print(...))", exit_code, truncated=truncated)

    def _subprocess(self, tmp: str, timeout: float) -> SandboxResult:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": tmp,
            "TMPDIR": tmp,
            "LANG": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
        }
        try:
            proc = subprocess.Popen(
                [sys.executable, "-I", "-S", "runner.py"],
                cwd=tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True,
            )
        except OSError as exc:
            return SandboxResult(f"error: could not start Python ({type(exc).__name__})")
        try:
            stdout, stderr = proc.communicate(timeout=timeout + 2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            stdout, stderr = proc.communicate()
            return SandboxResult("error: timed out", timed_out=True)
        return self._result(stdout, stderr, proc.returncode)

    def _docker(self, tmp: str, timeout: float) -> SandboxResult:
        command = [
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "64", "--memory", "512m", "--cpus", "1",
            "-v", f"{tmp}:/workspace:rw", "-w", "/workspace",
            self.image, "python", "-I", "-B", "runner.py",
        ]
        try:
            proc = subprocess.run(command, capture_output=True, text=True,
                                  timeout=timeout + 10)
        except FileNotFoundError:
            return SandboxResult("error: docker is not installed")
        except subprocess.TimeoutExpired:
            return SandboxResult("error: timed out", timed_out=True)
        return self._result(proc.stdout, proc.stderr, proc.returncode)


@lru_cache(maxsize=1)
def default_sandbox() -> PythonSandbox:
    """The configured sandbox. ``PYTHON_SANDBOX_IMAGE`` selects Docker."""
    image = os.environ.get("PYTHON_SANDBOX_IMAGE", "")
    timeout = float(os.environ.get("PYTHON_SANDBOX_TIMEOUT", "") or 15)
    max_output = int(os.environ.get("PYTHON_SANDBOX_MAX_OUTPUT", "") or 4000)
    if not image:
        logger.info("python sandbox runs as a local subprocess; set PYTHON_SANDBOX_IMAGE for container isolation")
    return PythonSandbox(image=image, timeout=timeout, max_output=max_output)
