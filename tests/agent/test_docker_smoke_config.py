"""The Docker smoke probe must read the same configuration as the server."""

import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

pytest.importorskip("dotenv")
pytest.importorskip("ruamel.yaml")
pytest.importorskip("cryptography")

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("online", "override", "expected"),
    [(True, None, "1"), (False, None, "0"), (True, "false", "0"), (False, "true", "1")],
)
def test_knowledge_probe_uses_environment_overlay_and_environment_precedence(tmp_path, online, override, expected):
    script = (ROOT / "scripts/e2e_docker.sh").read_text()
    command = script.split("KNOWLEDGE_AVAILABLE=$(", 1)[1].split("2>/dev/null", 1)[0]
    arguments = shlex.split(command.replace("\\\n", ""))
    probe = arguments[arguments.index("-c") + 1]

    files = {
        "config.yaml": f"INCLUDE: [config.llm.yaml]\nKNOWLEDGE_ONLINE: {str(not online).lower()}\n",
        "config.llm.yaml": "{}\n",
        "config.smoketest.yaml": f"KNOWLEDGE_ONLINE: {str(online).lower()}\n",
    }
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    env = {
        **os.environ,
        "ENV": "smoketest",
        "KNOWLEDGE_INDEX": str(tmp_path / "missing.sqlite3"),
        "PYTHONPATH": str(ROOT),
    }
    env.pop("KNOWLEDGE_ONLINE", None)
    if override is not None:
        env["KNOWLEDGE_ONLINE"] = override
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=20, check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == expected, result.stderr
    assert {name: (tmp_path / name).read_text() for name in files} == files
