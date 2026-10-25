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
_UNIT = re.compile(r"[ \t]*([\wµμ%°/\[\]{][\wµμ%°/^*.\-\[\]{}]{0,47})")
_PROSE_AFTER_VALUE = frozenset({"to", "from", "between", "and", "on", "at", "in", "for",
                               "before", "after", "today", "this", "the", "was", "is"})
_CONSTANTS = re.compile(r"^\(constants:\s*(.*?)\)\s*$")
#: Keep numeric stats and observed-day trend values tied to their source row.
_FIELDS = ("value", "avg", "min", "max", "sum", "first", "last", "change",
           "first_third_mean", "last_third_mean", "delta")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+Z-]+)?|\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}:\d{2}(?::\d{2})?")
_GENERIC = re.compile(r"(?:\s|[:：*()=]|latest|previous|last|result|value|was|is|the|at|and|average|mean|maximum|minimum|sum|最新|上次|之前|结果|数值|是|为|均值|平均|最大|最小|总和)*", re.I)
_BINDING = re.compile(r"(?:\s|[:：*()=]|is|was|at|averaged|average|mean|maximum|minimum|sum|measured|recorded|result|value|fell|rose|from|to|by|and|的|是|为|均值|平均|测得|结果|数值|最大|最小|总和)*", re.I)


def _tolerance(target: float) -> float:
    """How close two numbers must be to be the same reading, in the value's own scale.

    The floor used to be 0.011 absolute, which for a concentration of 0.003 g/L makes every
    figure in the row "match": a row with min=0.002, avg=0.003, max=0.004 endorsed "0.01 g/L".
    The tolerance now follows the magnitude, with only a tiny floor for floating point.
    """
    return max(abs(target) * 0.002, 1e-9)


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


def _unit_after(text: str, start: int) -> tuple[int, str]:
    """The unit written after a number, through the wrappers a report puts around it.

    "(mg/dL)" and "**mg/dL**" are the same claim as "mg/dL": reading only the first token
    returns nothing for them, and an empty unit is accepted as "no claim", so a mmol/L row
    could be cited to a sentence that explicitly said mg/dL.
    """
    stripped = text[start:start + 40].lstrip()
    wrapper = len(text[start:start + 40]) - len(stripped)
    while stripped[:1] in ("(", "[", "*", "_", '"', "'", "``", "`"):
        stripped, wrapper = stripped[1:], wrapper + 1
    match = _UNIT.match(stripped)
    if match is None:
        return start, ""
    token = match.group(1).rstrip(".")
    if token.casefold() in _PROSE_AFTER_VALUE:
        return start, ""
    return start + wrapper + match.start(1) + len(token), token


def _binding_text(text: str) -> str:
    text = _DATE_TIME.sub("", text)
    out, cursor = [], 0
    for match in _NUM.finditer(text):
        if match.start() < cursor:
            continue
        end, unit = _unit_after(text, match.end())
        out.append(text[cursor:match.start()])
        out.append(" ")
        cursor = end if unit and normalize_unit(unit) else match.end()
    return "".join([*out, text[cursor:]]).strip()


def _date_matches(row: dict[str, Any], dates: set[str]) -> bool:
    bounds = {str(row.get(k) or "")[:10] for k in ("first_date", "last_date")} - {""}
    if bounds and len(bounds) == 2:
        return bounds == dates
    observed = {str(row.get(k) or "")[:10] for k in ("time", "date", "period")} - {""}
    return len(dates) == 1 and bool(dates & (observed | bounds))


def _unit_matches(row: dict[str, Any], unit: str) -> bool:
    if not unit:
        return True
    wanted = normalize_unit(unit)
    if wanted is None:
        return False
    recorded = normalize_unit(str(row.get("unit") or ""))
    # A generic count gains its entity only from the named metric, never from
    # dimensional equivalence between unrelated counted things.
    return wanted == recorded or (recorded == "{#}" and wanted.startswith("{") and wanted.endswith("}")
                                  and any(normalize_unit(n) == wanted for n in _aliases(row)))


#: The words an answer uses for each statistic a row reports.
_STATISTIC_WORDS = {
    "maximum": "max", "max": "max", "highest": "max", "peak": "max", "最高": "max", "最大": "max",
    "minimum": "min", "min": "min", "lowest": "min", "最低": "min", "最小": "min",
    "average": "avg", "mean": "avg", "averaged": "avg", "平均": "avg", "均值": "avg",
    "sum": "sum", "total": "sum", "总和": "sum", "合计": "sum",
    "median": "median",
}


def _statistic_before(window: str) -> str:
    """The field an answer is talking about, or "" when it names no statistic."""
    recent = window[-60:]
    found = [(recent.rfind(word), field) for word, field in _STATISTIC_WORDS.items() if word in recent]
    if not found:
        return ""
    position, field = max(found)
    return field


def _choose(before: str, context: str, unit: str, candidates: list[dict[str, Any]],
            names: set[str]) -> dict[str, Any] | None:
    # The field belongs in the key: an average and a minimum that happen to be the same number
    # are two claims about one row, and collapsing them kept whichever came last - which is how
    # "Body weight averaged 70" resolved to the row's minimum.
    candidates = list({(r["rid"], r.get("_field", ""), r["_value"], r.get("unit", "")): r
                       for r in candidates}.values())
    original_count = len({r["rid"] for r in candidates})
    dates = set(_DATE.findall(context))
    dates.update(f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
                 for y, m, d in re.findall(r"(\d{4})年(\d{1,2})月(\d{1,2})日", context))
    if dates:
        candidates = [r for r in candidates if _date_matches(r, dates)]
    window = re.sub(r"<[^>]+>", "", before).casefold()
    matched = [(window.rfind(name) + len(name), len(name), name) for name in names if name in window]
    name_end, _, nearest = max(matched, default=(-1, 0, ""))
    named = [r for r in candidates if nearest and nearest in _aliases(r)]
    if nearest:
        if not _BINDING.fullmatch(_binding_text(window[name_end:])):
            return None
        candidates = named
    elif original_count != 1 or not _GENERIC.fullmatch(_DATE_TIME.sub("", window).strip()):
        return None
    candidates = [r for r in candidates if _unit_matches(r, unit)]
    # A sentence that names a statistic means that field.
    statistic = _statistic_before(window)
    if statistic:
        by_field = [r for r in candidates if r.get("_field") == statistic]
        if not by_field:
            return None
        candidates = by_field
    return candidates[0] if len({r["rid"] for r in candidates}) == 1 else None


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
        end, unit = _unit_after(text, match.end())
        left = max([0, *(i + 1 for i in boundaries if i < match.start())])
        right = min([len(text), *(i for i in boundaries if i >= end)])
        before = text[max(left, match.start() - 120):match.start()]
        context = text[max(left, match.start() - 120):min(right, end + 80)]
        chosen = _choose(before, context, unit, candidates, names)
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
