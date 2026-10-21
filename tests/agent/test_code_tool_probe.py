"""The code-language gate must validate labels, execution and changing inputs."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from deepagents.backends.protocol import ExecuteResponse

from benchmarks.local_agent import code_tool_probe as probe
from mirobody.kernel import tools


def case_and_trace(*, code="print(data)", values=None, exit_code=0, query=True):
    case = next(c for c in probe.cases() if c["id"] == "bp_q2")
    expected = {item["label"]: item["value"] for item in case["expect"]}
    value = expected if values is None else values
    messages = [HumanMessage("Synthetic calculation")]
    if query:
        messages.append(ToolMessage(name="query_health_indicators", tool_call_id="q1", content="fixture",
                                    artifact=tools.Envelope(tools.STATUS_OK, data=[{"value": 1.0}])))
    messages += [
        AIMessage(content="", tool_calls=[{"name": "python_exec", "id": "c1", "args": {"code": code}}]),
        ToolMessage(name="python_exec", tool_call_id="c1", content=json.dumps(value),
                    artifact={"status": "ok" if exit_code == 0 else "error", "exit_code": exit_code,
                              "source_tool_call_ids": ["q1"]}),
        AIMessage(content=json.dumps(expected)),
    ]
    return case, messages, expected


@pytest.mark.parametrize("scenario", ["no_query", "no_code", "failed_code", "hardcoded", "wrong_labels", "wrong_output"])
def test_the_gate_rejects_false_success(scenario):
    case, messages, expected = case_and_trace()
    if scenario == "no_query":
        messages.pop(1)
    elif scenario == "no_code":
        messages = [messages[0], messages[1], messages[-1]]
    elif scenario == "failed_code":
        messages[-2].artifact["exit_code"] = 1
    elif scenario == "hardcoded":
        messages[-3].tool_calls[0]["args"]["code"] = "print({})"
    elif scenario == "wrong_labels":
        swapped = {"mean systolic": expected["mean diastolic"], "mean diastolic": expected["mean systolic"]}
        messages[-1].content = json.dumps(swapped)
    elif scenario == "wrong_output":
        messages[-2].content = json.dumps({"mean systolic": 0, "mean diastolic": 0})
    assert not probe.score(case, messages, "python_exec")["passed"]


def test_printed_python_dicts_and_nested_citation_results_are_accepted():
    case, messages, expected = case_and_trace()
    messages[-2].content = repr({key: {"value": value, "unit": "mmHg", "rids": ["r1"]}
                                for key, value in expected.items()})
    assert probe.score(case, messages, "python_exec")["passed"]


def test_monthly_mean_fields_with_extra_count_and_citations_are_accepted():
    case = next(c for c in probe.cases() if c["id"] == "rhr_monthly")
    for field in ("mean", "monthly_mean_bpm"):
        result = {item["label"]: {field: item["value"], "n": 10, "rids": ["r1"]} for item in case["expect"]}
        assert probe.matches(case["expect"], json.dumps(result))


def test_boolean_and_nonfinite_numbers_are_rejected():
    expected = [{"label": "a count", "value": 1, "tol": 0}]
    for value in (True, float("nan"), float("inf")):
        assert not probe.matches(expected, json.dumps({"a count": value}))


async def test_dummy_input_reference_does_not_pass_changed_data_replay(monkeypatch):
    case, _, expected = case_and_trace()
    output = json.dumps(expected)
    calls = [
        {"name": "query_health_indicators", "id": "q1", "args": {"keywords": ["blood pressure"]}},
        {"name": "python_exec", "id": "c1", "args": {"code": "print(len(data)); print('literal result')"}},
        {"result_for": "c1", "content": output, "artifact": {"exit_code": 0, "source_tool_call_ids": ["q1"]}},
    ]
    record = {"case": case["id"], "arm": "python", "calls": calls,
              "score": {"passed": True, "answer_accurate": True, "calculated": True}}
    monkeypatch.setattr(probe, "run_python", lambda code, inputs, timeout: ExecuteResponse(output, 0))
    result = await probe.replay_case(record, case)
    assert not result["score"]["passed"]
    assert not result["replay"]["verified"]
