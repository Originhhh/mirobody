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

## Initial recorded outcome (superseded calibration)

- 32 main tasks, 8 held-out, 4 reliability pairs; 888 source snapshots checked and byte-identical rebuild verified.
- Two answering models completed all 32 tasks; external Sonnet judge produced 28 valid reviews per model. Failures remain explicit.
- Six capability items separate the models on executable facts alone; two more differ on handoff semantics with adjudication notes.
- Final checks: 893 passed, 110 skipped, 12 xfailed; ruff/compileall/bare imports pass; 6 import contracts kept.
- Detailed evidence and per-item comparison: `benchmarks/intent/validation.json`; raw responses and held-out labels stay outside the repository.

## Modern-model revision

- Use Claude Opus 5.5 and GPT-6 Luna with medium reasoning, 8192 output tokens
  and a 180-second timeout. Keep both common judges and cross-family reviews.
- Keep 32 main tasks, eight held-out tasks and four reliability pairs. Rebuild
  890 verified source snapshots using the declared Asia/Shanghai account day,
  explicit 21/7 baseline and complete three-result report comparisons.
- Distinguish medication strength from per-dose instructions, accept unordered
  excluded dates without accepting duplicates, and bind adjacent key/value
  table units locally. Invalidate changed reviewer inputs rather than carrying
  over their old scores.
- Both models answered all 32 visible packets. Four medication reviews per
  audit and incomplete scoring remain pending provider access. The current
  report records these limits; older model totals above are historical.
- The eight held-out tasks remain uncalled. Raw responses, source packets and
  revalidation scripts are preserved outside the tracked tree.

## Additional independent judge

The user authorized DeepSeek V4 Pro on Nebula after the supplied key was found
to lack Claude/Luna access. Preserve both existing answer sets and earlier
partial judgments. Run a separate common judge over the full fixed development
set, and audit all 32 corrected tasks, including the medication rubrics.

- [x] Bind `nebula` to its fixed HTTPS endpoint and the existing
  `NEUBULA_API_KEY` environment entry; preserve actual response model identity,
  high reasoning settings, output budget and request provenance.
- [x] Verify key isolation, payload and cache identity with the targeted suite.
- [x] Complete the 32-task audit and two 32-answer score runs; retain failures
  and every retry attempt without regenerating answers.
- [x] Independently inspect meaningful score differences and any audit issues.
- [ ] Update both READMEs, measured validation, cloud reproduction bundle and
  PR179; run source gates and verify CI on the pushed commit.

### Additional-judge outcome

- DeepSeek V4 Pro returned all 96 valid records after four invalid/incomplete
  evaluations alone were retried. Initial requests used high thinking,
  8192 tokens and 180 seconds; those retries used 16384 tokens and 300 seconds.
  No saved answer was regenerated. All request/case/answer identities checked.
- Evaluator 9 fixed date/quantity comma tokenization and table values whose
  unit is in the enclosing section heading. Two saved valid verdicts recovered
  offline; raw versions and original failures remain immutable.
- Raw scores: Opus 32/32, Luna 29/32. Source-contract adjudication: Opus 31/32,
  Luna 29/32. Both infer an unsupported per-dose amount in medication02;
  one Luna date exclusion was wrongly failed by the judge. Raw and adjudicated
  metrics are separate; this is not clinical expert validation.
- Only two confirmed capability differences remain, both in date alignment.
  Opus is near ceiling (23/24 capability). Predeclare six harder replacements
  within the same quota for a later revision; this frozen set is unchanged.
- Source gates: 55 targeted tests; 915 passed, 110 skipped, 12 xfailed overall;
  ruff, compile, bare imports and all six import contracts pass.
