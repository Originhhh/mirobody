# Windows: the local models on a native Windows machine


The evaluation in [`../local_models`](../local_models) was written and measured
on Apple silicon and Linux. This directory is what happened when the same
candidate version was run on **Windows 11 with an RTX 5060**, one step at a
time, by following the hand-off manual the Windows experiment came with.

The measurements use the same synthetic cases and corpus as that directory.
They are historical RC results; the current runners have regression tests, but
these scores are not a fresh Windows 1.5.7 hardware pass.

| | |
| --- | --- |
| Machine | `i5-14400F` (10 cores / 16 threads), 32 GB, Windows 11 Pro 26200 |
| GPU | NVIDIA GeForce RTX 5060, 8151 MiB, compute capability **12.0 (`sm_120`)**, driver 591.86, CUDA 13.1 |
| Checkout under test | `df5f62a` ("local models: an 8-bit KV cache for the 2B's 64k"), 1.5.6-rc |
| Container image | `thetahealth4mirobody/mirobody:1.5.6-rc1` |
| llama.cpp | b11429, commit d81235049 |
| Docker | Docker Desktop 4.95.0, engine 29.8.2, WSL 2 backend, Ubuntu 24.04 |
| Corpus | seed 7, `files.jsonl` sha256 `5bad865b…` (matches, byte for byte) |

## What was measured

| Part | Script | Result |
| --- | --- | --- |
| **Speed**, GPU and CPU, 9 configs | [`speed_windows.ps1`](speed_windows.ps1) + [`speed_probe.py`](speed_probe.py) | [`results/speed/summary.md`](results/speed/summary.md) |
| **Deployment**, the Docker route | `../local_models` + `wsl/` | [`results/E1-deployment.md`](results/E1-deployment.md) |
| **Quality**, the 30 questions, GPU x3 | `wsl/run_quality.sh docker-gpu` | [`results/E3-quality.md`](results/E3-quality.md) |
| **Quality**, the 30 questions, CPU x1 | `wsl/run_quality.sh docker-cpu` | [`results/E3-quality.md`](results/E3-quality.md) |
| **Native llama-server** route, Vulkan | this directory | [`results/native-route.md`](results/native-route.md) |
| Every problem found | — | [`results/issues.md`](results/issues.md) |

[`RUNBOOK.md`](RUNBOOK.md) gives commands for a fresh checkout. The historical
reports retain the measured baseline; the table below separates their fixes
from experimental options.

| Finding | Current behavior for 1.5.7 |
| --- | --- |
| CPU used the GPU preset at `df5f62a` | Already fixed in main: `llama_cpu` mounts `docker/local-models-cpu.ini`, 32k/F16 KV |
| Model weight Q4/Q8 confusion | Both production presets request MiniCPM5-2B **Q8_0 weights**; KV quantization is separate |
| System proxy broke localhost benchmark requests | Already fixed in `local_models/common.py`; probes connect directly and WSL preflights bypass proxies |
| `.env` prevented a local size switch | Already fixed in main for the same pinned endpoint; changing an endpoint still requires editing `.env` |
| CPU probe duplicated native probe/template | One streamed probe and the production `docker/minicpm5-2b.jinja` |
| Failed measurement reused old results | Fresh output, explicit failed cases and nonzero runner exits |
| Native UNC working directory refused | English/Chinese guides use a Windows-drive checkout |
| 16 threads and DSpark gains | Hardware-specific experiments; production defaults remain unchanged |

Use a fresh test generation when re-running quality; original `win1` accounts
are not portable between stacks.

## Running the speed measurement

The script starts one `llama-server` per configuration on port 18557, sends the
same fixed agent turns through `/v1/chat/completions` (temperature 0, at most
700 tokens), records generation rate, prompt-processing rate, **time to first
token**, draft acceptance and memory, then stops the server and moves on.

```powershell
# Every configuration; or -Only gpu / -Only cpu
powershell -ExecutionPolicy Bypass -File benchmarks\windows\speed_windows.ps1 -Kit C:\mirobody-bench -Only gpu
```

| Configuration | What it is |
| --- | --- |
| `*-base` | 32k context, 16-bit KV cache: the 1.5.5 setting |
| `*-64k-q8` | 64k context, 8-bit KV cache, flash attention: the candidate's setting |
| `*-dspark7` | 32k plus OpenBMB's DSpark draft head (speculative, at most 7 tokens a step) |
| `vulkan-*` / `cuda-*` / `cpu4-*` | the matching build; `cpu4` uses 4 threads, to stand in for Docker's 4 cores |

Missing builds/dependencies fail the selected run. Each invocation prints a fresh
result directory (`-Results` overrides it); memory is the server process working
set and private bytes plus the VRAM delta. Scripts, cases and template stay in
the checkout; `-Kit` names only the downloaded binaries and weights.

`ONLY=vulkan-base,cuda-base` restricts the matrix to matching labels, so one
configuration can be repeated on its own.

## Three changes this machine needed

The hand-off manual downloads **CUDA 12.4**. An RTX 5060 is Blackwell
(`sm_120`), and CUDA 12.4 ships no `sm_120` kernels, so that build cannot run
there at all. Both scripts were changed; the details are in
[`results/issues.md`](results/issues.md):

1. **CUDA 13.4 instead of 12.4** — the same b11429 release publishes a
   `cuda-13.4` Windows build. `speed_windows.ps1` now reads the GPU's compute
   capability with `nvidia-smi` and prefers `llama-cuda-13.4` when it is 12 or
   newer; an explicit CUDA executable can be supplied for another compatible build.
2. **`uv` is resolved instead of assumed** — `winget` installs it under
   `%LOCALAPPDATA%\Microsoft\WinGet\Packages\…` and only widens `PATH` for
   shells started afterwards, so a bare `uv run` fails with
   `CommandNotFoundException` and every configuration is skipped in silence.
3. **The probe streams over `http.client`** — see below.

## Why the probe does not use `urllib`

`speed_probe.py` originally read a non-streaming response with
`urllib.request.urlopen`. On this machine a CPU turn died at about **268
seconds** with `[WinError 10054]` (sometimes HTTP 503) while `llama-server`
itself was fine: it finished the prompt, kept generating, and logged
`cancel task` only once the client had gone.

The cause is the **system HTTP proxy**. Windows has one configured
(`localhost:15236`), WSL inherits `http_proxy`/`https_proxy` from it, and
`urllib` honours those variables — so a request to `127.0.0.1:18557` was handed
to a proxy for which "localhost" means *Windows*, not the Linux side. The proxy
dropped the connection after a couple of minutes. GPU turns finish in seconds,
so only the CPU half of the matrix failed.

The probe now uses `http.client.HTTPConnection`, which does not read the proxy
environment, and asks for `stream: true` — which is also what makes the
time-to-first-token measurement possible. `run.py` in `../local_models` uses
`requests`, with local hosts exempted by `common.py`. External download traffic
still uses its configured proxy. The historical trap is described in
[`results/issues.md`](results/issues.md).

## Results

### Speed (generation/prompt medians; TTFT is the fastest recorded turn)

GPU, RTX 5060:

| Configuration | Generation tok/s | Prompt tok/s | TTFT s | Acceptance | VRAM |
| --- | --- | --- | --- | --- | --- |
| `vulkan-base` | 113.0 | 5,337 | 1.65 | — | 3.7 GB |
| `vulkan-64k-q8` | 116.9 | 5,196 | 1.67 | — | 3.9 GB |
| `vulkan-dspark7` | 78.1 | 4,673 | 1.93 | 25.4% | 5.1 GB |
| `cuda-base` | 120.6 | 7,181 | **1.23** | — | 3.8 GB |
| `cuda-64k-q8` | 100.3 | 6,881 | 1.29 | — | 4.0 GB |
| `cuda-dspark7` | **164.1** | 5,639 | 1.60 | 29.4% | 5.3 GB |

CPU, 4 threads:

| Configuration | Generation tok/s | Prompt tok/s | TTFT s | Acceptance | Wall, 2 turns |
| --- | --- | --- | --- | --- | --- |
| `cpu4-base` | 6.8 | **53.5** | 163 | — | 472 s |
| `cpu4-64k-q8` | 7.4 | **24.5** | 329 (slowest 395) | — | **811 s** |
| `cpu4-dspark7` | 6.5 | **61.7** | 141 | 36.0% | 365 s |

Two things stand out, and both are in [`results/issues.md`](results/issues.md):

- **The CPU regression the candidate was warned about reproduces here, at about
  half the size.** Prompt processing falls from 53.5 to 24.5 tok/s (46% of the
  32k/16-bit number) and time to first token from 163 s to 329 s. On the Xeon
  the same comparison was 48.5 to 12.7 (26%). The GPU shows no such drop.
- **DSpark removes that regression on the CPU.** `cpu4-dspark7` reads prompts at
  61.7 tok/s, higher than `cpu4-base`, where `cpu4-64k-q8` manages 24.5; its two
  turns finish in 365 s against 811 s. Generation is unchanged (6.5 against 6.8).
  On Vulkan the same configuration is a *loss* (78.1 against 113.0 generation,
  the lowest acceptance of the three at 25.4%), and on CUDA a gain.

### Quality

The 30 questions through the product's own API, one fresh session each, on the
Docker GPU route:

| Run | Core 24 passed | answered / tool / facts / language / chart | Added 6 passed | Median / p90 s |
| --- | --- | --- | --- | --- |
| `win-docker-gpu-1` | 21/24 | 24 / 24 / 22 / 24 / 22 | 1/6 (6/6/1/6) | 8.1 / 24.6 |
| `win-docker-gpu-2` | 23/24 | 24 / 24 / 24 / 24 / 23 | 2/6 (6/6/2/6) | 6.9 / 22.5 |
| `win-docker-gpu-3` | 22/24 | 24 / 24 / 23 / 24 / 23 | 2/6 (6/6/2/6) | 8.6 / 26.0 |

For comparison, the same candidate on Apple silicon measured 23 / 23 / 23 on the
core 24, and 1.5.5 on the same Mac 23 / 24. **Windows lands in the same band**;
the answer language was right in all 72 answers and the answering model never
timed out.

## The Docker CPU route: 2.5x slower than it needs to be

A question on the `local-cpu` profile was reported as taking hours on this
machine. Two configuration effects stack, and one turn of 8,706 prompt tokens
separates them:

| Variant | Context, cache | Threads | Prompt tok/s | TTFT | Generation tok/s | Wall |
| --- | --- | --- | --- | --- | --- | --- |
| **A** what the manual's checkout deploys | 65,536, `q8_0` KV, flash attention | 8 | 33.9 | **256.9 s** | 8.8 | 294 s |
| **B** the branch's CPU preset | 32,768, 16-bit KV | 8 | **72.4** | 120.3 s | 8.2 | 148 s |
| **C** B with every thread the container can see | 32,768, 16-bit KV | 16 | **83.9** | **103.8 s** | 7.3 | **135 s** |

**A → C is 2.5x on prompt processing and time to first token for this one
prompt.** The 30-question pass ran on C in **1 h 45 m**, scoring **20/24** on the
core questions with two timeouts at the 600 s ceiling and a per-answer median
of 2.0 minutes against a p90 of 8.6. No full A pass was completed, so a whole-pass
speedup was not measured. Full write-up, the mechanism for each step and the suggested fix:
[`results/cpu-docker-route.md`](results/cpu-docker-route.md).

In short:

- the manual pins `df5f62a` and then asks for the `local-cpu` profile, but
  `docker/local-models-cpu.ini` does not exist before `0b72ead`, so `llama_cpu`
  mounts the GPU preset and runs 64k with an 8-bit cache. Asking for it by name is
  worse: Docker creates a *directory* for a bind-mount source that is missing, and
  the router then restarts in a loop with
  `basic_filebuf::underflow error reading the file: Is a directory`;
- the container sees 16 CPUs and `/sys/fs/cgroup/cpu.max` imposes no quota, but
  `llama.cpp` starts 8 threads. A sweep on the same prompt and settings puts 16
  threads at 87 prompt tok/s against 71 at 8.

The C summary, metadata and log are in [`results/raw/`](results/raw/). The
median answer is **2.0 minutes** and p90 **8.6 minutes**. Of four failed core
cases, two timed out and two failed chart checks. The six added questions passed
only 1/6; these measurements are not an all-green product-quality gate.

## Scripts here

| File | What it does |
| --- | --- |
| `speed_windows.ps1` | the nine speed configurations on Windows, with time to first token |
| `speed_probe.py` | one streamed probe for native servers and the router; `PROBE_MODEL` defaults to `minicpm5-2b` |
| `cpu-route.sh` | the three CPU variants above, one case, through the Docker CPU route |
| `cpu-thread-sweep.ps1` | `llama-server -t N` on Windows for N in 4..16, same case, same settings |
| `results/` | every measurement; `results/raw/` holds the probes as they came out |

## Reproducing the CPU measurement

```bash
# inside WSL, with the Docker CPU profile up and the case file present
MIROBODY_DIR=$HOME/mirobody bash benchmarks/windows/cpu-route.sh
```

The case used here is `results/raw/cpu_case_one.json`: a single evaluation turn
whose prompt is 8,706 tokens. `cpu-route.sh` defaults to that tracked case and its sibling probe; optional
arguments name the case and output directory.

```powershell
# the thread sweep, on Windows, no Docker involved
powershell -ExecutionPolicy Bypass -File benchmarks\windows\cpu-thread-sweep.ps1 -Kit C:\mirobody-bench
```

`cpu-route.sh` restarts `llama_cpu` between variants and restores its previous
preset/running state on exit. The native thread sweep starts isolated processes
on port 18558. Both leave the app, worker and PostgreSQL data volumes intact.
