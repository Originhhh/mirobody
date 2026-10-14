# E3: the 30 questions on Windows

Historical RC measurement (`df5f62a`, 2026-10-12/13), including local CPU
preset overrides. See [current status](../README.md) and [fresh-run commands](../RUNBOOK.md)
for 1.5.7; the raw records have not been rerun on that release.

The same 30 cases, the same seed 7 accounts and the same automatic checks as
[`../../local_models`](../../local_models), run through the product's own HTTP
API against the Docker route on this machine. One fresh chat session per
question; the answering model is `minicpm5-2b` (small) and `glm-ocr` reads
documents.

Baseline: `df5f62a`, image `thetahealth4mirobody/mirobody:1.5.6-rc1`.

Method note: the four accounts were loaded **once**, before any run, while the
app already had a model — the failure the manual warns about ("No vision model
available") did not happen, and `load-verify-win1.txt` shows the documents
arriving with their printed rows. The runs then reuse those accounts.

---

## Docker GPU route — 3 runs

| Run | Core 24 passed | answered / tool / facts / language / chart |
| --- | --- | --- |
| `win-docker-gpu-1` | **21/24** | 24 / 24 / 22 / 24 / 22 |
| `win-docker-gpu-2` | **23/24** | 24 / 24 / 24 / 24 / 23 |
| `win-docker-gpu-3` | **22/24** | 24 / 24 / 23 / 24 / 23 |

| Run | Added 6 (2026-10-10) | Cited answers in the format | Made-up ids | Median / p90 s | Timeouts |
| --- | --- | --- | --- | --- | --- |
| 1 | 1/6 (answered/tool/facts/language 6/6/1/6) | 3/24 | 0 | 8.1 / 24.6 | 0 |
| 2 | 2/6 (6/6/2/6) | 5/23 | 0 | 6.9 / 22.5 | 0 |
| 3 | 2/6 (6/6/2/6) | 4/22 | 0 | 8.6 / 26.0 | 0 |

### Against the reference numbers

Same candidate version, same cases, same data, 24 core questions:

| Machine | Runs |
| --- | --- |
| Mac, Apple silicon 48 GB, Metal, **candidate** | 23 / 23 / 23 |
| Mac, 1.5.5 (32k, 16-bit KV) | 23 / 24 |
| **Windows 11, RTX 5060, Docker (this)** | **21 / 23 / 22** |

**Windows lands in the same band.** Two more things hold across all three runs
and all 72 answers:

- **Language: 24/24 every run.** Every answer came back in the language of its
  question, including the English questions about Chinese records and the other
  way round.
- **No timeouts.** Nothing hit the 600 s ceiling, and the answering model never
  failed to answer.
- **No made-up citation ids** in any run (0 of 69 formatted answers). The
  "numbers in no tool result" line (32, 25, 33 across the runs) counts values
  the answer computed or restated — a mean, a difference, a chart axis — not
  invented data, and `score.py` reports it separately for that reason.

### Which cases are unstable

Across three runs, the core chart/sleep cases and added cases vary as follows:

| Case | R1 | R2 | R3 | Failed check |
| --- | --- | --- | --- | --- |
| `p005-weight-chart` | no | no | no | chart (facts 4/6 in R1, 6/6 in R2 and R3) |
| `p002-sleep-july` | no | yes | no | facts |
| `p002-steps-chart` | no | yes | yes | chart |
| `p002-alt-knowledge` | no | no | no | facts |
| `p003-metformin-label` | no | no | no | facts |
| `p005-hgb-meaning` | no | no | no | facts |
| `p003-a1c-target` | no | yes | yes | facts |
| `p005-ferritin-source` | no | no | no | facts |

Model behaviour, not a Windows problem: the four medical-knowledge cases
(`p002-alt-knowledge`, `p003-metformin-label`, `p005-hgb-meaning`,
`p005-ferritin-source`) fail the `facts` check in all three runs, exactly as they
do in the Mac record for the same harness — the failing-check column of
`../local_models/results/2026-10-10/small-base/summary.md` lists the same four.
`p005-weight-chart` is the only core case that never passes here while it failed
on the Mac too.

---

## Docker CPU route — 1 run

The manual asks for a single run on CPU, since a full pass is hours.

**First attempt, on the settings the manual's checkout deploys: stopped.** The
CPU model service came up and `win-docker-cpu-1` began, but the first question
was still running after seven minutes, which prompted a **5–10 hour estimate**, not a completed measurement. That is not the model being slow on a CPU; it is the
wrong preset, and it is measured in
[`cpu-docker-route.md`](cpu-docker-route.md): `df5f62a` predates
`docker/local-models-cpu.ini`, so `llama_cpu` falls back to the GPU preset and
reads prompts at 34 prompt tok/s against 84 with the CPU preset and all 16
threads.

**Second attempt, on the fixed settings: complete.**

| | |
| --- | --- |
| Pass | `win-docker-cpu-1`, 11:23:08 → 13:08:38, **1 h 45 m** |
| Core 24 passed | **20/24** |
| answered / right tool / facts / language / chart | 22 / 24 / 22 / 22 / 21 |
| Added 6 (2026-10-10) | 1/6 |
| Seconds per answer, median / p90 | **122.7 / 514.2** |
| Timeouts (600 s) | 2 |
| Made-up citation ids | 0 |

Against the same build on the GPU route (21, 23 and 22 of 24 over three runs),
the CPU pass has four failed core cases: two chart failures and two timeouts.
`p002-vitd-none` and `p003-bp-chart` both hit the 600 s ceiling with nothing
answered, and each passes on the GPU.

**The median answer is 2.0 minutes and the p90 is 8.6**. The single-prompt
A/B/C experiment does not establish a 2.5x multiplier for whole answers:
generation rates, output lengths and tool-call counts differ. No full A pass
was completed. The summary, metadata and log for C are in [`raw/`](raw/).

---

## What was *not* measured

- **Extraction and journal parts.** The runs were `--parts qa`, as the manual's
  command line asks. The `Extraction` and `Journal` rows in each `summary.md`
  are therefore `0/0`; that is the harness reporting an unrun part, not a
  failure. The Mac records cover those parts.
- **Hand grading.** `grades.json` is Claude Code's rubric grading; only the
  automatic checks were run here.
- **The `large` size.** `qwen3.8-27b` wants about 20.8 GB of memory in the
  tier table and this machine has 8 GB of VRAM, so only `small` was measured.
- **A CPU pass on the settings the manual deploys.** It was stopped after the
  first question passed seven minutes (see above); the initial 5–10 hour estimate was not verified. The pass reported above ran on the fixed settings and took 1 h 45 m. The
  comparison that matters, 2.5x, is measured on a single turn in
  [`cpu-docker-route.md`](cpu-docker-route.md).
