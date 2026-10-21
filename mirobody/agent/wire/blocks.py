"""The blocks a turn streams, named the way LangChain names them.

`langchain_core.messages.content` defines a standard vocabulary for what a
model emits: ``text`` carries ``text``, ``reasoning`` carries ``reasoning``, a
``tool_call`` carries ``id`` / ``name`` / ``args``, and a tool's answer is a
``ToolMessage`` with ``tool_call_id`` and ``content``. This module uses those
names. What came before was ``reply`` / ``thinking`` / ``queryTitle`` /
``queryArguments`` / ``queryDetail`` / ``costStatistics``, invented here, so
every reader had to learn a private dialect to see the same five things.

Six block types have no LangChain equivalent because they are about the
stream rather than the model: ``start`` opens it with the id the answer will
be saved under, ``heartbeat`` keeps a proxy from closing an idle connection,
``notice`` is the system talking to the user, ``error`` is a fault the user is
told about, ``context`` reports actual summary-generation activity without
its contents, and ``end`` says why the turn stopped. ``interrupt`` is
LangGraph's: a paused run, with the pending call under its own name and
arguments.

Stdlib only. The chat layer imports this to read its stored transcripts, and
that must not drag LangChain into a process that only serves history.
"""

from __future__ import annotations

import json
from typing import Any

#: What the model produced. Same spelling as `langchain_core.messages.content`.
TEXT = "text"
REASONING = "reasoning"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
USAGE = "usage"

#: What the stream itself reports.
START = "start"
INTERRUPT = "interrupt"
#: A line the SYSTEM is telling the user ("that model is not configured, using
#: the default"), never the model's own words. It rode the reasoning channel
#: until 1.4.4, where a reader could not tell it from the model's trace;
#: `kernel.events.Notice` names that as the defect it exists to fix.
NOTICE = "notice"
CONTEXT = "context"
ERROR = "error"
END = "end"
HEARTBEAT = "heartbeat"

#: Why a turn ended. A closed set, because a client branches on it: `stop` is
#: the model finishing, `error` a fault the user was told about, `unavailable`
#: never having started, and `empty` finishing cleanly with nothing to show.
FINISH_STOP = "stop"
FINISH_ERROR = "error"
FINISH_UNAVAILABLE = "unavailable"
FINISH_EMPTY = "empty"

#: Blocks whose text continues the previous block of the same type rather than
#: starting a new one, and the field carrying it. A turn streams hundreds of
#: `text` blocks and stores one.
_CONTINUES = {TEXT: "text", REASONING: "reasoning"}


def merge(transcript: list[dict[str, Any]], block: dict[str, Any]) -> None:
    """Append `block` to `transcript`, joining it to the previous one when they
    are the same continuable type."""
    field = _CONTINUES.get(block.get("type", ""))
    if field and transcript and transcript[-1].get("type") == block.get("type"):
        transcript[-1][field] = transcript[-1].get(field, "") + block.get(field, "")
        return
    transcript.append(dict(block))


def answer_text(transcript: Any) -> str:
    """The visible answer in a stored transcript: its `text` blocks, joined.

    Reasoning and tool traffic are not the answer, which is what a session
    title and a shared conversation want.
    """
    if not isinstance(transcript, list):
        return ""
    return "".join(
        b.get("text", "") for b in transcript
        if isinstance(b, dict) and b.get("type") == TEXT
    )


def _upgrade_one(block: dict[str, Any]) -> dict[str, Any] | None:
    """One pre-1.4.4 block in today's vocabulary, or None to drop it."""
    kind = block.get("type")
    content = block.get("content")
    rest = {k: v for k, v in block.items() if k not in ("type", "content", "tool_id")}
    if kind == "reply":
        return {"type": TEXT, "text": content or ""}
    if kind == "thinking":
        return {"type": REASONING, "reasoning": content or ""}
    if kind == "queryTitle":
        return {"type": TOOL_CALL, "id": block.get("tool_id", ""), "name": content or "", "args": {}}
    if kind == "queryDetail":
        return {"type": TOOL_RESULT, "tool_call_id": block.get("tool_id", ""),
                "content": content, **rest}
    if kind == "costStatistics":
        return {"type": USAGE, **(content if isinstance(content, dict) else {})}
    if kind == "widget":
        payload = content if isinstance(content, dict) else block
        return {"type": INTERRUPT, "name": "ask_user",
                "args": {"question": payload.get("question", ""),
                         "options": (payload.get("config") or {}).get("options") or []}}
    # `queryArguments` is folded into the preceding `tool_call` by `upgrade`,
    # which can see both blocks; on its own it becomes nothing.
    if kind == "queryArguments":
        return None
    return block


def _old_tool_args(transcript: list) -> dict[str, dict]:
    """The arguments a pre-1.4.4 transcript kept in its own block, by tool id.

    Old `queryTitle` carried the name and nothing else, so dropping
    `queryArguments` left an upgraded `tool_call` reading "called it with no
    arguments". `events_bridge` wrote the whole `json.dumps(arguments)` in one
    block despite the delta name, but concatenate anyway: two fragments cost
    nothing and a single one is unchanged.
    """
    raw: dict[str, str] = {}
    for b in transcript:
        if isinstance(b, dict) and b.get("type") == "queryArguments":
            key = b.get("tool_id", "")
            content = b.get("content")
            if isinstance(content, str):
                raw[key] = raw.get(key, "") + content
    out = {}
    for key, text in raw.items():
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            out[key] = parsed
    return out


def upgrade(transcript: Any) -> Any:
    """A stored transcript in today's vocabulary, whenever it was written.

    Rows written before 1.4.4 hold `reply` / `thinking` / `queryTitle` /
    `queryDetail` / `costStatistics` / `widget`, and a block type a client does
    not recognise renders as nothing at all. So the rename happens once, where
    a transcript is LOADED, rather than in every reader.
    """
    if not isinstance(transcript, list):
        return transcript
    args = _old_tool_args(transcript)
    upgraded = [_upgrade_one(b) if isinstance(b, dict) else b for b in transcript]
    out = [b for b in upgraded if b is not None]
    for b in out:
        if isinstance(b, dict) and b.get("type") == TOOL_CALL and not b.get("args"):
            found = args.get(b.get("id"))
            if found is not None:
                b["args"] = found
    return out
