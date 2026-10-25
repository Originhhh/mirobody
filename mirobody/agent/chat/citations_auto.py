"""Attach a source only when the visible claim can be attributed unambiguously.

A matching number alone does not establish provenance. Automatic citations require
compatible units and a named source metric or an unambiguous generic continuation;
explicit encounter dates must agree too. Unknown names and ambiguous rows stay uncited.
Fenced data and existing citations are preserved. Uncited figures are an audit signal,
not a verdict that a derived number is false.
"""
from __future__ import annotations

import re
from typing import Any

from mirobody.units import normalize_unit

_NUM = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?")
_CITE = re.compile(r"<cite>.*?</cite>", re.S)
_FENCE = re.compile(r"```.*?```", re.S)
_RID = re.compile(r"^r\d+$")
_UNIT = re.compile(r"\s?(?:[a-zA-Zµμ%/][a-zA-Zµμ%^0-9/\-.]{0,23})?")
_CONSTANTS = re.compile(r"^\(constants:\s*(.*?)\)\s*$")
_FIELDS = ("value", "avg", "min", "max", "sum", "first", "last", "change")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+Z-]+)?|\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}:\d{2}(?::\d{2})?")
_GENERIC = re.compile(r"(?:\s|[:：*()=]|latest|previous|last|result|value|was|is|the|at|and|最新|上次|之前|结果|数值|是|为)*", re.I)
_BINDING = re.compile(r"(?:\s|[:：*()=]|is|was|at|averaged|average|measured|recorded|result|value|的|是|为|均值|平均|测得|结果|数值)*", re.I)


def _tolerance(target: float) -> float:
    return max(0.011, abs(target) * 0.002)


def _cells(line: str) -> list[str]:
    cells, current = [], []
    index = 0
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line):
            index += 1
            current.append("\n" if line[index] == "n" else line[index])
        elif char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    return [*cells, "".join(current).strip()]


def rows_from_result(content: str) -> list[dict[str, Any]]:
    """Decode compact record tables, hoisted constants and aggregate value fields."""
    records: list[dict[str, str]] = []
    constants: dict[str, str] = {}
    header: list[str] | None = None

    def flush() -> None:
        if header is None and _RID.fullmatch(constants.get("rid", "")):
            records.append(dict(constants))

    for line in [*content.splitlines(), ""]:
        stripped = line.strip()
        if not stripped:
            flush()
            constants, header = {}, None
            continue
        match = _CONSTANTS.match(stripped)
        if match:
            flush()
            constants, header = {}, None
            for item in re.split(r",\s+(?=[A-Za-z_]\w*=)", match.group(1)):
                if "=" in item:
                    key, value = item.split("=", 1)
                    if key in constants:
                        constants = {}
                        break
                    constants[key] = value
            continue
        parts = _cells(stripped)
        if "rid" in parts and any(k in parts or k in constants for k in _FIELDS):
            header = parts
            continue
        if header is not None and len(parts) == len(header):
            row = {**constants, **dict(zip(header, parts, strict=True))}
            if _RID.fullmatch(row.get("rid", "")):
                records.append(row)

    parsed = []
    for row in records:
        for field in _FIELDS:
            try:
                value = float(row[field])
            except (KeyError, TypeError, ValueError):
                continue
            parsed.append({**row, "_value": value, "_field": field})
    return parsed


def _aliases(row: dict[str, Any]) -> set[str]:
    return {str(row.get(key) or "").strip().casefold() for key in ("name", "indicator")} - {""}


def _choose(before: str, context: str, unit: str, candidates: list[dict[str, Any]],
            names: set[str]) -> dict[str, Any] | None:
    candidates = list({(r["rid"], r["_value"], r.get("unit", "")): r for r in candidates}.values())
    original_count = len(candidates)
    dates = set(_DATE.findall(context))
    dates.update(f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
                 for y, m, d in re.findall(r"(\d{4})年(\d{1,2})月(\d{1,2})日", context))
    if len(dates) > 1:
        return None
    if dates:
        candidates = [r for r in candidates if any(d in str(r.get(k, "")) for d in dates for k in ("time", "date", "period"))]
    window = re.sub(r"<[^>]+>", "", before).casefold()
    matched = [(window.rfind(name) + len(name), len(name), name) for name in names if name in window]
    name_end, _, nearest = max(matched, default=(-1, 0, ""))
    named = [r for r in candidates if nearest and nearest in _aliases(r)]
    if nearest:
        if not _BINDING.fullmatch(_DATE_TIME.sub("", window[name_end:]).strip()):
            return None
        candidates = named
    elif original_count != 1 or not _GENERIC.fullmatch(_DATE_TIME.sub("", window).strip()):
        return None
    candidates = [r for r in candidates if normalize_unit(unit) == normalize_unit(str(r.get("unit") or ""))]
    return candidates[0] if len(candidates) == 1 else None


def attach_citations(text: str, rows: list[dict[str, Any]]) -> tuple[str, int, list[str]]:
    """Return rewritten text, inserted citation count and figures left unattributed."""
    if not text:
        return text, 0, []
    regions = [m.span() for pattern in (_FENCE, _CITE, _DATE_TIME) for m in pattern.finditer(text)]
    names = {name for row in rows for name in _aliases(row)}
    out: list[str] = []
    cursor, cited = 0, 0
    uncited = []
    boundaries = [m.start() for m in re.finditer(r"[\n;；，。!?,]|\.(?!\d)|(?<!\d)\.", text)]
    for match in _NUM.finditer(text):
        if match.start() < cursor or any(start <= match.start() < end for start, end in regions):
            continue
        if re.match(r"[A-Za-z]", text[match.end():]):
            continue
        value = float(match.group().replace(",", ""))
        candidates = [r for r in rows if abs(value - r["_value"]) <= _tolerance(r["_value"])]
        end = match.end()
        unit_match = _UNIT.match(text, end)
        if unit_match:
            end = unit_match.end()
            while end > match.end() and text[end - 1] == ".":
                end -= 1
        left = max([0, *(i + 1 for i in boundaries if i < match.start())])
        right = min([len(text), *(i for i in boundaries if i >= end)])
        before = text[max(left, match.start() - 120):match.start()]
        context = text[max(left, match.start() - 120):min(right, end + 80)]
        chosen = _choose(before, context, text[match.end():end].strip(), candidates, names)
        out.append(text[cursor:end])
        if chosen is not None:
            if not text[end:end + 6].startswith("<cite>"):
                out.append(f"<cite>[{chosen['rid']}]</cite>")
                cited += 1
        else:
            uncited.append(match.group())
        cursor = end
    out.append(text[cursor:])
    return "".join(out), cited, uncited


def collect_rows(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for block in transcript:
        if block.get("type") == "tool_result" and block.get("status") in (None, "ok"):
            rows.extend(rows_from_result(str(block.get("content") or "")))
    return rows
