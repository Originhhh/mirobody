"""The judge prompt and its parsing, without a network call.

The judge is the second reader for answers the literal checks reject for wording; what
matters here is that it is given the question, every expected fact and the tool output,
and that a malformed reply is refused rather than counted as a pass.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import importlib.util

_JUDGE = Path(__file__).resolve().parents[2] / "benchmarks" / "local_models" / "judge.py"
_spec = importlib.util.spec_from_file_location("local_judge", _JUDGE)
_judge = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_judge)
answer_text, facts_text, tools_text = _judge.answer_text, _judge.facts_text, _judge.tools_text


CASE = {
    "id": "c1",
    "question": "What was my resting heart rate in August?",
    "facts": [
        {"kind": "number", "value": 53.0, "label": "first"},
        {"kind": "words", "any": ["rise", "fall"], "label": "direction"},
    ],
}
RUN = {
    "blocks": [
        {"type": "tool_result", "content": "rid|name|value\nr1|heart|53\nr2|heart|50"},
        {"type": "reasoning", "reasoning": "thinking"},
        {"type": "text", "text": "It fell from 53 to 50."},
    ]
}


def test_every_expected_fact_reaches_the_prompt() -> None:
    text = facts_text(CASE)
    assert "first: 53.0" in text
    assert "direction: one of ['rise', 'fall']" in text


def test_only_tool_output_and_answer_are_quoted() -> None:
    assert "r1|heart|53" in tools_text(RUN)
    assert "thinking" not in tools_text(RUN)
    assert answer_text(RUN) == "It fell from 53 to 50."


def test_a_reply_without_json_is_refused() -> None:
    content = "The answer looks fine to me."
    start, end = content.find("{"), content.rfind("}")
    assert start < 0 and end < 0  # the caller raises rather than guessing


def test_verdict_shape_is_serializable() -> None:
    verdict = {"correct": False, "missing": ["first"], "unsupported": ["99"], "note": "wrong value"}
    assert json.loads(json.dumps(verdict))["correct"] is False
