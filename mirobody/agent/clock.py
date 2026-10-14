"""The agent's own "now": the real clock, or an eval-only override.

A frozen eval run plays back old data and asks a question dated after it, so
the system clock is the wrong answer: a run against data ending 2026-09-01,
executed on 2026-10-14, asked "the last 30 days" against the real date, found
nothing and answered "cannot tell" (the bug this module fixes). `as_of` pins
the request's own "today" instead, carried on a `ContextVar` so one request's
moment never leaks into another running concurrently.

Gated by `EVAL_AS_OF_ENABLED` (`chat/service.py`): off by default, so an
ordinary deployment cannot have its answers dated by a client-supplied value.
Only the agent's OWN reads of "now" go through here: `prompt.turn_note`,
`prompt.build_system_prompt`, `hitl.parse_date_answer`, and a tool's
`RecordTool._clock()`. A stored wall-clock fact, such as `th_messages.created_at`
or a file's `upload_time`, is not routed through this and must not be: that is
when the row was actually written, eval or not.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, time, tzinfo

from mirobody.kernel.series import zone as _zone
from mirobody.utils import safe_read_cfg

#: The pinned moment for the request running in this task, if any. Set once
#: per chat turn (`chat/turn.py::run`) and seen by every tool call and prompt
#: render that turn makes, because `asyncio.create_task` copies the context a
#: background task starts with.
_AS_OF: ContextVar[datetime | None] = ContextVar("agent_as_of", default=None)


def now(tz: str | tzinfo | None = None) -> datetime:
    """The agent's current moment, in `tz` (an IANA name, a `UTC±HH:MM`
    offset, a `tzinfo`, or none for naive local time): the pinned `as_of` for
    this request if one is set, else the real clock.

    Same shape as `datetime.now(tz)` so every call site this replaces
    (`datetime.now(zone(timezone))`, `datetime.now(UTC)`, `datetime.now()`)
    keeps its own timezone handling; only where the moment comes from changes.
    """
    zone = tz if tz is None or isinstance(tz, tzinfo) else _zone(tz)
    pinned = _AS_OF.get()
    if pinned is not None:
        return pinned.astimezone(zone) if zone is not None else pinned
    return datetime.now(zone)


@contextmanager
def as_of(moment: datetime) -> Generator[None, None, None]:
    """Pin `now()` to `moment` (must be timezone-aware) for this block.

    Resets to whatever the context held before, so nesting and concurrent
    tasks (each its own copy of the context) cannot see each other's pin.
    """
    if moment.tzinfo is None:
        raise ValueError("as_of moment must be timezone-aware")
    token = _AS_OF.set(moment)
    try:
        yield
    finally:
        _AS_OF.reset(token)


def parse_as_of(value: str, tz: str | tzinfo) -> datetime:
    """An `as_of` request field as an aware moment.

    A bare ISO date (`2025-10-18`) becomes noon that day in `tz`: midnight
    would read as "yesterday evening" the moment a positive UTC offset is
    involved, and this is a driver picking a day, not a precise instant. An
    ISO datetime is used as given; naive (no offset) takes `tz`, the same way
    the date form does. Anything else raises `ValueError`, quoting the input,
    for the caller to turn into a client-facing rejection.
    """
    text = (value or "").strip()
    if not text:
        raise ValueError("as_of must not be empty")
    zone = tz if isinstance(tz, tzinfo) else _zone(tz)
    try:
        return datetime.combine(date.fromisoformat(text), time(12, 0), zone)
    except ValueError:
        pass
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as e:
        raise ValueError(f"as_of is not an ISO date or datetime: {text!r}") from e
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=zone)


def eval_as_of_enabled() -> bool:
    """Whether this server honours a chat request's `as_of` override.

    Read the same way other boolean flags here are (`ENABLE_INDICATOR_EXTRACTION`,
    `GLOBAL_FILE_CACHE_ENABLED`): `safe_read_cfg` so it works with or without a
    loaded `Config`, off unless explicitly turned on.
    """
    return safe_read_cfg("EVAL_AS_OF_ENABLED", "").strip().upper() in ("TRUE", "1", "YES", "ON")
