"""What the judge is given, and what it refuses to accept as a verdict.

The judge is the second reader for answers the literal checks reject over wording, so the
properties here are about trust in its input and output: the frozen manifest decides which
gold is used, every tool result of the turn and of the earlier turns reaches it, a duration
keeps its kind, a malformed reply is an evaluator failure, and the cache key covers the
rubric, the judge, the gold and the evidence.
"""
from __future__ import annotations

import importlib.util
import json
import hashlib
import sys
from pathlib import Path

import pytest

_JUDGE = Path(__file__).resolve().parents[2] / "benchmarks" / "local_models" / "judge.py"
_spec = importlib.util.spec_from_file_location("local_judge", _JUDGE)
_judge = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_judge)

answer_text = _judge.answer_text
cache_key = _judge.cache_key
evidence_text = _judge.evidence_text
facts_text = _judge.facts_text
saved_manifest = _judge.saved_manifest
validate = _judge.validate

CASE = {
    "id": "c1",
    "question": "What was my resting heart rate in August?",
    "facts": [
        {"kind": "number", "value": 53.0, "tol": 0.5, "unit": "bpm", "label": "first"},
        {"kind": "words", "any": ["rise", "fall"], "label": "direction"},
    ],
}
RUN = {
    "prior_blocks": [{"type": "tool_result", "content": "prior turn row"}],
    "blocks": [
        {"type": "tool_result", "status": "ok", "name": "query_health_indicators",
         "content": "rid|name|value\nr1|heart|53\nr2|heart|50"},
        {"type": "reasoning", "reasoning": "thinking"},
        {"type": "text", "text": "It fell from 53 to 50."},
    ],
}


def test_facts_keep_tolerance_and_unit() -> None:
    text = facts_text(CASE)
    assert '"value": 53.0' in text and '"unit": "bpm"' in text and '"tol": 0.5' in text
    assert '"any": ["rise", "fall"]' in text


def test_evidence_includes_prior_turns_and_excludes_reasoning() -> None:
    text = evidence_text(RUN)
    assert "prior turn row" in text
    assert "r1|heart|53" in text
    assert "thinking" not in text


def test_answer_is_only_the_text_blocks() -> None:
    assert answer_text(RUN) == "It fell from 53 to 50."


def test_a_malformed_verdict_is_not_accepted() -> None:
    for bad in ({}, {"correct": "yes"}, {"correct": True, "missing": "x"},
                {"missing": [], "unsupported": []}):
        try:
            validate(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted a malformed verdict: {bad}")


def test_a_well_formed_verdict_round_trips() -> None:
    verdict = validate({"correct": False, "missing": ["first"], "unsupported": ["99"], "note": "wrong"})
    assert json.loads(json.dumps(verdict))["unsupported"] == ["99"]


def test_the_cache_key_covers_gold_evidence_and_model() -> None:
    answer = answer_text(RUN)
    base = cache_key(CASE, RUN, answer, "judge-a")
    changed_model = cache_key(CASE, RUN, answer, "judge-b")
    changed_gold = cache_key({**CASE, "facts": [{"kind": "number", "value": 999, "label": "first"}]},
                             RUN, answer, "judge-a")
    changed_answer = cache_key(CASE, RUN, answer + " ", "judge-a")
    assert len({base, changed_model, changed_gold, changed_answer}) == 4


def test_the_frozen_manifest_is_the_default_case_list() -> None:
    run_data = frozen_run([])
    assert saved_manifest(run_data)[0]["id"] == "c1"
    try:
        saved_manifest({"runs": []})
    except SystemExit:
        return
    raise AssertionError("a run without a manifest must not be judged against live cases")


def frozen_run(runs):
    return {"case_manifest": [CASE], "selected_ids": ["c1"], "runs": runs,
            "cases_sha256": hashlib.sha256(json.dumps([CASE], sort_keys=True, ensure_ascii=False,
                                                       separators=(",", ":")).encode()).hexdigest()}


def test_all_existing_gold_types_reach_the_judge():
    facts = [{"kind": "duration", "minutes": [2858.251212], "tol": 3.0, "label": "sleep"},
             {"kind": "genotype", "value": "GA", "label": "rs671"},
             {"kind": "range", "value": "130 - 175", "label": "hemoglobin"}]
    text = facts_text({"facts": facts})
    assert "2858.251212" in text and '"tol": 3.0' in text
    assert '"value": "GA"' in text and '"value": "130 - 175"' in text


def test_a_tampered_frozen_manifest_is_rejected():
    data = frozen_run([])
    data["cases_sha256"] = "wrong-hash"
    with pytest.raises(ValueError, match="hash"):
        saved_manifest(data)


@pytest.mark.parametrize("verdict", [
    {"correct": True, "missing": [{}], "unsupported": [], "note": ""},
    {"correct": True, "missing": ["weight"], "unsupported": [], "note": ""},
    {"correct": False, "missing": [], "unsupported": [], "note": ""},
])
def test_malformed_or_contradictory_verdicts_fail(verdict):
    with pytest.raises(ValueError):
        validate(verdict)


@pytest.mark.parametrize("status", [{"finish_reason": "length"},
                                   {"finish_reason": "stop", "timeout": True},
                                   {"finish_reason": "stop", "error": "failed"}])
def test_incomplete_runs_do_not_enter_correct_counts(tmp_path, monkeypatch, capsys, status):
    run = {"id": "c1", "blocks": [{"type": "text", "text": "53 bpm"}], **status}
    (tmp_path / "qa.json").write_text(json.dumps(frozen_run([run])))
    monkeypatch.setattr(sys, "argv", ["judge.py", str(tmp_path)])
    def unexpected(*args, **kwargs):
        raise AssertionError("An incomplete answer must not be sent to the judge")
    monkeypatch.setattr(_judge, "ask", unexpected)
    assert _judge.main() == 1
    counts = json.loads(capsys.readouterr().out.splitlines()[0])
    assert counts["correct"] == 0 and counts["run_failure"] == 1


def test_all_evaluator_failures_propagate_to_the_exit_code(tmp_path, monkeypatch):
    (tmp_path / "qa.json").write_text(json.dumps(frozen_run([
        {"id": "c1", "finish_reason": "stop", "blocks": [{"type": "text", "text": "53 bpm"}]}])))
    monkeypatch.setattr(sys, "argv", ["judge.py", str(tmp_path)])
    def invalid(*args, **kwargs):
        raise ValueError("Invalid verdict")
    monkeypatch.setattr(_judge, "ask", invalid)
    assert _judge.main() == 1


def test_explicit_manifest_works_without_frozen_labels_and_is_recorded(tmp_path, monkeypatch):
    run = {"id": "c1", "finish_reason": "stop", "blocks": [{"type": "text", "text": "53 bpm"}]}
    (tmp_path / "qa.json").write_text(json.dumps({"runs": [run], "selected_ids": ["c1"]}))
    explicit = tmp_path / "override.jsonl"
    explicit.write_text(json.dumps(CASE) + "\n")
    monkeypatch.setattr(sys, "argv", ["judge.py", str(tmp_path), "--cases-file", str(explicit)])
    monkeypatch.setattr(_judge, "ask", lambda *a, **k: {"correct": True, "missing": [], "unsupported": [], "note": ""})
    assert _judge.main() == 0
    meta = json.loads((tmp_path / "judge-meta.json").read_text())
    assert meta["cases_source"]["kind"] == "explicit_override"
    assert meta["cases_sha256"] == frozen_run([])["cases_sha256"]


def test_evidence_budget_includes_the_serialized_headers(tmp_path, monkeypatch):
    content = "x" * (_judge.MAX_EVIDENCE_CHARS - 10) + "TAILMARKER"
    run = {"id": "c1", "finish_reason": "stop", "blocks": [
        {"type": "tool_result", "name": "query_health_indicators", "content": content},
        {"type": "text", "text": "53 bpm"}]}
    (tmp_path / "qa.json").write_text(json.dumps(frozen_run([run])))
    monkeypatch.setattr(sys, "argv", ["judge.py", str(tmp_path)])
    def forbidden(*a, **k):
        raise AssertionError("A truncated packet must not reach the judge")
    monkeypatch.setattr(_judge, "ask", forbidden)
    assert _judge.main() == 1
    counts = json.loads((tmp_path / "judge-meta.json").read_text())["counts"]
    assert counts["evaluator_failure"] == 1 and counts["judged"] == 0
