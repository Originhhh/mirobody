"""The stress runner cannot pass an unexercised or manually claimed compaction."""

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

BENCH = Path(__file__).resolve().parents[2] / "benchmarks" / "local_models"
sys.path.insert(0, str(BENCH))
stress = importlib.import_module("long_context")


def receipt(cutoffs=(), completed=0, window=12000):
    return {"type": "usage", "input_tokens": 30, "output_tokens": 2,
            "context": {"summary_cutoffs": list(cutoffs), "summary_calls_started": completed,
                        "summary_calls_completed": completed, "summary_calls_failed": 0,
                        "input_window_tokens": window}}


def fixture(tmp_path):
    rows = [{"record": index, "text": f"Source record {index}. " * 100} for index in range(12)]
    (tmp_path / "source.json").write_text(json.dumps({"records": rows}))
    corpus = SimpleNamespace(dir=tmp_path, path=lambda relative: tmp_path / relative)
    case = {"question": "Recall the comparison label and baseline dates.", "as_of": "2026-09-02",
            "long_context": {"minimum_summaries": 2, "max_pressure_turns": 3, "source_files": ["source.json"]}}
    parent = {"session_id": "real-session", "blocks": [receipt()], "finish_reason": "stop"}
    return corpus, case, parent


def test_distinct_pressure_records_are_used_once(tmp_path):
    corpus, _, _ = fixture(tmp_path)
    batches = list(stress.source_batches(corpus, ["source.json"], 2400))
    indices = [i for batch in batches for i in range(batch["start"], batch["end"])]
    assert indices == list(range(12))


def test_final_question_requires_both_real_event_receipts_and_completed_callbacks(tmp_path):
    corpus, case, parent = fixture(tmp_path)
    calls = []

    def ask(account, question, timeout, *, session_id, as_of):
        calls.append(question)
        assert session_id == "real-session" and as_of == case["as_of"]
        cutoff = (4,) if len(calls) == 1 else (8,) if len(calls) == 2 else ()
        return {"blocks": [receipt(cutoff, int(bool(cutoff)))], "seconds": 1, "finish_reason": "stop"}

    result = stress.run_long_context(None, case, parent=parent, corpus=corpus, ask=ask, timeout=3)
    assert calls[-1] == case["question"] and len(calls) == 3
    assert result["long_context"]["observed_summaries"] == 2
    assert result["long_context"]["summary_calls_completed"] == 2
    assert result["long_context"]["status"] == "exercised"
    assert result["context_setup_seconds"] == 2 and result["seconds"] == 3
    assert result["final_request_seconds"] == 1


def test_repeated_persisted_event_does_not_count_as_two_summaries(tmp_path):
    corpus, case, parent = fixture(tmp_path)
    calls = []

    def ask(account, question, timeout, **kwargs):
        calls.append(question)
        return {"blocks": [receipt((4,), 1)], "seconds": 1, "finish_reason": "stop"}

    result = stress.run_long_context(None, case, parent=parent, corpus=corpus, ask=ask, timeout=3)
    assert case["question"] not in calls
    assert result["long_context"]["status"] == "not_exercised"
    assert result["long_context"]["observed_summaries"] == 1
    assert result["error"] and len(result["pressure_turns"]) == 3


def test_events_without_model_callbacks_cannot_claim_a_summary(tmp_path):
    corpus, case, parent = fixture(tmp_path)
    calls = []

    def ask(account, question, timeout, **kwargs):
        calls.append(question)
        return {"blocks": [receipt((len(calls) * 4,), 0)], "seconds": 1, "finish_reason": "stop"}

    result = stress.run_long_context(None, case, parent=parent, corpus=corpus, ask=ask, timeout=3)
    assert case["question"] not in calls and result["long_context"]["status"] == "not_exercised"


def test_missing_or_overlarge_context_budget_does_not_start_pressure(tmp_path):
    corpus, case, parent = fixture(tmp_path)

    def unexpected(*args, **kwargs):
        raise AssertionError("Unconfigured context must not consume model calls")

    for blocks in ([], [receipt(window=1_000_000)]):
        parent["blocks"] = blocks
        result = stress.run_long_context(None, case, parent=parent, corpus=corpus, ask=unexpected, timeout=3)
        assert result["long_context"]["status"] == "not_exercised"
        assert result["long_context"]["pressure_requests"] == 0


def test_missing_session_cannot_silently_create_independent_pressure_conversations(tmp_path):
    corpus, case, parent = fixture(tmp_path)
    parent["session_id"] = None

    def unexpected(*args, **kwargs):
        raise AssertionError("The long-context prerequisite must establish a real session")

    result = stress.run_long_context(None, case, parent=parent, corpus=corpus, ask=unexpected, timeout=3)
    assert result["long_context"]["status"] == "not_exercised"
    assert result["seconds"] is None
    parent.pop("session_id")
    result = stress.run_long_context(None, case, parent=parent, corpus=corpus, ask=unexpected, timeout=3)
    assert result["long_context"]["status"] == "not_exercised"
