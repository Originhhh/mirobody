"""Reject misleading evidence and evaluation results in the intent suite."""

from __future__ import annotations

import copy
import io
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from benchmarks.intent.schema import answer_packet, digest, validate_case, validate_dataset
from benchmarks.intent.evaluate import chart_check, grade, validate_audit
from benchmarks.intent.run import decode_json, identity, messages_for, one, summarize
from benchmarks.intent.schema import read_jsonl, verify_bundle
from benchmarks.intent.analytics import compute
from benchmarks.intent.oracles import recompute
from benchmarks.intent.compare import compare
from benchmarks.intent.models import call, request_body

DATA = Path(__file__).resolve().parents[2] / "demo" / "intent"


@pytest.fixture
def case():
    return {
        "id": "intent-dev-one", "suite_id": "intent-20261025", "synthetic": True,
        "family_id": "seed-20261025:p001", "split": "development", "lang": "zh-CN",
        "domain": "interpretation", "question": "这次体重是多少？", "as_of": "2026-09-02",
        "history": [], "evidence": [{"id": "e1", "kind": "reading", "visible": True,
            "data": {"indicator": "Body weight", "value": 69.5, "unit": "kg"},
            "source": {"file": "manifest.jsonl", "line": 1, "selector": "rows/1"}}],
        "facts": [{"id": "f1", "kind": "number", "value": 69.5, "tol": 0.05,
                   "unit": "kg", "label": "weight", "evidence_ids": ["e1"]}],
        "rubric": [{"id": "r1", "text": "States the recorded weight with its unit.",
                    "critical": True, "evidence_ids": ["e1"], "fact_ids": ["f1"]}],
        "execution": {"mode": "evidence_conditioned", "requirements": []},
    }


def test_answer_packet_never_contains_gold_or_local_source_paths(case):
    validate_case(case)
    packet = answer_packet(case)
    assert not {"facts", "rubric", "split", "family_id"} & packet.keys()
    assert "source" not in packet["evidence"][0]
    assert packet["evidence"][0]["data"]["value"] == 69.5


def test_hidden_evidence_cannot_be_a_required_answer_fact(case):
    case["evidence"][0]["visible"] = False
    with pytest.raises(ValueError, match="visible"):
        validate_case(case)


@pytest.mark.parametrize("path", ["../secrets", "/absolute/file"])
def test_source_selectors_cannot_escape_the_corpus(case, path):
    case["evidence"][0]["source"]["file"] = path
    with pytest.raises(ValueError, match="source"):
        validate_case(case)


def test_patient_and_variants_stay_in_one_split(case):
    other = copy.deepcopy(case)
    other.update(id="intent-holdout-one", split="held_out")
    with pytest.raises(ValueError, match="split"):
        validate_dataset([case, other])


def test_fact_references_and_numeric_types_are_checked(case):
    case["facts"][0]["evidence_ids"] = ["invented"]
    with pytest.raises(ValueError, match="evidence"):
        validate_case(case)
    case["facts"][0]["evidence_ids"] = ["e1"]
    case["facts"][0]["value"] = True
    with pytest.raises(ValueError, match="number"):
        validate_case(case)


def test_duplicate_ids_and_negative_tolerance_fail_closed(case):
    with pytest.raises(ValueError, match="Duplicate"):
        validate_dataset([case, case])
    case["facts"][0]["tol"] = -1
    with pytest.raises(ValueError, match="tolerance"):
        validate_case(case)


def test_complete_request_identity_changes_with_evidence_and_rubric(case):
    original = digest(case)
    case["evidence"][0]["data"]["value"] = 70
    assert digest(case) != original


def verdict_for(case, answer, value=69.5, unit="kg"):
    return {"criteria": [{"id": r["id"], "status": "met", "answer_line_ids": ["a1"],
                          "evidence_ids": ["e1"], "reason": "Supported by the record."} for r in case["rubric"]],
            "facts": [{"id": "f1", "status": "stated", "value": value, "unit": unit, "answer_line_ids": ["a1"]}]}


def test_verdict_requires_every_criterion_and_real_quotes(case):
    answer = "69.5 kg"
    v = verdict_for(case, answer)
    v["criteria"] = []
    with pytest.raises(ValueError, match="criterion"):
        grade(case, answer, v)


def test_line_references_preserve_layout_without_accepting_fabricated_content(case):
    answer = "**69.5 kg**\n\nRecorded weight."
    verdict = verdict_for(case, "69.5 kg Recorded weight.")
    assert grade(case, answer, verdict)["evidence_conditioned_pass"]
    verdict["criteria"][0]["answer_line_ids"] = ["fabricated"]
    with pytest.raises(ValueError, match="line"):
        grade(case, answer, verdict)
    v = verdict_for(case, answer)
    v["facts"][0]["answer_line_ids"] = ["invented"]
    with pytest.raises(ValueError, match="line"):
        grade(case, answer, v)


def test_numeric_rounding_is_programmatic_not_a_judge_opinion(case):
    assert grade(case, "69.46 kg", verdict_for(case, "69.46 kg", 69.46))["facts"][0]["ok"]
    assert not grade(case, "69.6 kg", verdict_for(case, "69.6 kg", 69.6))["facts"][0]["ok"]


def test_excluded_dates_are_an_unordered_pair_but_cannot_repeat():
    task = next(c for c in read_jsonl(DATA / "development.jsonl")
                if c.get("analysis", {}).get("kind") == "trimmed_comparison")
    answer = "\n".join(f"{f['value']} {f.get('unit', '')}" for f in task["facts"])
    verdict = {"criteria": [{"id": r["id"], "status": "met", "answer_line_ids": ["a1"],
                              "evidence_ids": [], "reason": "Checked."} for r in task["rubric"]],
               "facts": [{"id": f["id"], "status": "stated", "value": f["value"],
                          "unit": f.get("unit", ""), "answer_line_ids": [f"a{i + 1}"]}
                         for i, f in enumerate(task["facts"])]}
    verdict["facts"][-2]["value"], verdict["facts"][-1]["value"] = (
        verdict["facts"][-1]["value"], verdict["facts"][-2]["value"])
    verdict["facts"][-2]["answer_line_ids"], verdict["facts"][-1]["answer_line_ids"] = ["a4"], ["a3"]
    assert grade(task, answer, verdict)["evidence_conditioned_pass"]
    verdict["facts"][-1].update(value=verdict["facts"][-2]["value"], answer_line_ids=["a4"])
    assert not grade(task, answer, verdict)["evidence_conditioned_pass"]


def test_medication_amount_keys_describe_recorded_strength_not_inferred_dose():
    tasks = [c for c in read_jsonl(DATA / "development.jsonl") if c["domain"] == "medication"]
    assert len(tasks) == 4
    for task in tasks:
        assert all("strength" in f["label"] for f in task["facts"] if f["kind"] == "number")
        assert "per-dose" in task["rubric"][0]["text"]


def test_fabricated_unit_and_quantity_are_evaluator_failures(case):
    with pytest.raises(ValueError, match="unit"):
        grade(case, "69.5 pounds", verdict_for(case, "69.5", unit="kg"))
    with pytest.raises(ValueError, match="number"):
        grade(case, "88 kg", verdict_for(case, "88 kg", value=69.5))
    assert grade(case, "69.5 公斤", verdict_for(case, "69.5 公斤", unit="公斤"))["facts"][0]["ok"]
    assert not grade(case, "69.5 pounds", verdict_for(case, "69.5 pounds", unit="pounds"))["facts"][0]["ok"]


def test_partial_unit_cannot_hide_a_concentration(case):
    with pytest.raises(ValueError, match="unit"):
        grade(case, "69.5 kg/L", verdict_for(case, "69.5 kg/L", unit="kg"))


@pytest.mark.parametrize("answer", ["2026-01-24，69.5 kg", "Recorded，69.5 kg"])
def test_chinese_comma_does_not_merge_the_quantity_with_a_date_or_word(case, answer):
    assert grade(case, answer, verdict_for(case, answer))["evidence_conditioned_pass"]
    with pytest.raises(ValueError, match="number"):
        grade(case, answer.replace("69.5", "6.95"), verdict_for(case, answer))


def test_date_separator_is_not_a_thousands_separator(case):
    case["facts"][0]["value"] = case["evidence"][0]["data"]["value"] = 165
    answer = "2026-01-24,165 kg"
    assert grade(case, answer, verdict_for(case, answer, value=165))["evidence_conditioned_pass"]
    case["facts"][0]["value"] = case["evidence"][0]["data"]["value"] = 1650
    answer = "1,650 kg"
    assert grade(case, answer, verdict_for(case, answer, value=1650))["evidence_conditioned_pass"]


def test_native_work_is_never_claimed_from_an_authored_history(case):
    case["execution"]["requirements"] = ["native_summaries>=2"]
    result = grade(case, "69.5 kg", verdict_for(case, "69.5 kg"))
    assert result["evidence_conditioned_pass"]
    assert result["execution_status"] == "not_exercised"
    assert result["required_execution_pass"] is None
    assert grade(case, "69.5 kg", verdict_for(case, "69.5 kg"), {"native_summaries": 2})["required_execution_pass"]


def test_summary_keeps_answer_and_evaluator_failures_in_the_denominator(case):
    cases = [{**case, "id": str(i), "evaluation_role": "capability"} for i in range(3)]
    rows = [{"id": "0", "status": "ok", "grade": {"evidence_conditioned_pass": True}},
            {"id": "1", "status": "evaluator_failure"}, {"id": "2", "status": "answer_failure"}]
    summary = summarize("score", cases, rows)
    assert summary["selected"] == 3
    assert summary["evidence_conditioned_pass"] == 1
    assert summary["statuses"] == {"ok": 1, "evaluator_failure": 1, "answer_failure": 1}
    assert summary["roles"]["capability"] == {"selected": 3, "pass": 1, "valid_reviews": 1}


def test_request_cache_includes_model_answer_rubric_and_receipts(case):
    messages = messages_for("score", case, "69.5 kg")
    key = identity("score", case, "deepseek", "A", messages, {})
    assert key != identity("score", case, "deepseek", "B", messages, {})
    assert key != identity("score", case, "deepseek", "A", messages_for("score", case, "88 kg"), {})
    assert key != identity("score", case, "deepseek", "A", messages, {"native_summaries": 2})
    changed = copy.deepcopy(case)
    changed["rubric"][0]["text"] += " More explicit."
    assert key != identity("score", changed, "deepseek", "A", messages, {})


def test_reasoning_models_omit_unsupported_sampling_and_keep_budget_in_cache(case):
    messages = messages_for("score", case, "69.5 kg")
    settings = {"reasoning_effort": "medium", "max_output_tokens": 8192, "timeout_seconds": 180}
    body = request_body("openrouter", "openai/gpt-6-luna", messages, json_output=True,
                        reasoning_effort="medium", max_output_tokens=8192)
    assert "temperature" not in body
    assert body["reasoning"] == {"effort": "medium"}
    assert body["max_tokens"] == 8192
    base = identity("score", case, "openrouter", "openai/gpt-6-luna", messages, {}, settings)
    assert base != identity("score", case, "openrouter", "openai/gpt-6-luna", messages, {},
                            {**settings, "reasoning_effort": "high"})
    assert base != identity("score", case, "openrouter", "openai/gpt-6-luna", messages, {},
                            {**settings, "max_output_tokens": 16384})
    direct = request_body("openai", "gpt-6-luna", messages, json_output=True,
                          reasoning_effort="medium", max_output_tokens=8192)
    assert direct["max_completion_tokens"] == 8192 and "max_tokens" not in direct
    assert direct["reasoning_effort"] == "medium" and "temperature" not in direct


def test_nebula_uses_only_its_matching_key_and_keeps_reasoning_identity(case, tmp_path, monkeypatch):
    monkeypatch.delenv("NEUBULA_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "unrelated-key")
    env = tmp_path / "private.env"
    env.write_text('NEUBULA_API_KEY="synthetic-nebula-key"\n')
    seen = []

    class Opener:
        def open(self, request, timeout):
            seen.append((request, timeout))
            return io.StringIO(json.dumps({"id": "synthetic-id", "model": "deepseek-v4-pro",
                "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]}))

    monkeypatch.setattr("benchmarks.intent.models.urllib.request.build_opener", lambda *_: Opener())
    messages = messages_for("audit", case)
    settings = {"reasoning_effort": "high", "max_output_tokens": 8192, "timeout_seconds": 180}
    result = call("nebula", "deepseek-v4-pro", messages, env, json_output=True, **settings)
    request, timeout = seen[0]
    assert request.full_url == "https://llm.ai-nebula.com/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer synthetic-nebula-key" and timeout == 180
    body = json.loads(request.data)
    assert body["thinking"] == {"type": "enabled"} and body["reasoning_effort"] == "high"
    assert body["max_tokens"] == 8192 and "temperature" not in body
    assert result["provider"] == "nebula" and result["response_model"] == "deepseek-v4-pro"
    assert "synthetic-nebula-key" not in json.dumps(result)
    assert identity("audit", case, "nebula", "deepseek-v4-pro", messages, {}, settings) != identity(
        "audit", case, "openrouter", "deepseek-v4-pro", messages, {}, settings)


def test_frozen_bundle_counts_and_source_derived_fact_integrity():
    result = verify_bundle(DATA)
    assert (result["cases"], result["pairs"], result["families"]) == (40, 4, 20)
    assert (result["held_out"]["cases"], result["held_out"]["families"]) == (8, 8)
    cases = read_jsonl(DATA / "development.jsonl")
    altered = copy.deepcopy(cases[0])
    altered["facts"][0]["value"] += 1
    with pytest.raises(ValueError, match="oracle"):
        validate_case(altered)
    families = {c["family_id"] for c in cases}
    assert not families.intersection(result["held_out"]["family_ids"])
    assert len({digest(answer_packet(c)) for c in cases}) == len(cases)
    assert sum(c["evaluation_role"] == "capability" for c in cases) == 24
    assert sum(c["evaluation_role"] == "regression" for c in cases) == 8


def test_latest_frequency_follows_effective_change_not_initial_plan():
    case = {"as_of": "2026-06-01", "evidence": [{"id": "e1", "visible": True, "data": {
        "start": "2026-01-01", "strength": "500 mg", "frequency": "bid", "changes": [
            {"date": "2026-04-01", "frequency": "tid"},
            {"date": "2026-05-01", "strength": "250 mg"},
            {"date": "2026-07-01", "frequency": "qd"}]}}]}
    rule = {"op": "effective_plan", "evidence": "e1", "field": "frequency"}
    assert recompute(case, rule) == ("tid", "")
    assert recompute(case, {**rule, "field": "strength_amount"}) == (250, "mg")


def test_trend_model_sees_the_actual_window_timezone_and_missing_policy():
    case = next(c for c in read_jsonl(DATA / "development.jsonl") if c["domain"] == "trend")
    packet = answer_packet(case)
    window = packet["comparison_window"]
    assert window["timezone"] == "Asia/Shanghai"
    assert window["start"] == "2026-08-05" and window["end"] == "2026-09-01"
    assert "first_half" not in window and "second_half" not in window
    assert "never zero" in window["aggregation"]
    lagged = next(c for c in read_jsonl(DATA / "development.jsonl") if c.get("analysis", {}).get("kind") == "lagged_comparison")
    assert answer_packet(lagged)["analysis_parameters"]["lag_policy"].startswith("next calendar day in Asia/Shanghai")


def test_personal_baseline_packet_declares_21_plus_7_days_only():
    case = next(c for c in read_jsonl(DATA / "development.jsonl") if c.get("analysis", {}).get("kind") == "personal_baseline")
    packet = answer_packet(case)
    assert packet["comparison_window"]["baseline_period"] == ["2026-08-05", "2026-08-25"]
    assert packet["comparison_window"]["recent_week"] == ["2026-08-26", "2026-09-01"]
    assert "first_half" not in packet["comparison_window"]
    assert "first_half_days" not in packet["analysis_parameters"]
    assert "step_threshold" not in packet["analysis_parameters"]


def test_chart_checks_type_dates_values_order_and_valid_json():
    case = next(c for c in read_jsonl(DATA / "reliability.jsonl") if c.get("chart_required"))
    chart = {"type": "line", "unit": case["chart_oracle"]["unit"], "data": case["chart_oracle"]["points"]}
    def render(value):
        return "```vis-chart\n" + json.dumps(value) + "\n```"
    assert chart_check(case, render(chart))
    chart["type"] = "pie"
    assert not chart_check(case, render(chart))
    chart["type"] = "line"
    chart["data"] = list(reversed(chart["data"]))
    assert not chart_check(case, render(chart))
    assert not chart_check(case, '```vis-chart\n{"value":69.5<cite>[r1]</cite>}\n```')


def test_audit_never_accepts_an_omitted_criterion(case):
    with pytest.raises(ValueError, match="audit"):
        validate_audit({"assessments": []}, case)


def test_table_units_bind_only_to_the_actual_numeric_column(case):
    answer = "| Date | Weight (kg) | Height (cm) |\n| --- | --- | --- |\n| 2026-01-01 | 69.5 | 180 |"
    v = verdict_for(case, answer)
    v["facts"][0]["answer_line_ids"] = ["a3"]
    assert grade(case, answer, v)["facts"][0]["ok"]
    v["facts"][0]["unit"] = "cm"
    with pytest.raises(ValueError, match="unit"):
        grade(case, answer, v)


@pytest.mark.parametrize("answer", [
    "| Weight (lb) | Height (kg) |\n| --- | --- |\n| 69.5 | 180 |",
    "| Weight | Height |\n| --- | --- |\n| 69.5 lb | 180 kg |",
    "69.5 lb\nUnrelated unit label: kg",
])
def test_cited_lines_cannot_lend_a_unit_across_cells_or_lines(case, answer):
    v = verdict_for(case, answer)
    v["facts"][0]["answer_line_ids"] = [f"a{i + 1}" for i in range(len(answer.splitlines()))]
    with pytest.raises(ValueError, match="unit"):
        grade(case, answer, v)


def test_an_explicit_unit_column_binds_to_its_own_data_row(case):
    answer = "| Date | Result | Unit |\n| --- | --- | --- |\n| 2026-01-01 | 69.5 | kg |\n| 2026-02-01 | 180 | cm |"
    v = verdict_for(case, answer)
    v["facts"][0]["answer_line_ids"] = ["a3"]
    assert grade(case, answer, v)["facts"][0]["ok"]


def test_adjacent_labeled_value_unit_fields_remain_valid(case):
    answer = "Value: 69.5\nUnit: kg\nValue: 180\nUnit: cm"
    v = verdict_for(case, answer)
    v["facts"][0]["answer_line_ids"] = ["a1", "a2"]
    assert grade(case, answer, v)["facts"][0]["ok"]
    v["facts"][0]["answer_line_ids"] = ["a1", "a4"]
    v["facts"][0]["unit"] = "cm"
    with pytest.raises(ValueError, match="unit"):
        grade(case, answer, v)


@pytest.mark.parametrize("labels", [("Value", "Unit"), ("结果", "单位"), ("数值", "单位")])
def test_adjacent_key_value_table_rows_bind_their_unit(case, labels):
    answer = f"| Field | Content |\n| --- | --- |\n| **{labels[0]}** | **69.5** |\n| {labels[1]} | kg |"
    v = verdict_for(case, answer)
    v["facts"][0]["answer_line_ids"] = ["a3", "a4"]
    assert grade(case, answer, v)["facts"][0]["ok"]


@pytest.mark.parametrize("answer, ids", [
    ("| Value | 69.5 |\n| Unit | lb |\n| Value | 180 |\n| Unit | kg |", ["a1", "a4"]),
    ("| Value | 69.5 lb |\n| Unit | kg |", ["a1", "a2"]),
    ("| Value | 69.5 |\n| Unit | kg |", ["a1"]),
    ("| Value | 69.5 |\nUnit: kg", ["a1", "a2"]),
])
def test_key_value_table_unit_cannot_cross_records_or_override_units(case, answer, ids):
    v = verdict_for(case, answer)
    v["facts"][0]["answer_line_ids"] = ids
    with pytest.raises(ValueError, match="unit"):
        grade(case, answer, v)


def test_section_unit_stops_at_the_next_heading(case):
    answer = "**Weight (kg)**\n- 2026-01-01: 69.5\n**Height (cm)**\n- 2026-02-01: 69.5"
    v = verdict_for(case, answer)
    v["facts"][0]["answer_line_ids"] = ["a2"]
    assert grade(case, answer, v)["facts"][0]["ok"]
    v["facts"][0]["answer_line_ids"] = ["a4"]
    with pytest.raises(ValueError, match="unit"):
        grade(case, answer, v)


def test_section_units_bind_to_plain_value_columns_in_their_own_table(case):
    answer = "### Weight (kg)\n| Date | Value | Source |\n| --- | --- | --- |\n| 2026-01-01 | 69.5 | e1 |"
    verdict = verdict_for(case, answer)
    verdict["facts"][0]["answer_line_ids"] = ["a1", "a4"]
    assert grade(case, answer, verdict)["facts"][0]["ok"]
    for variant in (
        answer.replace("Value", "Value (lb)"),
        answer.replace("69.5 |", "69.5 lb |"),
        answer.replace("Source", "Unit").replace("| e1 |", "| lb |"),
        answer + "\n### Height (cm)\n| Date | Value |\n| --- | --- |\n| 2026-02-01 | 69.5 |",
    ):
        verdict["facts"][0]["answer_line_ids"] = [f"a{len(variant.splitlines())}"]
        with pytest.raises(ValueError, match="unit"):
            grade(case, variant, verdict)


def test_gold_values_are_withheld_from_answer_fact_extraction(case):
    packet = json.loads(messages_for("score", case, "69.5 kg")[-1]["content"])
    assert "expected_facts" not in packet
    descriptor = packet["facts_to_extract"][0]
    assert descriptor["id"] == "f1" and descriptor["unit_required"]
    assert not {"value", "oracle", "tol", "unit"}.intersection(descriptor)


def test_stale_or_unbound_answers_fail_before_any_paid_call(case, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A mismatched answer must not reach the provider")
    monkeypatch.setattr("benchmarks.intent.run.call", forbidden)
    for binding in ({}, {"case_sha256": "old-case"}):
        row = one("score", case, "deepseek", "model", None, tmp_path,
                  {"status": "ok", "answer": "69.5 kg", **binding})
        assert row["status"] == "answer_failure"
        assert row["error_type"] == "MissingOrMismatchedCaseHash"
    row = one("score", case, "deepseek", "model", None, tmp_path,
              {"status": "ok", "case_sha256": digest(case), "answer": {"invalid": "object"}})
    assert row["error_type"] == "InvalidAnswerOrReceipts"


def test_json_wrapper_does_not_accept_extra_prose_or_change_values():
    assert decode_json('```json\n{"value":12}\n```') == {"value": 12}
    with pytest.raises(ValueError):
        decode_json('Explanation\n```json\n{"value":12}\n```')


def test_comparison_keeps_missing_judges_unresolved_and_checks_case_binding(case):
    case.update(evaluation_role="capability", expected_response_type="record")
    row = {"id": case["id"], "case_sha256": digest(case), "status": "ok",
           "grade": {"evidence_conditioned_pass": True}}
    result = compare([case], [row], [])
    group = result["groups"]["capability"]
    assert group["selected"] == 1 and group["joint_valid"] == 0
    assert group["left"]["confirmed_pass"] == 1 and group["right"]["unresolved"] == 1
    assert group["left_only_pass"] == []
    left = {**row, "grade": {"evidence_conditioned_pass": True, "facts": [{"id": "f1", "ok": True}]}}
    right = {**row, "grade": {"evidence_conditioned_pass": False, "facts": [{"id": "f1", "ok": False}]}}
    group = compare([case], [left], [right])["groups"]["capability"]
    assert group["left_only_facts_pass"] == [case["id"]]
    with pytest.raises(ValueError, match="different case"):
        compare([case], [{**row, "case_sha256": "old"}], [])


def test_dimensionless_counts_do_not_require_a_physical_unit(case):
    case["facts"][0].update(value=8, unit="", tol=0)
    v = verdict_for(case, "用了8天", value=8, unit="")
    assert grade(case, "用了8天", v)["facts"][0]["ok"]
    assert not grade(case, "用了9天", verdict_for(case, "用了9天", value=9, unit=""))["facts"][0]["ok"]


def analytics_case(kind, metrics):
    return {"window": {"start": "2026-01-01", "end": "2026-01-28"},
            "analysis": {"kind": kind, "step_threshold": 8000},
            "evidence": [{"kind": "device_reading", "visible": True,
                          "data": {"metric": metric, "value": value,
                                   "time": (date(2026, 1, 1) + timedelta(days=day - 1)).isoformat() + "T12:00:00+00:00"}}
                         for metric, observations in metrics.items() for day, value in observations.items()]}


def test_conditions_and_lags_use_different_day_joins():
    metrics = {"steps": {1: 8000, 2: 7000, 3: 9000}, "rhr": {1: 60, 2: 70, 3: 80, 4: 90}}
    result = compute(analytics_case("conditional_mean", metrics))
    assert result["qualifying_days"][0] == 2
    assert result["conditional_rhr_mean"][0] == 70
    result = compute(analytics_case("lagged_comparison", metrics))
    assert result["next_day_high_activity_mean"][0] == 80
    assert result["next_day_low_activity_mean"][0] == 80
    assert result["high_activity_pairs"][0] == 2
    assert result["higher_rhr_group"][0] == "equal"


def test_missingness_changes_pair_denominators_and_streaks():
    result = compute(analytics_case("paired_coverage", {"steps": {1: 8000, 2: 9000, 4: 10000}, "weight": {1: 60, 4: 61}}))
    assert result["complete_pair_days"][0] == 2
    assert result["calendar_days_without_a_pair"][0] == 26
    assert result["paired_day_mean_steps"][0] == 9000
    result = compute(analytics_case("longest_streak", {"steps": {1: 8000, 2: 8000, 4: 9000, 5: 9000}}))
    assert result["longest_streak_days"][0] == 2
    assert result["streak_start"][0] == "2026-01-01"
    assert result["streak_end"][0] == "2026-01-02"


def test_correlation_trimming_and_personal_baseline_have_hand_checked_answers():
    result = compute(analytics_case("correlation", {"steps": {1: 1000, 2: 2000, 3: 3000}, "rhr": {1: 60, 2: 70, 3: 80}}))
    assert result["pearson_r"][0] == pytest.approx(1)
    result = compute(analytics_case("trimmed_comparison", {"steps": {i: i for i in range(1, 29)}}))
    assert result["trimmed_first_mean"][0] == 7.5
    assert result["trimmed_second_mean"][0] == 20.5
    assert result["excluded_date_1"][0] == "2026-01-27"
    result = compute(analytics_case("personal_baseline", {"rhr": {i: 60 if i <= 21 else 70 for i in range(1, 29)}}))
    assert result["earlier_median_rhr"][0] == 60
    assert result["recent_week_mean_rhr"][0] == 70
    assert result["absolute_change"][0] == 10


def test_uneven_weigh_ins_use_observed_days_rather_than_equal_window_means():
    result = compute(analytics_case("observed_weighted_mean", {"weight": {1: 60, 2: 60, 20: 90}}))
    assert result["observed_day_weight_mean"][0] == 70
    assert result["first_half_observed_days"][0] == 2
    assert result["second_half_observed_days"][0] == 1


def test_timezone_is_applied_before_daily_pairing():
    case = analytics_case("conditional_mean", {"steps": {1: 8000}, "rhr": {1: 60}})
    case["evidence"][0]["data"]["time"] = "2026-01-02T01:00:00+08:00"
    assert compute(case)["conditional_rhr_mean"][0] == 60


def test_account_local_day_changes_the_join_instead_of_using_utc():
    case = analytics_case("conditional_mean", {"steps": {1: 8000}, "rhr": {1: 60}})
    case["evidence"][0]["data"]["time"] = "2026-01-02T01:00:00+08:00"
    case["window"]["timezone"] = "Asia/Shanghai"
    with pytest.raises(ValueError, match="empty"):
        compute(case)
