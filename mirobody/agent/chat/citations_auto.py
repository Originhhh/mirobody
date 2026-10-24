"""Cite what the turn actually read: attach rids to the numbers in the answer.

Why this is a harness job, not a training job: the base model already answers the
questions correctly and naturally (facts 74/100) but cites almost nothing (17/84 in
format), while our SFT that teaches `<cite>` drops facts from 74 to 63. The citation is
a mechanical claim about where a number came from, and the turn holds every row the
tools returned - so it can be attached deterministically, with no weight change.

`attach_citations` walks the answer's numbers and, for each one that matches a row of a
record result (value within tolerance), appends `<cite>[rid]</cite>` immediately after
it. Numbers with no matching row are left alone: they are either prose, or exactly the
unsupported numbers the review is meant to catch.
"""
from __future__ import annotations

import re
from typing import Any

_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_RID = re.compile(r"\[(r\d+)\]")
_TABLE_HEAD = re.compile(r"^rid\|", re.M)
#: A unit that must stay glued to the number a citation follows.
_UNIT = re.compile(r"\s?(?:[a-zA-Zµμ%/][a-zA-Zµμ%^0-9/\-.]{0,11})?")


def rows_from_result(content: str) -> list[dict[str, str]]:
    """Rows of a `rid|indicator|...` table (the compact render of a record result)."""
    if not content or "|" not in content:
        return []
    lines = [line for line in content.splitlines() if line.strip()]
    head_index = next((i for i, line in enumerate(lines) if line.startswith("rid|")), None)
    if head_index is None:
        return []
    columns = lines[head_index].split("|")
    if "value" not in columns:
        return []
    rows = []
    for line in lines[head_index + 1:]:
        parts = line.split("|")
        if len(parts) != len(columns):
            continue
        row = dict(zip(columns, parts))
        try:
            row["_value"] = float(row["value"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append(row)
    return rows


def _tolerance(target: float) -> float:
    return max(0.011, abs(target) * 0.002)


def attach_citations(text: str, rows: list[dict[str, str]]) -> tuple[str, int, int]:
    """Return (text with citations, cited count, uncited numeric count)."""
    if not text or not rows:
        return text, 0, 0
    by_value: dict[float, str] = {}
    for row in rows:
        rid = row.get("rid")
        if rid:
            by_value.setdefault(row["_value"], rid)

    out: list[str] = []
    cited = uncited = 0
    cursor = 0
    for match in _NUM.finditer(text):
        raw = match.group()
        # Skip digits inside dates, times, rids and already-cited spans.
        before = text[max(0, match.start() - 12):match.start()]
        after = text[match.end():match.end() + 12]
        if re.search(r"\d[-:/年.]$", before) or re.match(r"[-:/年月日]", after):
            continue
        if _RID.search(after):
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        hit = next((rid for target, rid in by_value.items() if abs(value - target) <= _tolerance(target)), None)
        # Keep the number adjacent to its unit: "15.8 g/dL<cite>[r2]</cite>", never
        # "15.8<cite>[r2]</cite> g/dL" - a citation inside the value swallows the unit.
        end = match.end()
        unit = _UNIT.match(text, end)
        if unit:
            end = unit.end()
        out.append(text[cursor:end])
        already = text[end:end + 6].startswith("<cite>")
        if hit and not already:
            out.append(f"<cite>[{hit}]</cite>")
            cited += 1
        else:
            uncited += 1
        cursor = end
    out.append(text[cursor:])
    return "".join(out), cited, uncited


def collect_rows(transcript: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for block in transcript:
        if block.get("type") != "tool_result":
            continue
        if block.get("status") not in (None, "ok"):
            continue
        rows.extend(rows_from_result(str(block.get("content") or "")))
    return rows
