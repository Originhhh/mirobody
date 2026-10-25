# Intent evaluation expansion implementation plan

> **For agentic workers:** Implement the approved evaluation expansion in this worktree, keeping the product harness and PR #178 files untouched. Execute the tasks below in order and verify each deliverable.

**Goal:** Ship 32 development questions (24 capability, 8 regression), 8 patient-disjoint held-out questions, and 4 ordinary/stress pairs with independent synthetic evidence, typed answer facts, explicit rubrics, and executable external-model review.

**Architecture:** Build small, auditable evidence snapshots from a fresh mirobody-gen cohort (seed 20261025, 30 people). Store development and reliability cases in the repository; write held-out answer keys outside the tracked tree and publish only their hashes. Validate facts and source selectors offline, and keep optional evidence-conditioned model baselines distinct from product-harness results.

**Tech stack:** Python 3.12, standard-library JSON/HTTP/hash utilities, pytest, the existing mirobody-gen checkout for generation only.

## Global constraints

- Do not edit the agent, the original 100-case suite, or PR #178's judge.
- No new runtime dependencies or library-layer modules.
- Synthetic visible evidence only; do not expose latent diagnoses or invisible source values to an answering model.
- Keys are read only from an explicit environment file or process environment, and never written to results or printed.
- Keep every patient's cases and all reliability variants in one split.
- Data-management and handoff rubrics are authored evaluation candidates, not clinical validation.
- User scope update: prioritize clinician assistance and personal record management; no medical consultation task mix. Borrow public personal-health benchmark task types without copying restricted data.
- Keep capability and regression scores separate and measure actual item-level model differences before claiming discrimination.
- A padded conversation is not evidence of real native compaction; require execution receipts before reporting that dimension as exercised.

## Task 1: Data contract and rejection controls

Create `benchmarks/intent/schema.py` and `mirobody/tests/test_intent_evaluation.py`.

- [x] Specify `validate_case(case)`, `validate_dataset(cases)`, `digest(value)`, and `answer_packet(case)`.
- [x] Add failing controls for source/fact references, hidden answer leakage, duplicate IDs, split leakage and tampered manifests.
- [x] Implement the contract and rerun `python -m pytest -q mirobody/tests/test_intent_evaluation.py`.

The answering packet contains only `question`, visible `evidence`, actual user history, locale and clock. Gold facts, rubrics, source paths and split labels remain evaluator-side.

## Task 2: Fresh grounded dataset and paired variants

Create `benchmarks/intent/build.py`, authored question templates, and `demo/intent/`.

- [x] Build a fresh cohort with `PYTHONPATH=<mirobody-gen> python -m mirobody_gen build --seed 20261025 --people 30 --clinical --out <outside-tree>/cohort`.
- [x] Preserve generator commit, generation recipe and hashes of consumed source files.
- [x] Select observable report rows, medication schedule versions and device days with explicit selectors; independently calculate derived facts.
- [x] Emit exactly 32 development tasks across record filing/medication versions/personal statistics/documents/clinician preparation/authorization, 8 held-out tasks and 8 reliability episodes in 4 pairs.
- [x] Validate source values, visibility and split isolation; rebuild twice and compare file hashes.

## Task 3: Optional external-model review and calibration baseline

Create `benchmarks/intent/models.py`, `benchmarks/intent/evaluate.py` and `benchmarks/intent/run.py`.

- [x] Require explicit provider/model/environment file for paid calls; bind provider-specific keys to fixed matching endpoints.
- [x] Hash complete requests, model identity and evaluator version; strictly validate review JSON, quotes, evidence references and numeric tolerances.
- [x] Preserve evaluator errors in the denominator and keep all raw responses outside tracked files.
- [x] Run a live dataset-quality review and same-question calibration of all 32 tasks with two answering models and an external judge.
- [x] Do not run held-out questions against answering models. Do not label evidence-conditioned calls as Mirobody harness performance.

## Task 4: Documentation and gates

Create English and Chinese benchmark READMEs, add the benchmark index entry and an Unreleased changelog entry.

- [x] Document fresh-cohort reproduction, validation, held-out handling, result schemas and product-adapter requirements.
- [x] Record actual live review counts, concerns, errors and model identities separately from dataset contract validation.
- [x] Run `ruff check mirobody examples`, `python -m compileall -q mirobody`, `pytest -q`, `lint-imports`, and bare library imports against this checkout. Also lint and compile the new benchmark modules.
- [x] Review the complete diff and verify no PR #178 implementation files changed.

## Recorded outcome

- 32 main tasks, 8 held-out, 4 reliability pairs; 888 source snapshots checked and byte-identical rebuild verified.
- Two answering models completed all 32 tasks; external Sonnet judge produced 28 valid reviews per model. Failures remain explicit.
- Six capability items separate the models on executable facts alone; two more differ on handoff semantics with adjudication notes.
- Final checks: 893 passed, 110 skipped, 12 xfailed; ruff/compileall/bare imports pass; 6 import contracts kept.
- Detailed evidence and per-item comparison: `benchmarks/intent/validation.json`; raw responses and held-out labels stay outside the repository.
