# Reproducing the Windows local-model benchmarks

The results under `results/` were recorded on 2026-10-12/13 against
`df5f62a` and image `1.5.6-rc1`, with local CPU-preset changes for the completed
CPU pass. Use the steps below for a new run on the release you want to test.
The historical summaries do not certify 1.5.7. All accounts and documents
come from the seed-7 synthetic generator; use a dedicated test stack.

## Windows and WSL setup

Install WSL 2 and Ubuntu from an elevated PowerShell, then reboot if prompted:

```powershell
wsl --install -d Ubuntu-24.04
```

Install Docker Desktop with the WSL 2 backend. Enable the distribution in
Settings > Resources > WSL integration. From Ubuntu, `docker version` must
reach the server. If the socket refuses access, run
`sudo usermod -aG docker "$USER"`, then `wsl --shutdown` from PowerShell and
open Ubuntu again. Give Docker enough memory for the model pair and services
(the recorded machine had 32 GB; see [the model guide](../../docs/local-models.md)).

Inside Ubuntu:

```bash
sudo apt update
sudo apt install -y git git-lfs curl unzip
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
git clone https://github.com/thetahealth/mirobody.git ~/mirobody
cd ~/mirobody
git lfs install
git lfs pull
uv venv --python 3.12
uv pip install -e '.[app,test]'
```

Check out the branch or release under test before installing it. The original
`fix/local-model-harness` branch and RC image are historical identifiers.
For the current 100-question suite, build and validate the corpus using
[the source recipe](../local_models/README.md#source-and-reproducibility),
use a fresh `BENCH_QA_GENERATION`, and enable `EVAL_AS_OF_ENABLED=true` on
the isolated test server. Its manifest validates source hashes before loading.
The archived 24+6-question Windows measurements used the historical
[reproduction recipe](../local_models/HISTORY.md#reproduce) and
`files.jsonl` SHA-256
`5bad865b1e5cba4979d453338f79b2d5bf342e9c19b8cd8061901ae1dab09b11`;
that hash does not identify the new suite.

## Docker deployment and quality

The production Compose services keep the router internal. Benchmark clients
need its host port; add this test-only override at the checkout root:

```bash
cat > compose.override.yaml <<'YAML'
services:
  llama:
    ports: ["127.0.0.1:8080:8080"]
  llama_cpu:
    ports: ["127.0.0.1:8080:8080"]
YAML
COMPOSE_PROFILES=local ./deploy.sh       # NVIDIA GPU
# or: COMPOSE_PROFILES=local-cpu ./deploy.sh
```

Only one model profile should run at a time. `deploy.sh` uses the release image
named by Compose; for code changes, set `MIROBODY_IMAGE` to your built image
before deployment and record both the checkout and image in your results.
Confirm deployment with `docker compose ps`, then:

```bash
curl --noproxy '*' -fsS http://127.0.0.1:18060/api/health
curl --noproxy '*' -fsS http://127.0.0.1:8080/models
```

Choose the small local model on the setup page **before the first document
load**. Current main permits changing models at the same `.env`-pinned
endpoint. Keep `LOCAL_BASE_URL=http://llama:8080/v1` for Docker runs; unlike the
historical RC, it needs no deletion for a size switch. Moving to another
endpoint still requires changing `.env` and recreating the app containers.

```bash
export MIROBODY_DIR=$HOME/mirobody
export CORPUS=$HOME/mirobody-gen-seed7
export OUT=$HOME/mirobody-results
export BENCH_QA_GENERATION=win157-test1
export NO_PROXY=127.0.0.1,localhost,host.docker.internal,llama,pg
export no_proxy=$NO_PROXY
bash benchmarks/windows/wsl/run_quality.sh docker-gpu
# For a CPU stack instead: RUNS=1 bash benchmarks/windows/wsl/run_quality.sh docker-cpu
```

Use a fresh generation and output directory for each new stack or changed
extraction pipeline. The loader's state is in
`benchmarks/local_models/results/load.json`; names `qa1` through `qa6` and
`win1` already refer to earlier records. The runner verifies completed
document state and its commands before marking a generation loaded. Printed
rows missing from a completed extraction remain evaluation diagnostics;
read the verification report before interpreting QA scores.

The runner emits a nonzero status for operational failures. Model quality
scores remain measurements: a completed run with 20/24 correct is recorded,
not converted into a claim that every answer passed. Collection:

```bash
OUT=$HOME/mirobody-results bash benchmarks/windows/wsl/collect.sh
# Include native outputs too: SPEED_RESULTS=/mnt/c/path/to/results OUT=$HOME/mirobody-results bash benchmarks/windows/wsl/collect.sh
```

The archive contains synthetic transcripts and logs from the dedicated stack.
The historical GPU QA transcripts and load-verification file were not included
in the source branch; its reported scores are summaries, not independently
regradable evidence. The CPU archive here includes its summary, metadata and
run log; speed results include per-turn output and server logs.

## Native Windows speed matrix and thread sweep

Keep a Windows-drive checkout at the same release as the WSL app. Download
builds from [llama.cpp b11429](https://github.com/ggml-org/llama.cpp/releases/tag/b11429)
for historical comparisons, or explicitly record another version. Expand each
build into a directory under `C:\mirobody-bench`:

| Directory | Recorded build |
| --- | --- |
| `llama-cpu` | `llama-b11429-bin-win-cpu-x64.zip` |
| `llama-vulkan` | `llama-b11429-bin-win-vulkan-x64.zip` |
| `llama-cuda-13.4` | `llama-b11429-bin-win-cuda-13.4-x64.zip`, plus the matching CUDA DLL archive if needed |

The RTX 5060 is compute capability 12.0. CUDA 12.4 builds do not include its
kernels; the recorded CUDA 13.4 build works. The CUDA runtime DLLs must match
the selected build. Use `-CudaServer C:\path\to\llama-server.exe` for an explicit compatible build.
Install `uv` with `winget install --id astral-sh.uv`; the runner resolves the
WinGet location even in a shell whose PATH predates installation.

Put the weights under `C:\mirobody-bench\models`:

- `MiniCPM5-2B-Q8_0.gguf`, from [OpenBMB MiniCPM5-2B-GGUF](https://huggingface.co/openbmb/MiniCPM5-2B-GGUF) (2,679,710,688 bytes
  in the recorded run).
- `MiniCPM5-2.6B-DSpark.gguf`, [the OpenBMB draft head](https://huggingface.co/openbmb/MiniCPM5-2B-DSpark-GGUF) (652,730,240 bytes in
  the recorded run), when selecting a DSpark configuration.

Scripts, cases and the chat template stay in the repository; no external
`speed/` kit or `_work/cpu_one.json` is required. Stop Docker model services
before native measurements so they do not compete for hardware or ports:

```bash
docker compose --profile local --profile local-cpu stop llama llama_cpu
```

From PowerShell in the Windows checkout:

```powershell
powershell -ExecutionPolicy Bypass -File benchmarks\windows\speed_windows.ps1 -Kit C:\mirobody-bench -Only gpu
powershell -ExecutionPolicy Bypass -File benchmarks\windows\speed_windows.ps1 -Kit C:\mirobody-bench -Only cpu
powershell -ExecutionPolicy Bypass -File benchmarks\windows\cpu-thread-sweep.ps1 -Kit C:\mirobody-bench
```

Each invocation uses a fresh result directory by default and prints its path;
`-Results` names your own. To choose other thread counts from a PowerShell
session, invoke the script directly with an array (the `powershell -File`
command-line binder does not parse a comma-separated array):

```powershell
& .\benchmarks\windows\cpu-thread-sweep.ps1 -Kit C:\mirobody-bench -Threads @(4, 8, 16)
``` Missing dependencies, failed startup and failed
probes return nonzero. Rebuild the speed table using:

```powershell
$env:SPEED_DIR = 'C:\path\printed\by\runner'
python benchmarks\windows\results\speed\analyze_speed.py
```

## Native Windows model service for the app

Follow the [English](../../docs/local-models.md) or
[Chinese](../../docs/local-models.zh-CN.md) guide to start `llama-server`
from a Windows drive checkout with the production preset. The app reaches it
through `http://host.docker.internal:8080/v1`. Change any pinned endpoint in
`.env` to that address and recreate `mirobody` and `mirobody_worker`, then
choose the small local model. No data volume is deleted. A native QA run uses:

```bash
bash benchmarks/windows/wsl/run_quality.sh native
```

The historical record verifies discovery and direct completions on this route;
it does not contain a native 30-question pass.

## Docker CPU A/B/C experiment

With only the CPU profile running and the benchmark port override above:

```bash
MIROBODY_DIR=$HOME/mirobody bash benchmarks/windows/cpu-route.sh
```

The runner compares GPU preset A, CPU preset B, and CPU preset C with the
container's available thread count, checks model readiness, and restores the
original preset and running state on exit. Positional arguments can name a
case and output directory. This restarts only `llama_cpu`; run it on the test
stack while no QA job is using that model service. Do not turn a single
machine's winning thread count or DSpark setting into a global default.
