"""An aggregate is citable, and a unit that does not normalize must not erase the citation.

The numbers a person reads are often computed - "my average was 6,978", "the highest was
11,846" - and a figure nobody can trace back is exactly what the record is supposed to prevent.
These tests pin the three cases that used to leave such a number uncited.
"""
from __future__ import annotations

from mirobody.agent.chat.citations_auto import attach_citations, rows_from_result

AGGREGATE = "(constants: rid=r1, count=30, avg=6978.5, max=11846, unit=count)"


def test_a_stats_aggregate_is_citable() -> None:
    rows = rows_from_result(AGGREGATE)
    text, cited, uncited = attach_citations("Average steps 6978.5.", rows)
    assert "6978.5<cite>[r1]</cite>" in text
    assert (cited, uncited) == (1, [])


def test_two_figures_from_one_row_cite_that_row() -> None:
    rows = rows_from_result(AGGREGATE)
    text, cited, _ = attach_citations("Average steps 6978.5, maximum 11846.", rows)
    assert text.count("<cite>[r1]</cite>") == 2
    assert cited == 2


def test_a_unit_in_another_language_keeps_the_citation() -> None:
    rows = rows_from_result("(constants: rid=r3, avg=57.7, unit=bpm)")
    text, cited, _ = attach_citations("静息心率 57.7 次/分。", rows)
    assert cited == 1, "a non-normalizing unit must not drop the source"
    assert "57.7" in text and "<cite>" in text


def test_a_trend_field_is_citable() -> None:
    rows = rows_from_result("(constants: rid=r7, first_third_mean=7.659, last_third_mean=6.963, delta=-0.696)")
    text, cited, _ = attach_citations("It fell from 7.659 to 6.963 between the thirds.", rows)
    assert cited == 2
    assert text.count("<cite>[r7]</cite>") == 2


def test_a_number_no_row_supports_stays_uncited() -> None:
    rows = rows_from_result(AGGREGATE)
    text, cited, uncited = attach_citations("Average steps 6978.5, about 12000 on the best day.", rows)
    assert "12000<cite>" not in text
    assert (cited, uncited) == (1, ["12000"])
