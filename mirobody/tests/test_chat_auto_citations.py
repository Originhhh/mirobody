"""The citations a chat answer carries about its own numbers.

These are the properties a reader relies on: the rid is the one the tool minted for the
row the value came from, the unit stays beside its number, dates are not mistaken for
values, and a number no row supports is left uncited for review.
"""
from __future__ import annotations

from mirobody.agent.chat.citations_auto import attach_citations, collect_rows, rows_from_result

TABLE = """(constants: unit=kg)
rid|indicator|name|time|value|unit
r3|Body weight|体重|2026-08-31|69.5|kg
r4|Body weight|体重|2026-08-30|69.7|kg"""


def test_value_is_cited_after_its_unit() -> None:
    rows = rows_from_result(TABLE)
    text, cited, uncited = attach_citations("最新体重 69.5 kg，上次 69.7 kg。", rows)
    assert "69.5 kg<cite>[r3]</cite>" in text
    assert "69.7 kg<cite>[r4]</cite>" in text
    assert (cited, uncited) == (2, [])


def test_dates_are_not_cited() -> None:
    rows = rows_from_result(TABLE)
    text, cited, _ = attach_citations("2026-08-31 与 2026-08-30 的体重是 69.5 kg。", rows)
    assert "2026-08-31<cite>" not in text
    assert cited == 1


def test_a_number_no_row_supports_stays_uncited() -> None:
    rows = rows_from_result(TABLE)
    text, cited, uncited = attach_citations("体重涨了 3.2 kg，最新 69.5 kg。", rows)
    assert "3.2<cite>" not in text
    assert (cited, uncited) == (1, ["3.2"])


def test_an_existing_citation_is_not_duplicated() -> None:
    rows = rows_from_result(TABLE)
    text, cited, _ = attach_citations("体重 69.5 kg<cite>[r3]</cite>。", rows)
    assert text.count("<cite>") == 1
    assert cited == 0


def test_rows_come_from_every_result_of_the_turn() -> None:
    transcript = [
        {"type": "tool_result", "status": "ok", "content": TABLE},
        {"type": "text", "text": "ignored"},
    ]
    assert len(collect_rows(transcript)) == 2


def test_a_table_without_a_value_column_is_not_a_citation_source() -> None:
    assert rows_from_result("rid|indicator|time\nr1|x|2026-01-01") == []
