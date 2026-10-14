# The native `llama-server.exe` route on Windows

Historical RC measurement (`df5f62a`, 2026-10-12/13), including local CPU
preset overrides. See [current status](../README.md) and [fresh-run commands](../RUNBOOK.md)
for 1.5.7; the raw records have not been rerun on that release.

The manual describes this route and notes that the repository had never run it on
Windows. The recorded run verified discovery and direct completions; the 30-question
app evaluation was not repeated on this route.

Baseline: `df5f62a`; llama.cpp b11429, **Vulkan** build
(`llama-b11429-bin-win-vulkan-x64.zip`); Docker Desktop 4.95.0 for the app;
NVIDIA driver 591.86.

---

## The question the manual most wants answered

> **Does `host.docker.internal` reach a Windows service bound to `127.0.0.1`?**

**Yes.** Nothing else was needed: no `--host 0.0.0.0`, no firewall rule.

```
# inside the app container
getent hosts host.docker.internal              -> fdc4:f303:9324::254 host.docker.internal
curl http://host.docker.internal:8080/health   -> HTTP 200
curl http://host.docker.internal:8080/models   -> glm-ocr, minicpm5-2b,
                                                  paddleocr-vl, qwen3.8-27b

# the app's own probe of its candidates
GET /api/setup/local
  base_url = http://host.docker.internal:8080/v1
  served:   glm-ocr, minicpm5-2b, paddleocr-vl, qwen3.8-27b
```

`llama-server.exe` was bound to `127.0.0.1:8080`, exactly as the manual says, and
the container reached it. The app's candidate list is

```
['http://host.docker.internal:8080/v1', 'http://llama:8080/v1', 'http://127.0.0.1:8080/v1']
```

with `host.docker.internal` **first**, so the setup page finds the native server
by itself — the reading the manual asks for, and the "restart it with
`--host 0.0.0.0`" fallback was not needed.

## The server, and one setting it needs

```
C:\mirobody-bench\llama-vulkan\llama-server.exe --models-preset docker\local-models.ini --port 8080 --models-max 2
```

```
srv  operator(): Available models (4):
srv  operator():   [    preset] glm-ocr
srv  operator():   [    preset] minicpm5-2b
srv  operator():   [    preset] paddleocr-vl
srv  operator():   [    preset] qwen3.8-27b
srv  operator(): starting server in router mode. models will be automatically loaded on-demand
srv  llama_server: listening on http://127.0.0.1:8080
```

`--list-devices` on the same build:

```
Vulkan0: NVIDIA GeForce RTX 5060 (8110 MiB, 7338 MiB free)
```

**It downloads its own weights, and it does not use the machine's proxy.** The
container route reaches Hugging Face through the Docker engine's proxy settings;
this process does not, so on a network where `huggingface.co` is only reachable
through a proxy it sat at roughly 195 KB/s — hours for the 2.7 GB Q8_0. Setting
`HF_ENDPOINT` for that process fixed it:

```powershell
$env:HF_ENDPOINT = "https://hf-mirror.com"      # network mirror used in this run
C:\mirobody-bench\llama-vulkan\llama-server.exe --models-preset docker\local-models.ini --port 8080 --models-max 2
```

Worth a line beside the existing advice about `HF_ENDPOINT` in `.env` (which
covers the container and not this process).

## Two practical notes

**Start it from the repository root.** The preset's
`chat-template-file = docker/minicpm5-2b.jinja` is a relative path and is passed
through verbatim:

```
"--chat-template-file", "docker/minicpm5-2b.jinja"
```

so the process's working directory has to be the checkout root. Starting it from
`\\wsl.localhost\…` — the manual's `cd` step — was **refused for the working
directory**, because Windows will not set a process's current directory to that
UNC path:

> `Start-Process : This is not a valid working directory …`

The route still works from a Windows-side copy of the same branch, which is what
was done here, and the relative path resolves the same way. Worth a line in the
manual, since the `cd \\wsl.localhost\…` step reads as required when it is only
one way to put the preset in front of the binary.

**The Docker model service must be stopped first.** Both routes want
`127.0.0.1:8080`:

```bash
docker compose --profile local --profile local-cpu stop llama llama_cpu
```

The app, the worker and Postgres stay up, so accounts and data are untouched
across the switch.

## Verified discovery and direct completions

With `minicpm5-2b` loaded on the native Vulkan server (2.8 GB working set), a
real completion in each language through `/v1/chat/completions`:

| Prompt | Generation | Answer |
| --- | --- | --- |
| 用一句话说明你是什么模型。 | 103.9 tok/s | "我是 MiniCPM 系列模型，由面壁智能和 OpenBMB 开源社区开发…" |
| In one sentence, what model are you? | 120.6 tok/s | "I am a model from the MiniCPM series, developed by ModelBest Inc. and…" |

The completions show the model answering in both languages. Its self-description
does not prove which template was applied; the recorded startup arguments name
`docker/minicpm5-2b.jinja`.

For reference, the same model on the same card in the native speed matrix measured
113.0 tok/s generation on Vulkan and 120.6 on CUDA
([`speed/summary.md`](speed/summary.md)); the native Vulkan process at 103.9–123.5
tok/s is the same machine, the same model and the same backend, so the two routes
perform alike.

## What was *not* done

The app was pointed at the native server and its own discovery confirmed it, but
the 30 questions were **not** re-run on this route: that is the same
`run_quality.sh native` run the manual lists, and the Docker route already
produced all three GPU passes. The pieces it depends on are all verified above —
the app finds the models, the models load, and completions come back correct.
