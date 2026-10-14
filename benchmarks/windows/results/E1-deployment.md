# E1: does the deployment manual work on Windows?

Historical RC measurement (`df5f62a`, 2026-10-12/13), including local CPU
preset overrides. See [current status](../README.md) and [fresh-run commands](../RUNBOOK.md)
for 1.5.7; the raw records have not been rerun on that release.

The hand-off manual describes two routes to a running stack: the product's
default one, models in Docker, and a native `llama-server.exe` on Windows. Both
were to be tried, and one of them the repository had never run on Windows.

Result in short: **the Docker route works end to end on Windows 11 with an
RTX 5060**, including GPU passthrough into the container, and it passed the 30
questions. The native route is described at the end.

Baseline: `df5f62a`, image `thetahealth4mirobody/mirobody:1.5.6-rc1`,
Docker Desktop 4.95.0 (engine 29.8.2, Linux containers, WSL 2 backend,
Ubuntu 24.04), driver 591.86.

---

## The Docker route (the product default) — works

### 1. The image exists

`thetahealth4mirobody/mirobody:1.5.6-rc1` is published, so the preferred option
of naming the candidate image in `.env` is the one that applies; no local build
was needed.

```bash
echo 'MIROBODY_IMAGE=thetahealth4mirobody/mirobody:1.5.6-rc1' >> .env
cp /mnt/c/mirobody-bench/wsl/compose.override.yaml .
COMPOSE_PROFILES=local ./deploy.sh
```

`deploy.sh` generated the five secrets, wrote `COMPOSE_PROFILES`,
`LOCAL_BASE_URL` and `LOCAL_OCR_BASE_URL`, pulled the images and brought the
stack up. All four pulls the daemon needs succeeded, including both
`ghcr.io/ggml-org/llama.cpp` images — worth recording, because `ghcr.io` is not
always reachable from this network and there is a fallback path for that
(`LLAMA_IMAGE`, and a `DOCKER_MIRROR` for Docker Hub).

### 2. The running services

```
NAME                         SERVICE           STATUS
mirobody-llama-1             llama             Up (healthy)   127.0.0.1:8080->8080/tcp
mirobody-mirobody-1          mirobody          Up (healthy)   127.0.0.1:18060->18060/tcp
mirobody-mirobody_worker-1   mirobody_worker   Up             18060/tcp
mirobody-pg-1                pg                Up (healthy)   127.0.0.1:18062->5432/tcp
```

**A trap worth a line in the manual:** `curl -s http://localhost:18060/api/health`
answers **503** on this machine even though the app is healthy, because the
system HTTP proxy is handed the request and *its* localhost is Windows, not the
container. The app's own log at that moment says `model local ready: model
llm_type=openai model_name=minicpm5-2b thinking_level=medium`. With the proxy
out of the way the same call answers:

```json
{"service":"mirobody","version":"1.5.6rc1","tools":9,"public_tools":3,
 "authenticated_tools":6,"agent":"Mirobody"}
```

See [`issues.md`](issues.md) §2; it cost more time than anything else here.

### 3. The model service reports all four models

```
GET http://127.0.0.1:8080/models
  - glm-ocr
  - minicpm5-2b
  - paddleocr-vl
  - qwen3.8-27b
```

The service runs llama.cpp's router with `--models-max 2`, so nothing is loaded
until it is asked for; `minicpm5-2b` is 2.7 GB and is downloaded on first use.

### 4. GPU passthrough into the container — works

Inside the `llama` container:

```
$ nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv
NVIDIA GeForce RTX 5060, 1557 MiB, 8151 MiB
```

And the manual's own check from §2.3, the CUDA sample, passes with the card
named and a number:

```
Compute 12.0 CUDA device: [NVIDIA GeForce RTX 5060]
30720 bodies, total time for 10 iterations: 16.953 ms
= 556.674 billion interactions per second
= 11133.481 single-precision GFLOP/s at 20 flops per interaction
```

### 5. Documents must be loaded with a model already chosen

The manual warns about this and it is real: a document uploaded while the app
has no model fails, and the record is then missing that document's readings, so
every question about it fails too. The load was run only after the app had a
model, and `load-verify-win1.txt` shows the documents arriving intact — for
example `p002_2026-08-07_e04a.pdf: 78/78 rows, unit 78, range 77` and
`p005_2025-08-28_e05a.pdf: 50/52 rows, unit 50, range 49`.

### 6. And then it must be *un*fixed for the runs

Following the manual literally — `deploy.sh` with `local`, then
`run_quality.sh` — ends every run in two seconds, because `deploy.sh` fixes
`LOCAL_BASE_URL` in `.env` and the setup API then refuses the model switch
`run.py` needs. Removing those two lines from `.env` and recreating the app
containers is what makes the sequence work. Full detail in
[`issues.md`](issues.md) §3; the accounts and their data are untouched by this
because saved settings live in the database.

### 7. What the evaluation needs from the daemon

`run_quality.sh` and `run.py` use the `docker` CLI against the same daemon, and
the Linux user has to be in the `docker` group for that (see
[`issues.md`](issues.md) §5). Everything else — `docker compose`, `docker
stats`, `docker compose logs` — worked as written.

---

## The native `llama-server.exe` route

The manual flags this one as never having been run on Windows, so it was
attempted as its own result. The relevant facts established first:

- The Vulkan build of b11429 enumerates the card:
  `Vulkan0: NVIDIA GeForce RTX 5060 (8110 MiB, 7338 MiB free)`. So the
  GPU-acceleration half of the route is sound on this hardware.
- `docker/local-models.ini` exists on this baseline and names relative paths
  (`docker/minicpm5-2b.jinja`), so `llama-server` has to be started **from the
  repository root** for the preset to resolve its chat template — which requires a Windows-drive checkout root in the tested launcher.
  The manual's UNC working directory was refused; see `native-route.md`.
- The app reaches it from inside a container as `host.docker.internal:8080`, and
  `compose.yaml` already passes `host.docker.internal` in `NO_PROXY` and maps it
  in `extra_hosts`, so the routing itself is configured for this route.

Whether the setup page actually finds the native server — that is, whether
`host.docker.internal` resolves to a Windows service bound to `127.0.0.1` —
is recorded in [`native-route.md`](native-route.md).
