# What was wrong, and what to do about it

Historical RC measurement (`df5f62a`, 2026-10-12/13), including local CPU
preset overrides. See [current status](../README.md) and [fresh-run commands](../RUNBOOK.md)
for 1.5.7; the raw records have not been rerun on that release.

Everything found while running the candidate version on Windows 11 with an
RTX 5060. Ordered by how much of the work each one blocks.

Baseline under test: `df5f62a`, image `thetahealth4mirobody/mirobody:1.5.6-rc1`,
llama.cpp b11429.

---

## 1. CUDA 12.4 cannot run on an RTX 50 series card (`sm_120`)

**Blocking: the whole GPU half of the speed matrix, and the `local` profile's
model service if it is ever pointed at a hand-downloaded build.**

An RTX 5060 is Blackwell. `nvidia-smi --query-gpu=compute_cap` reports **12.0**,
and CUDA 12.4 ships **no `sm_120` kernels**. The instruction to download
`llama-b11429-bin-win-cuda-12.4-x64.zip` therefore produces a binary that cannot
run on the card at all.

b11429 publishes a `cuda-13.4` Windows build beside the 12.4 one, and that is
what works here:

```
llama-cli --list-devices
  CUDA0: NVIDIA GeForce RTX 5060 (8150 MiB, 7020 MiB free)
  Vulkan0: NVIDIA GeForce RTX 5060 (8110 MiB, 7338 MiB free)
```

**What changed here:** `speed_windows.ps1` reads the compute capability and
prefers `llama-cuda-13.4` when it is 12.0 or newer, falling back to any
`llama-cuda*` directory that holds a server:

```
#### cuda build C:\mirobody-bench\llama-cuda\llama-server.exe  (sm_12, wanted llama-cuda-13.4)
```

**Suggested for the repository:** whatever documents the CUDA download should
name the build by the GPU's compute capability, not by a fixed CUDA version —
CUDA 12.4 is right for Ada (`sm_89`) and wrong for Blackwell, and RTX 50 cards
are what a new Windows machine is likely to have.

---

## 2. The system HTTP proxy carries loopback traffic, and kills long requests

**Blocking: every CPU speed configuration, and any harness script that talks to
`127.0.0.1`.**

This is the most costly problem here, because it does not look like a
configuration mistake. It looks like the model server failing.

Windows has a system proxy configured (`localhost:15236`). WSL 2 **inherits
`http_proxy`/`https_proxy` from it**, so inside the distro:

```
https_proxy=http://localhost:15236
HTTPS_PROXY=http://localhost:15236
HTTP_PROXY=http://localhost:15236
http_proxy=http://localhost:15236
```

Both `urllib` and `requests` honour those variables. So a request to
`127.0.0.1:18557` or `localhost:18060` is handed to the proxy — and for that
proxy "localhost" is **Windows**, not the Linux side or a container. Three
different symptoms, one cause:

| What was asked | What happened |
| --- | --- |
| `speed_probe.py` → `127.0.0.1:18557`, a CPU turn | `[WinError 10054]` after ~268 s (sometimes HTTP 503). `llama-server` was healthy throughout: it finished the prompt at 51 tok/s, kept generating at 6.2 tok/s, and logged `cancel task` only after the client disappeared. GPU turns finished in seconds, so only the CPU half of the matrix failed. |
| `curl localhost:18060/api/health` | **HTTP 503** with a `Proxy-Connection: close` header — the proxy answering, not the app. The app's own log said `model local ready`. |
| `curl localhost:18060/mirobody.json` | Empty body, so `__MODEL_SETUP__` could not be read at all. |

Setting `NO_PROXY=127.0.0.1,localhost,llama,pg` (or using `curl --noproxy '*'`,
or `http.client`, which ignores the variables) makes all three behave:

```
No proxy:  /api/health -> 200 {"service":"mirobody","version":"1.5.6rc1",...}
                                                       "__MODEL_SETUP__":"ready"
```

The connection reset at ~268 s is the proxy's own idle/response limit; the CPU
turns are the only ones long enough to reach it.

**What changed here:** `speed_probe.py` streams over `http.client` instead of
`urllib`, which both avoids the proxy and makes time-to-first-token measurable.

**Current resolution:** `local_models/common.py::_local_without_proxy` adds the
app/router's local hosts to both `NO_PROXY` and `no_proxy`. This is already on
main; external download hosts continue to use the configured proxy. The shared
speed probe uses a direct `http.client` connection, and WSL preflights explicitly
bypass proxies.

---

## 3. `LOCAL_BASE_URL` in `.env` blocks the model switch the evaluation needs

**Blocking: `run_quality.sh` ends every run in about two seconds.**

`deploy.sh` with `COMPOSE_PROFILES=local` (or `local-cpu`) writes the local
endpoint into `.env`:

```
LOCAL_BASE_URL=http://llama:8080/v1
LOCAL_OCR_BASE_URL=http://llama:8080/v1
```

`run.py` switches the stack to a size through `POST /api/setup`. The setup API
refuses, because a value that was already in the environment when the app
started is treated as *fixed* (`mirobody/utils/config/settings.py`,
`set_in_environment`) and `setup_router.py` answers:

```python
if "LOCAL_BASE_URL" in fixed:
    return err(409, "LOCAL_BASE_URL is set in .env; change it there.")
```

which surfaces as:

```
setup refused minicpm5-2b: LOCAL_BASE_URL is set in .env; change it there.
[00:04:08] win-docker-gpu-1 exit 1     # and 2, and 3, each within two seconds
```

**What worked here:** remove those two lines from `.env` and recreate the app
containers (`docker compose up -d --force-recreate mirobody mirobody_worker`).
The saved settings live in the database, so nothing is lost; the app then reports
`__MODEL_SETUP__: needed` and `run.py` configures it through the API as the
harness expects. All runs after that passed.

**Current resolution:** main allows a signed-in, trusted setup request to change
models at the **same pinned endpoint**. Another endpoint remains refused; the
setup token does not authorize overriding environment-pinned destinations.
Deleting `LOCAL_BASE_URL` was a workaround for the RC, not a requirement for
1.5.7. The WSL runner now checks readiness before a new document load, and
checks completed loader state before reusing accounts.

---

## 4. `uv` installed by `winget` is not on the `PATH` of the shell that runs the script

**Blocking: silently skipped every one of the six GPU configurations.**

`winget install --id astral-sh.uv` puts the binary in

```
%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe
```

and prints *"Path environment variable modified; restart your shell to use the
new value"*. A script that calls `uv run` fails with
`CommandNotFoundException`, and because the call sits in a pipeline the matrix
walks every configuration in about forty seconds without recording a single
number — it looks like the benchmark ran.

**What changed here:** `speed_windows.ps1` resolves `uv` explicitly (WinGet
links directory, then `~/.local/bin`, then `%LOCALAPPDATA%\Microsoft\WinGet\Packages`,
then `PATH`) and says so in the output, so a missing `uv` is visible instead of
silent.

---

## 5. Docker Desktop integrates no WSL distribution until it is asked to

**Blocking: `docker` does not exist inside the distro, which is where every
harness script runs.**

After installing Docker Desktop, `settings-store.json` contained no
`IntegratedWslDistros` key at all — the setting is absent by default and absent
means *no distro integrated*. `wsl -l -v` then lists only the distribution you
installed, no `docker-desktop`, and inside it:

```
$ docker version
/bin/bash: line 1: docker: command not found
```

Two things were needed:

1. **Enable the integration.** Rather than the GUI, this was written into
   `%APPDATA%\Docker\settings-store.json` (with Docker Desktop stopped) and the
   application restarted:
   ```json
   "IntegratedWslDistros": ["Ubuntu-24.04"],
   "EnableIntegrationWithDefaultWslDistro": true
   ```
   After that `/usr/bin/docker` is a symlink to
   `/mnt/wsl/docker-desktop/cli-tools/usr/bin/docker`.
2. **Join the `docker` group.** The socket is `srw-rw---- root docker`, and the
   Linux user created by the distro's first-run wizard is not in it:
   ```
   permission denied while trying to connect to the Docker API at unix:///var/run/docker.sock
   ```
   `usermod -aG docker <user>` plus `wsl --shutdown` fixes it.

Also worth knowing: **the installer is machine-wide and needs elevation, but
4.95.0 installs into the user profile**
(`%LOCALAPPDATA%\Programs\DockerDesktop`) and the engine starts without an
administrator prompt — so a session without administrator rights can run the
whole stack, once WSL itself has been installed. `AutoStart` stays `false` by
default, which is convenient here: the engine does not come up on its own and
compete for the GPU with a speed run.

---

## 6. The CPU regression the candidate was warned about, measured

Not a defect in the code, but the number the experiment was meant to produce.

4 threads, the same two turns, `small` = `minicpm5-2b` Q8_0:

| | 32k, 16-bit KV | 64k, 8-bit KV, FA | |
| --- | --- | --- | --- |
| Prompt processing | 53.5 tok/s | **24.5 tok/s** | 46% of base |
| Time to first token | 163 s | **329 s** (slowest 395) | 2.0x |
| Wall, two turns | 472 s | **811 s** | 1.7x |
| Generation | 6.8 tok/s | 7.4 tok/s | unchanged |

So the direction the candidate's own note describes ("it read a prompt at 13
tokens/s against 48") reproduces on an i5-14400F, at roughly **half** the size:
48.5 → 12.7 was 26% on the Xeon, 53.5 → 24.5 is 46% here. The GPU shows no
comparable drop (5,337 → 5,196 prompt tok/s, −2.6%).

The fix that is already on this branch — `docker/local-models-cpu.ini`, 32k with
a 16-bit cache — is exactly the `cpu4-base` column above, so the numbers here
confirm the choice.

**The same regression through the Docker route is worse than these numbers
suggest, because the deployed checkout does not get that fix at all.** On
`df5f62a` the preset file does not exist yet, `llama_cpu` falls back to the GPU
preset, and a single question was still running after seven minutes. Measured
there: 33.9 prompt tok/s against 83.9 with the preset and all threads, and a
30-question pass on C in 1 h 45 m. No full pass on A was completed. The mechanism, the
A/B/C numbers and the two suggested changes are in
[`cpu-docker-route.md`](cpu-docker-route.md); the pass itself is in
[`E3-quality.md`](E3-quality.md).

---

## 7. DSpark removes the CPU regression, and costs on Vulkan

A finding rather than a defect, and worth a second look by whoever owns the
speculative path.

| | base | 64k-q8 | **dspark7** |
| --- | --- | --- | --- |
| CPU prompt tok/s | 53.5 | 24.5 | **61.7** |
| CPU TTFT | 163 s | 329 s | **141 s** |
| CPU wall, 2 turns | 472 s | 811 s | **365 s** |
| CPU acceptance | — | — | 36.0% |

On the CPU, DSpark is **faster than the base configuration at prompt
processing** and nearly halves the worst-case wall time, while generation is
unchanged (6.5 against 6.8 tok/s). The logs show the draft model being loaded
first and taking the prompt pass, which is consistent with the main model's
prefill being relieved — but that is an inference from the log, **not a
verified mechanism**, and it is the thing to check.

On the GPU the same configuration splits by backend:

| Backend | base gen | dspark7 gen | |
| --- | --- | --- | --- |
| CUDA | 120.6 | **164.1** | +36% |
| Vulkan | 113.0 | 78.1 | **−31%** |

Vulkan also has the lowest acceptance of the three backends (25.4% against 29.4%
on CUDA, 36.0% on the CPU). If DSpark is to be offered on the CPU profile — where
it is the only configuration measured here that avoids the 64k/8-bit regression
— the Vulkan loss is worth understanding first.
