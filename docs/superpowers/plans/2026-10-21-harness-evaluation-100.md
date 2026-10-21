# Thinking presentation and 100-question evaluation implementation plan

[中文版](2026-10-21-harness-evaluation-100.zh-CN.md)

> Execute inline with the executing-plans workflow. The user's 2026-10-21 instructions authorize implementation, require 100 questions, retain local genetics and OCR-assisted photos, and count the first visible reasoning token as TTFT.

**Goal:** Keep intermediate work out of final answers and make the local-model evaluation a reproducible, diverse 100-question product regression.

**Architecture:** Keep the one MirobodyAgent and existing model/tool block vocabulary; add content-free `context` activity for actual summary generation. A turn-owned stream renderer immediately forwards native reasoning and classifies ordinary text when its model step completes. Tool steps emit their narration into reasoning; only a completed answer emits text. Build questions and loading plans from a pinned mirobody-gen corpus, never from product answers. Measure first visible token, first reasoning, first tool activity and first answer separately.

**Tech stack:** Python, LangGraph/deepagents, the existing HTTP benchmark, mirobody-gen 9723605, pytest.

## Constraints

- Initial base: `d8463a4a`, including PRs 159, 166 and 167. Before PR submission, rebased onto `origin/main` `d4468653`, including PR 168.
- No second harness, no new public MCP tool, no real health records in fixtures.
- Native reasoning is requested where the model endpoint supports it. A tool event is real work, not fabricated model reasoning; missing native reasoning remains observable.
- Keep OCR/extraction utility models independent of chat reasoning policy.
- No interim prose in final text or future speech input. Reasoning and tool events remain separate typed blocks for the thinking panel.
- Unknown generation audits remain unknown. Preserve failed builds and historical benchmark results.
- Exactly 100 scored question rows, matching frontend locales: 60 en, 30 zh-CN, 5 ja and 5 zh-TW.
- Multi-turn questions reuse a session. One heavy long-context follow-up permits up to 16 additional, explicitly recorded source-pressure requests; it requires two actual automatic summaries before grading retention.

## Task 1: Stream classification and turn boundaries

Files: `mirobody/agent/wire/stream.py`, `mirobody/agent/agent.py`, `mirobody/agent/models/messages.py`, `mirobody/agent/python_tool.py`, `tests/agent/test_thinking_stream.py`, `tests/test_code_tool_capability.py`.

- [x] Add tests with `AIMessageChunk(content="Looking up")`, followed by a completed `AIMessage` with tool calls: expected output contains reasoning and tool_call, no text.
- [x] Add a native reasoning chunk before any model update: it must be yielded immediately.
- [x] Add final, invalid-call, cancelled, repeated-update and cross-turn fixtures; final text appears once and discarded incomplete text never becomes an answer.
- [x] Implement `TurnStream.blocks(stream_type, stream_event, trace_id)` and use one instance for each `_stream_agent_response` invocation. Keep the generic event bridge independent.
- [x] Fix `source_results` to distinguish named harness nudges from a person's new message. Test both known nudges and a true new question.
- [x] Run the affected tests and a real compiled graph with a scripted model.

## Task 2: Explicit thinking and latency accounting

Files: `mirobody/agent/models/clients.py`, `mirobody/agent/agent.py`, `mirobody/agent/prompts/mirobody.jinja`, `benchmarks/local_models/run.py`, `benchmarks/local_models/timing.py`, benchmark tests.

- [x] Test chat policy requesting reasoning with provider-specific parameters while utility construction remains unchanged.
- [x] Preserve protocol-required settings for transports that do not expose reasoning; document them and measure actual reasoning availability rather than claim they stream it.
- [x] Tell the model that planning/tool narration belongs to thinking and final content should address the person directly.
- [x] Add `StreamTiming.observe(block, elapsed)` with `first_token_s`, `first_reasoning_s`, `first_tool_s`, `first_text_s`; first_token is the first nonempty reasoning or text block. Exclude heartbeats, notices, empty chunks and usage.
- [x] Include p50/p90 and observed sample counts for each timing field in summaries. Keep missing measurements null.

## Task 3: Source-derived 100-question suite

Files: `benchmarks/local_models/cases.py`, `cases.jsonl`, `plan.json`, `common.py`, `load.py`, `run.py`, `score.py`, new benchmark tests.

- [x] Build seed 7, eight people using the current generator with rendering, clinical presentation and handwriting. Save generator version, recipe, source hashes and audit outcomes.
- [x] Select document paths from actual source records; eliminate historical hardcoded filenames and expected values.
- [x] Cover device statistics/windows, labs/ranges/units, documents/OCR, genotype and PGx, medications, journal, missing evidence and same-session follow-ups. Avoid treating hidden simulation history as visible evidence.
- [x] Derive numeric, temporal and genotype expectations from source records. Add binding checks so swapped labels/dates and wrong genotype identifiers cannot pass by matching a bag of numbers.
- [x] Require exactly 100 unique IDs, 60 en / 30 zh-CN / 5 ja / 5 zh-TW, multiple people and multiple task families. Record a 30-question smoke selection separately.
- [x] Wire selected documents, genotype imports, medication data and frozen as-of time into loading and execution. Validate corpus fingerprints before writing data or making model calls.
- [x] Support multi-turn sessions and fixed as-of dates without sending reference answers to the agent.
- [x] Generate offline scenario-review packets through mirobody-gen; preserve unassessed clinical criteria.

## Task 4: Documentation and verification

Files: `CHANGELOG.md`, `docs/answers.md`, `docs/model-choice.md`, `docs/model-choice.zh-CN.md`, `benchmarks/local_models/README.md`, implementation tests.

- [x] Document thinking/answer separation, the terminal-text buffering tradeoff, actual endpoint limitations, all four latency fields and the 100-question recipe.
- [x] Document fixture audits and distinguish new execution from archived scores; retain historical results unchanged.
- [x] Run `ruff check mirobody examples`, `python -m compileall -q mirobody`, `pytest -q`, `lint-imports`, and bare kernel imports against source.
- [x] Run affected synthetic tests and public source regressions. The untouched baseline full suite has five collection errors in gitignored tests; report these separately if they remain.
- [ ] Run the 100-question learned-model evaluation against an isolated configured test stack. No running application/model stack was available during this implementation; no new model score or hardware latency improvement is claimed.

## Task 5: Genuine automatic long-context evaluation (user steering)

Files: `mirobody/agent/middleware/genotype_summarization.py`, `mirobody/agent/wire/stream.py`, `mirobody/agent/agent.py`, `benchmarks/local_models/long_context.py`, `tests/agent/test_long_context.py`, `tests/agent/test_long_context_runner.py`.

- [x] Reproduce early-fact loss through the real automatic middleware path; never return facts from a scripted summarizer unless its actual input contained them.
- [x] Remove the inherited 4,000-token tail trim. Check the complete redacted summary input against the same model's budget; fail explicitly on unknown budgets, overflow and empty/truncated summaries.
- [x] Verify at least two advancing summary events and completed native summary-model callbacks across real sequential graph turns, including dates, units, later corrections, missingness and genotype protection.
- [x] Publish numeric `usage.context` receipts, never summary contents.
- [x] Grow one real evaluation conversation with distinct consumed device records, within 16 pressure requests. Require two committed summaries plus completed summary calls before asking the scored retention question; otherwise record an unexercised failure.
- [x] Retain pressure transcripts, source ranges/hashes, request counts and cost. Keep the heavy case outside the 30-question smoke selection.

## Verification status

- Main integrated for PR submission: `d4468653` (through PR 168); generator: `9723605`.
- Frozen suite: 100 unique cases, 60 en / 30 zh-CN / 5 ja / 5 zh-TW; 30-case smoke; 13 consumed documents.
- Independently regenerated source corpus: all 59 gated input files match; audit log paths are recorded separately.
- Genuine automatic-compaction contract: committed cutoffs `[4, 8]`, two completed summary callbacks, early facts/corrections retained in actual inputs. This uses a scripted source-only model, not a learned-model quality result.
- Full `pytest -q` still stops on the same five pre-existing gitignored collection errors (removed extraction/MCP/SSE/task symbols and missing `pytz`). Public/source evidence tests are run separately and their results are reported with skips/xfails.
- Upstream corpus privacy replay audit remains failed (8 matches), with unrun image/temporal audits preserved. The suite is development/regression material, not certified clinical or training gold.

## Frontend integration (user steering)

- `mirobody-web` base `2d6891f`, source commit `937eb1b`, branch `feat/harness-thinking-experience`:
  immediate first visible content, question-bound stream lifecycle, actual
  thinking/tool/context steps, truthful stop/partial/error states and final-only copy.
- Real summary generation emits paired content-free `context` events. Two
  actual automatic summaries and failed-summary paths are covered by the graph tests.
- Backend public/evidence suite: **755 passed, 107 skipped, 12 xfailed** across
  21 tracked modules. All five collection failures of the full suite remain
  the pre-existing ones named above. Ruff, compileall, six import contracts,
  bare source imports and diff checks passed.
- Frontend: **460 tests passed** across 34 files; lint has zero errors and four
  existing warnings in untouched code. Production build passed with existing
  dependency/chunk-size warnings.
- Browser verification used an isolated synthetic HTTP SSE server and the real
  client parser, stores and renderers: first thinking before answer, parallel
  tools, partial results, real-shaped summary activity, stop then re-ask,
  history, clipboard success/failure, keyboard expansion, four locales,
  reduced motion and a 390 px viewport without horizontal overflow.
- The production output was synchronized into the backend's `frontend/`:
  all **74 files** match the build by SHA-256, with no extra files.
- Stop ends reception; server-side generation cancellation remains explicit
  open work in `docs/roadmap.md`. No live learned-model score or hardware
  TTFT improvement is claimed; the 100-question model run is still pending.

## Additional PR gates

- The separate genomics unittest suite exposed two stale summary spies: they
  patched the inherited summary implementation and supplied no model window.
  They now declare a bounded test model and inspect actual model input through
  its generation method, preserving the genotype-redaction assertions.
- `python -m unittest discover -s benchmarks/genomics -p 'test_*.py'`:
  37 tests run, 36 passed and one skipped locally. The health-record unittest
  suite ran three tests, all passing. The affected thinking/summary tests ran
  43 tests, all passing after this correction.
- Comment-style, version and PHI-log gates passed. New diagnostics retain
  IDs/status messages without model aliases or tool-name interpolation; the
  PHI baseline was not expanded.
