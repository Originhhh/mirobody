# Speed: MiniCPM5-2B Q8_0 on Windows, RTX 5060 and 4 CPU threads

Raw output is in [`speed_windows.out`](speed_windows.out); every turn's text,
timing and token count is in the `speed_cases*.<config>.out.json` files beside
it, and each `llama-server`'s own log is in `<config>.log`.
[`analyze_speed.py`](analyze_speed.py) rebuilds the tables below
(`SPEED_DIR=… python analyze_speed.py`; without it, the tracked results here).

## Method

Each configuration was measured on its own fresh `llama-server` on port 18557
(`-np 2 --kv-unified --cache-ram 1024`), with the same fixed agent turns the Mac
and Linux runs use: `temperature 0`, `max_tokens 700`, thinking enabled,
`cache_prompt false`. Three turns per GPU configuration and two per CPU
configuration (`speed_cases_cpu.json`), streamed so that **time to first
token** could be measured as well as llama-server's own prompt-processing and
generation rates.

TTFT is reported twice — measured off the stream, and derived from the server's
timings as `prompt_ms + 1/generation rate`, which is what a non-streaming client
waits for. The two agree within 10–150 ms in every row, which is the check that
the streamed measurement is sound.

The machine was otherwise idle. `cpu4-*` uses `-t 4` to stand in for Docker's
4 cores; the machine has 16 logical cores, so the other 12 were free.

## GPU (RTX 5060, 8 GB, `sm_120`)

| Configuration | Generation tok/s | Prompt tok/s | TTFT s (fastest) | TTFT s (slowest) | Acceptance | Working set | VRAM |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `vulkan-base` | 113.0 | 5,337 | 1.65 | 1.82 | — | 2,755 MB | 3,729 MB |
| `vulkan-64k-q8` | 116.9 | 5,196 | 1.67 | 1.76 | — | 2,790 MB | 3,861 MB |
| `vulkan-dspark7` | 78.1 | 4,673 | 1.93 | 1.95 | 25.4% | 3,618 MB | 5,067 MB |
| `cuda-base` | 120.6 | 7,181 | **1.23** | 1.26 | — | 2,787 MB | 3,846 MB |
| `cuda-64k-q8` | 100.3 | 6,881 | 1.29 | 1.32 | — | 2,831 MB | 3,998 MB |
| `cuda-dspark7` | **164.1** | 5,639 | 1.60 | 1.65 | 29.4% | 3,805 MB | 5,270 MB |

## CPU (4 threads)

| Configuration | Generation tok/s | Prompt tok/s | TTFT s (fastest) | TTFT s (slowest) | Acceptance | Wall, 2 turns |
| --- | --- | --- | --- | --- | --- | --- |
| `cpu4-base` | 6.8 | **53.5** | 163.0 | 168.1 | — | 472 s |
| `cpu4-64k-q8` | 7.4 | **24.5** | **329.3** | **395.4** | — | **811 s** |
| `cpu4-dspark7` | 6.5 | **61.7** | 141.0 | 145.9 | 36.0% | 365 s |

## Reading the numbers

**The candidate's own warning reproduces on the CPU, at about half the size.**
32k with a 16-bit cache reads a prompt at 53.5 tok/s; 64k with an 8-bit cache
and flash attention reads the same kind of prompt at 24.5, and the first token
takes 2.0x as long (163 s → 329 s). Generation is unaffected (6.8 → 7.4 tok/s),
so the prompt pass is the whole of it. The candidate's note records 48.5 → 12.7
on a Xeon, which is 26% of base against 46% here — same direction, smaller.

**On the GPU there is no such drop.** 5,337 → 5,196 prompt tok/s (−2.6%) and
generation actually rises, 113.0 → 116.9. An 8 GB RTX 5060 runs the candidate's
64k/8-bit setting comfortably at 1.7 s to the first token and under 4 GB of VRAM.

**CUDA wins on the base configuration here**: prompt processing 5,337 → 7,181
(+35%), generation 113.0 → 120.6 (+6.7%), and fastest TTFT 1.65 s → 1.23 s.
At 64k/Q8 KV, Vulkan generates faster (116.9 versus CUDA's 100.3 tok/s), while
CUDA still has faster prompt processing and TTFT. The result depends on the
configuration, not just the backend.

**DSpark splits by backend.** On CUDA it is the fastest configuration measured
(164.1 tok/s generation, +36% over base). On Vulkan it is the slowest (78.1,
−31%) with the lowest acceptance of the three backends (25.4%). On the CPU it
does something more interesting than either: it reads prompts at 61.7 tok/s,
*higher than the base configuration*, and finishes the two turns in 365 s
against base's 472 s and 64k-q8's 811 s. Generation is unchanged. See
[`../issues.md`](../issues.md) §7 for why that is worth a second
look — the claim that the draft model absorbs the prompt pass comes from the
server log, not from a verified mechanism.

## Reproducing

Hardware-dependent, so treat these as one machine's numbers:

```
i5-14400F (10C/16T), 32 GB, Windows 11 Pro 26200
NVIDIA GeForce RTX 5060, 8151 MiB, sm_120, driver 591.86
llama.cpp b11429 (commit d81235049)
MiniCPM5-2B-Q8_0.gguf 2,679,710,688 bytes
MiniCPM5-2.6B-DSpark.gguf 652,730,240 bytes
```

```powershell
cd C:\src\mirobody
powershell -ExecutionPolicy Bypass -File benchmarks\windows\speed_windows.ps1 -Kit C:\mirobody-bench                 # all
powershell -ExecutionPolicy Bypass -File benchmarks\windows\speed_windows.ps1 -Kit C:\mirobody-bench -Only gpu      # GPU only
$env:ONLY="cuda-64k-q8"; powershell -ExecutionPolicy Bypass -File benchmarks\windows\speed_windows.ps1 -Kit C:\mirobody-bench
```
