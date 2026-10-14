"""Executable shell gates for Windows benchmark orchestration, using synthetic stubs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
WINDOWS = ROOT / "benchmarks" / "windows"
pytestmark = pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="WSL shell gates run on POSIX")


def executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n" + body, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def harness(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "repo with spaces"
    bench = repo / "benchmarks/local_models"
    bench.mkdir(parents=True)
    (bench / "results").mkdir()
    (bench / "plan.json").write_text(json.dumps({"qa_people": {"p001": {"documents": ["synthetic.pdf"], "genomics": False}}}))
    (bench / "common.py").write_text('''
import os
def qa_email(person):
    return person + "@synthetic.test"
class Account:
    user_id = "synthetic-user"
    def get(self, path, **kwargs):
        files = [] if os.environ.get("STALE_RECORD") else [{"file_key": "synthetic-file"}]
        return {"data": {"files": files}}
def sign_in(email, **kwargs):
    return Account()
''')
    (bench / "load.py").write_text('''
import json, os, sys
from pathlib import Path
LOAD_STATE = Path(__file__).parent / "results/load.json"
if __name__ == "__main__":
    with open(os.environ["CALLS"], "a") as log:
        log.write(json.dumps({"command": "load", "verify": "--verify" in sys.argv}) + "\\n")
    if "--verify" in sys.argv:
        sys.exit(int(os.environ.get("VERIFY_EXIT", "0")))
    code = int(os.environ.get("LOAD_EXIT", "0"))
    if not code:
        status = "failed" if os.environ.get("INCOMPLETE") else "done"
        LOAD_STATE.write_text(json.dumps({"p001@synthetic.test": {"user_id": "synthetic-user", "tz": "UTC", "lang": "en", "steps": {"documents": {"synthetic.pdf": {"status": status, "file_key": "synthetic-file"}}}}}))
    sys.exit(code)
''')
    (bench / "run.py").write_text('''
import json, os, sys
from pathlib import Path
with open(os.environ["CALLS"], "a") as log:
    log.write(json.dumps({"command": "run"}) + "\\n")
code = int(os.environ.get("RUN_EXIT", "0"))
if not code:
    out = Path(sys.argv[sys.argv.index("--out") + 1])
    (out / "summary.md").write_text("| Questions passed | 20/24 |\\n")
sys.exit(code)
''')
    (repo / ".venv/bin").mkdir(parents=True)
    (repo / ".venv/bin/python").symlink_to(sys.executable)
    docker_dir = repo / "docker"
    docker_dir.mkdir()
    for preset in ("local-models.ini", "local-models-cpu.ini"):
        (docker_dir / preset).write_text("version = 1\n[minicpm5-2b]\nc = 32768\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "files.jsonl").write_text("{}\n")
    binary = tmp_path / "bin"
    binary.mkdir()
    calls = tmp_path / "calls.jsonl"
    calls.touch()
    executable(binary / "curl", '''
import json, os, sys
with open(os.environ["CALLS"], "a") as log:
    log.write(json.dumps({"command": "curl", "args": sys.argv[1:]}) + "\\n")
assert "--noproxy" in sys.argv
url = next(a for a in sys.argv if a.startswith("http"))
if url.endswith("/api/health"):
    print('{"status":"ok"}')
elif url.endswith("/health"):
    print(json.dumps({"status": "unavailable" if os.environ.get("HEALTH_FAIL") else "ok"}))
elif url.endswith("/mirobody.json"):
    print(json.dumps({"__MODEL_SETUP__": os.environ.get("SETUP", "ready")}))
elif url.endswith("/models/load"):
    print('{"success": true}')
elif url.endswith("/models"):
    print(json.dumps({"data": [{"id":"minicpm5-2b", "status": {"value":"loading" if os.environ.get("MODEL_FAIL") else "loaded"}}]}))
''')
    executable(binary / "docker", '''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
preset = os.environ.get("LOCAL_MODELS_PRESET")
with open(os.environ["CALLS"], "a") as log:
    record = {"command": "docker", "args": args, "preset": preset}
    if preset and Path(preset).is_file():
        record["content"] = Path(preset).read_text()
    log.write(json.dumps(record) + "\\n")
if os.environ.get("DOCKER_FAIL"):
    sys.exit(8)
if "cp" in args:
    source = Path(os.environ["MIROBODY_DIR"]) / "docker/local-models-cpu.ini"
    Path(args[-1]).write_text("different preset" if os.environ.get("DIFFERENT_MOUNT") else source.read_text())
elif "inspect" in args:
    if "State.Running" in args[args.index("--format")+1]:
        print(os.environ.get("ORIGINAL_RUNNING", "true"))
    else:
        print("/daemon-internal/preset.ini" if os.environ.get("INTERNAL_MOUNT") else Path(os.environ["MIROBODY_DIR"]) / "docker/local-models-cpu.ini")
elif "config" in args and "--format" in args:
    print(json.dumps({"services": {"llama_cpu": {"volumes": [{"source": str(Path(os.environ["MIROBODY_DIR"]) / "docker/local-models-cpu.ini"), "target": "/presets/local-models.ini"}]}}}))
elif "config" in args:
    print("synthetic-image:1.5.7")
elif "ps" in args and "--quiet" in args:
    if not os.environ.get("ABSENT_CONTAINER"):
        print("synthetic-container")
elif "nproc" in args:
    print("12")
elif "logs" in args:
    print("n_threads = 12")
elif "stats" in args:
    print("synthetic 1MiB")
if "up" in args and os.environ.get("UP_FAIL") and preset and "local-models-cpu.ini" not in preset:
    sys.exit(6)
''')
    executable(binary / "uv", '''
import json, os, sys
from pathlib import Path
with open(os.environ["CALLS"], "a") as log:
    log.write(json.dumps({"command": "uv", "args": sys.argv[1:]}) + "\\n")
code = int(os.environ.get("PROBE_EXIT", "0"))
if not code:
    Path(sys.argv[-1]).write_text(json.dumps({"synthetic": {"pp_tps": 12, "ttft_s": 1, "gen_tps": 2, "wall": 3}}))
sys.exit(code)
''')
    env = os.environ.copy()
    env.update({
        "PATH": str(binary) + os.pathsep + env["PATH"],
        "HOME": str(tmp_path), "MIROBODY_DIR": str(repo), "CORPUS": str(corpus),
        "OUT": str(tmp_path / "results"), "CALLS": str(calls), "RUNS": "1",
        "BENCH_QA_GENERATION": "win-gate", "BENCH_HEALTH_ATTEMPTS": "1",
        "BENCH_MODEL_ATTEMPTS": "1", "BENCH_WAIT_SECONDS": "0",
        "HTTP_PROXY": "http://127.0.0.1:1", "http_proxy": "http://127.0.0.1:1",
    })
    return repo, env


def invoke(script: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(script), *args], env=env, cwd=ROOT, capture_output=True, text=True, timeout=20)


def calls(env: dict[str, str]) -> list[dict]:
    return [json.loads(line) for line in Path(env["CALLS"]).read_text().splitlines()]


def loaded(repo: Path, env: dict[str, str]) -> None:
    state = {"p001@synthetic.test": {"user_id": "synthetic-user", "tz": "UTC", "lang": "en", "steps": {"documents": {"synthetic.pdf": {"status": "done", "file_key": "synthetic-file"}}}}}
    (repo / "benchmarks/local_models/results/load.json").write_text(json.dumps(state))
    out = Path(env["OUT"])
    out.mkdir()
    (out / "load-win-gate.done").touch()


def test_quality_loaded_accounts_can_run_while_setup_needed(harness) -> None:
    repo, env = harness
    loaded(repo, env)
    env["SETUP"] = "needed"
    result = invoke(WINDOWS / "wsl/run_quality.sh", env, "docker-cpu")
    assert result.returncode == 0, result.stdout + result.stderr
    seen = calls(env)
    assert any(c["command"] == "run" for c in seen)
    assert [c["verify"] for c in seen if c["command"] == "load"] == [True]
    assert not any(c["command"] == "curl" and any(a.endswith("/mirobody.json") for a in c["args"]) for c in seen)
    assert len(list(Path(env["OUT"]).glob("win-docker-cpu-*/summary.md"))) == 1


@pytest.mark.parametrize("failure", ["LOAD_EXIT", "VERIFY_EXIT", "INCOMPLETE"])
def test_quality_does_not_mark_failed_load_complete(harness, failure: str) -> None:
    _, env = harness
    env[failure] = "7" if failure.endswith("EXIT") else "1"
    result = invoke(WINDOWS / "wsl/run_quality.sh", env, "docker-gpu")
    assert result.returncode != 0
    assert not (Path(env["OUT"]) / "load-win-gate.done").exists()
    assert not any(c["command"] == "run" for c in calls(env))


def test_quality_requires_ready_only_before_loading(harness) -> None:
    _, env = harness
    env["SETUP"] = "needed"
    result = invoke(WINDOWS / "wsl/run_quality.sh", env, "native")
    assert result.returncode != 0
    assert "Configure a model before loading" in result.stdout
    assert not any(c["command"] in ("run", "load") for c in calls(env))


def test_quality_propagates_runner_failure_despite_stale_summary(harness) -> None:
    repo, env = harness
    loaded(repo, env)
    old = Path(env["OUT"]) / "win-docker-gpu-1"
    old.mkdir()
    (old / "summary.md").write_text("old success")
    env["RUN_EXIT"] = "9"
    result = invoke(WINDOWS / "wsl/run_quality.sh", env, "docker-gpu")
    assert result.returncode == 9
    assert "exit 9" in result.stdout
    assert (old / "summary.md").read_text() == "old success"


def test_quality_validates_live_accounts_before_trusting_marker(harness) -> None:
    repo, env = harness
    loaded(repo, env)
    env["VERIFY_EXIT"] = "11"
    result = invoke(WINDOWS / "wsl/run_quality.sh", env, "docker-cpu")
    assert result.returncode == 11
    assert not any(c["command"] == "run" for c in calls(env))


def test_quality_rejects_stale_marker_even_when_verify_exits_zero(harness) -> None:
    repo, env = harness
    loaded(repo, env)
    env["STALE_RECORD"] = "1"
    result = invoke(WINDOWS / "wsl/run_quality.sh", env, "docker-cpu")
    assert result.returncode != 0
    assert "differ from this stack" in result.stdout
    assert not (Path(env["OUT"]) / "load-win-gate.done").exists()
    assert not any(c["command"] == "run" for c in calls(env))


@pytest.mark.parametrize("generation", ["qa1", "qa6", "../../unsafe"])
def test_quality_rejects_foreign_or_invalid_record_name(harness, generation: str) -> None:
    _, env = harness
    env["BENCH_QA_GENERATION"] = generation
    result = invoke(WINDOWS / "wsl/run_quality.sh", env, "docker-gpu")
    assert result.returncode == 2
    assert not calls(env)


@pytest.mark.parametrize("failure", ["HEALTH_FAIL", "MODEL_FAIL", "PROBE_EXIT", "UP_FAIL"])
def test_cpu_failure_is_visible_and_restores_stopped_service(harness, failure: str) -> None:
    _, env = harness
    env[failure] = "7" if failure == "PROBE_EXIT" else "1"
    env["ORIGINAL_RUNNING"] = "false"
    out = Path(env["OUT"])
    result = invoke(WINDOWS / "cpu-route.sh", env, str(WINDOWS / "results/raw/cpu_case_one.json"), str(out))
    assert result.returncode != 0
    seen = calls(env)
    restored = [c for c in seen if c["command"] == "docker" and "up" in c["args"] and c.get("preset", "").endswith("local-models-cpu.ini")]
    assert restored
    assert any(c["command"] == "docker" and "stop" in c["args"] for c in seen)
    if failure in ("HEALTH_FAIL", "MODEL_FAIL", "UP_FAIL"):
        assert not any(c["command"] == "uv" for c in seen)


def test_cpu_all_threads_uses_container_count_and_restores_running_service(harness) -> None:
    repo, env = harness
    out = Path(env["OUT"])
    result = invoke(WINDOWS / "cpu-route.sh", env, str(WINDOWS / "results/raw/cpu_case_one.json"), str(out))
    assert result.returncode == 0, result.stdout + result.stderr
    seen = calls(env)
    variants = [c for c in seen if c["command"] == "docker" and "up" in c["args"]]
    assert len(variants) == 4
    assert "threads = 12" in variants[2]["content"]
    assert variants[-1]["preset"] == str(repo / "docker/local-models-cpu.ini")
    assert not Path(variants[2]["preset"]).exists()
    assert not any(c["command"] == "docker" and "stop" in c["args"] for c in seen)
    assert len(list(out.glob("*.out.json"))) == 3
    assert all("speed_probe.py" in " ".join(c["args"]) for c in seen if c["command"] == "uv")


def test_cpu_removes_created_service_if_original_absent(harness) -> None:
    _, env = harness
    env["ABSENT_CONTAINER"] = "1"
    env["HEALTH_FAIL"] = "1"
    result = invoke(WINDOWS / "cpu-route.sh", env, str(WINDOWS / "results/raw/cpu_case_one.json"), env["OUT"])
    assert result.returncode != 0
    assert any(c["command"] == "docker" and "rm" in c["args"] for c in calls(env))


@pytest.mark.parametrize("different", [False, True])
def test_cpu_desktop_bind_path_requires_matching_restore_source(harness, different: bool) -> None:
    _, env = harness
    env["INTERNAL_MOUNT"] = "1"
    if different:
        env["DIFFERENT_MOUNT"] = "1"
    else:
        env["HEALTH_FAIL"] = "1"
    result = invoke(WINDOWS / "cpu-route.sh", env, str(WINDOWS / "results/raw/cpu_case_one.json"), env["OUT"])
    assert result.returncode != 0
    seen = calls(env)
    mutations = [c for c in seen if c["command"] == "docker" and "up" in c["args"]]
    if different:
        assert not mutations
        assert "align Compose first" in result.stderr
    else:
        assert len(mutations) == 2
        assert mutations[-1]["preset"].endswith("local-models-cpu.ini")


def test_cpu_rejects_stale_output_directory(harness) -> None:
    _, env = harness
    out = Path(env["OUT"])
    out.mkdir()
    old = out / "cpu-A-deployed.out.json"
    old.write_text("old success")
    result = invoke(WINDOWS / "cpu-route.sh", env, str(WINDOWS / "results/raw/cpu_case_one.json"), str(out))
    assert result.returncode != 0
    assert not calls(env)
    assert old.read_text() == "old success"


def test_collect_preserves_diagnostic_errors_and_native_results(harness) -> None:
    _, env = harness
    out = Path(env["OUT"])
    out.mkdir()
    (out / "qa.txt").write_text("synthetic QA")
    native = out.parent / "speed-run"
    native.mkdir()
    (native / "cuda-base.out.json").write_text("{}")
    kit = out.parent / "kit"
    env.update({"DOCKER_FAIL": "1", "KIT": str(kit), "SPEED_RESULTS": str(native)})
    result = invoke(WINDOWS / "wsl/collect.sh", env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "diagnostic exited 8" in (out / "environment.txt").read_text()
    archive = next(kit.glob("mirobody-windows-results-*.tgz"))
    with tarfile.open(archive) as packed:
        assert "results/qa.txt" in packed.getnames()
        assert "results/native/speed-run/cuda-base.out.json" in packed.getnames()


def test_collect_missing_results_fails(harness) -> None:
    _, env = harness
    result = invoke(WINDOWS / "wsl/collect.sh", env)
    assert result.returncode != 0
    assert "No results directory" in result.stderr
