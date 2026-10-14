# Why a Docker CPU question took hours, and what removes it

Historical RC measurement (`df5f62a`, 2026-10-12/13), including local CPU
preset overrides. See [current status](../README.md) and [fresh-run commands](../RUNBOOK.md)
for 1.5.7; the raw records have not been rerun on that release.

The manual's own note says a first answer on CPU takes 2–3 minutes on a 16 GB
Apple-silicon laptop and **up to about 15 minutes on an Intel Xeon Gold 5220R**,
download included. On this machine (i5-14400F, 10 cores / 16 threads, 32 GB) a
single question was reported as taking on the order of hours. That is not the
same machine behaving normally; it is two configuration effects stacking, and
both are measurable in one turn.

Everything below is one machine, one model (`minicpm5-2b` Q8_0), one case, one
prompt of **8,706 tokens**, measured through the Docker CPU route on port 8080.
The runner is [`../cpu-route.sh`](../cpu-route.sh); the raw probes are in
[`raw/`](raw/).

## The measurement

| Variant | Context, cache | Threads | Prompt tok/s | TTFT | Generation tok/s | Wall |
| --- | --- | --- | --- | --- | --- | --- |
| **A** what the manual's checkout deploys | 65,536, `q8_0` K and V, flash attention | 8 | 33.9 | **256.9 s** | 8.8 | 294 s |
| **B** the branch's CPU preset | 32,768, 16-bit KV | 8 | **72.4** | 120.3 s | 8.2 | 148 s |
| **C** B, with every thread the container can see | 32,768, 16-bit KV | 16 | **83.9** | **103.8 s** | 7.3 | **135 s** |

Same prompt every time (8,706 tokens), same completion budget. Three separate
runs of each agreed within about 3 seconds.

**A → B is 2.1x** on prompt processing and **2.2x** on time to first token.
**A → C is 2.5x and 2.5x for prompt processing and TTFT.** Generation falls
from 8.8 to 7.3 tok/s (17%); wall time for this turn improves from 294 to 135 s
(2.2x). These are separate metrics, and none measures a whole QA-pass speedup.

## Why A is what gets deployed

`compose.yaml` on this baseline selects the CPU preset by default:

```yaml
# llama_cpu
- ${LOCAL_MODELS_PRESET:-./docker/local-models-cpu.ini}:/presets/local-models.ini:ro
```

but that file **does not exist at `df5f62a`** — it arrives in `0b72ead` ("local
models: a CPU preset, 32k with a 16-bit cache"), which is six commits later and
on `fix/local-model-harness` rather than `main`. The manual pins `df5f62a` and
then tells the reader to run the `local-cpu` profile, and at that commit
`llama_cpu` mounts the GPU preset instead:

```
$ docker compose config          # at df5f62a
source: /home/miro/mirobody/docker/local-models.ini     -> /presets/local-models.ini
```

so the CPU service comes up with the GPU settings:

```
--ctx-size 65536  --cache-type-k q8_0  --cache-type-v q8_0  --flash-attn on
```

It gets worse than a slow model. A bind mount whose source does not exist is
created by Docker **as a directory**, so asking for the CPU preset by name at
`df5f62a` produces a file-shaped directory and the router refuses to start at
all, restarting in a loop:

```
E srv llama_server: failed to initialize router models:
    basic_filebuf::underflow error reading the file: Is a directory
```

That is what happened when `LOCAL_MODELS_PRESET=./docker/local-models-cpu.ini`
was set on this checkout. The failure is silent in the sense that matters: it
looks like a preset problem, not a missing-file problem.

## Why 16 threads are available and 8 are used

The container is not short of CPU:

```
$ docker compose exec llama_cpu nproc
16
$ cat /sys/fs/cgroup/cpu.max
max 100000            # no quota
```

but llama.cpp starts 8 threads, so half the machine idles:

```
I cmn init: llama threadpool init, n_threads = 8
```

Thread count is another lever measured here. A sweep on the host with
the same prompt and the same 32k/16-bit settings, `llama-server -t N`:

| Threads | Prompt tok/s | TTFT | Generation tok/s |
| --- | --- | --- | --- |
| 4 | 47.7 | 182.5 s | 5.8 |
| 6 | 62.7 | 139.0 s | 7.7 |
| 8 | 71.3 | 122.2 s | 7.9 |
| 10 | 71.0 | 122.7 s | 8.5 |
| 16 | **87.0** | **100.1 s** | **9.3** |

4 → 16 threads is **1.8x** on prompt processing. The Docker preset change
A → B is **2.1x**, measured separately. The preset format accepts the key, and it reaches the child server:

```ini
[minicpm5-2b]
c = 32768
threads = 16
```

```
--threads 16
I cmn init: llama threadpool init, n_threads = 16
```

## What this means for a whole question

The whole 30-question pass was then run on variant C, the fixed configuration:
**1 h 45 m** for the pass (11:23:08 → 13:08:38). No full pass on A was
completed; the earlier estimates are not comparative measurements.

| | C, measured |
| --- | --- |
| Questions passed (automatic) | 20/24 |
| answered / right tool / facts / language / chart | 22 / 24 / 22 / 22 / 21 |
| Added 6 (2026-10-10) | 1/6 |
| Seconds per answer, median / p90 | **122.7 / 514.2** |
| Timeouts (600 s) | 2 |
| Made-up citation ids | 0 |

Against the same build on the GPU route (21, 23, 22 of 24 over three runs), the
CPU pass has four failed core cases: two chart failures and two timeouts.
`p002-vitd-none` and `p003-bp-chart` both hit the 600 s ceiling with nothing
answered, and each is a case that passes on the GPU.

**The median question is 2.0 minutes; the 90th percentile is 8.6 minutes.**
The single-prompt speed ratio cannot be applied to these whole-answer times:
each answer may generate different text and make a different number of calls.

## Applying the finding to 1.5.7

The CPU preset and Compose selection are already on main. Reproduce CPU runs
from a checkout containing `docker/local-models-cpu.ini`; the current
[runbook](../RUNBOOK.md) does not pin the earlier RC.

A 16-thread override improved this i5's prompt processing, but it also reduced
generation rate in the Docker comparison. That does not establish a portable
default for another processor or a container with a quota. Use the thread sweep
and the CPU experiment to choose a count for the machine being tested. The
experiment derives C's count from the container and restores its prior
configuration on exit; production presets do not acquire `threads = 16`.

## Limits of this measurement

- The A/B/C comparison is one case, one 8,706-token prompt. Absolute numbers are
  this machine's; the ratios should travel better, since they come from the same
  prompt and model.
- The 30-question pass was run on **C only**. No full A pass was
  completed; the whole-pass ratio remains unmeasured.
- `threads = 16` is the count this container can see. A container given fewer
  cores should set what it has; the measured gains do not establish a default for another machine.
- Two of the 30 hit the 600 s timeout on C. That is the harness's ceiling, not a
  crash: the turn keeps running on the server after the client gives up, which is
  why the pass took 1 h 45 m while the per-answer median is 2.0 minutes.
