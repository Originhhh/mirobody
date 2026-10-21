"""LangGraph's stream → the blocks in `blocks.py`.

These decisions are this renderer's own, and they are why it reads the stream
rather than taking `events_bridge`'s finished events:

* only the ``model`` and ``tools`` nodes are user-visible, so a
  summarisation-internal model call never reaches a client;
* a tool result's content passes through VERBATIM, multimodal blocks
  included, with the status read off its artifact rather than off its text;
* ordinary text is classified only when its model step completes, so tool
  narration never becomes an answer. Native reasoning still streams at once.

Interrupts are the caller's (`hitl.interrupt_block`), not this module's.
"""

import logging
from typing import Any
from collections.abc import Iterator

from langchain_core.callbacks import AsyncCallbackHandler
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from deepagents.middleware.summarization import SUMMARIZATION_EVENT_KEY

from .blocks import CONTEXT, REASONING, TEXT, TOOL_CALL, TOOL_RESULT
from .events_bridge import (
    ToolCallCompleted,
    ToolCallStarted,
    is_tool_message,
    result_status,
    content_text,
    tool_call_events,
)
from mirobody.agent.models.usage import UsageAccumulator
from mirobody.agent.models.messages import message_reasoning

from mirobody.kernel.ops import is_driver_exception

logger = logging.getLogger(__name__)


# LangGraph stream node names that contribute to the final user-visible output.
FINAL_OUTPUT_NODES: set[str] = {"tools", "model"}


class _InlineThinking:
    """Keep split inline thinking delimiters out of the answer channel."""

    def __init__(self) -> None:
        self.pending = ""
        self.thinking = False

    def feed(self, text: str, *, final: bool = False) -> Iterator[tuple[str, str]]:
        self.pending += text
        while self.pending:
            tag = "</think>" if self.thinking else "<think>"
            at = self.pending.find(tag)
            kind = REASONING if self.thinking else TEXT
            if at >= 0:
                if at:
                    yield kind, self.pending[:at]
                self.pending = self.pending[at + len(tag):]
                self.thinking = not self.thinking
                continue
            keep = 0 if final else next((n for n in range(len(tag) - 1, 0, -1)
                                        if self.pending.endswith(tag[:n])), 0)
            ready = self.pending[:-keep] if keep else self.pending
            self.pending = self.pending[-keep:] if keep else ""
            if ready:
                yield kind, ready
            break


class TurnStream:
    """Native thinking streams immediately; ordinary text waits for its step.

    Providers can send prose before a tool-call delta. Its channel is unknowable
    until the model message completes: publishing it early made tool narration
    part of the persisted answer and any speech consuming it. One instance per
    turn buffers that prose, labels tool-step prose as reasoning, and publishes
    only completed non-tool replies as text. It does not invent reasoning when
    an endpoint withholds it. Tool events retain their existing wire shape.
    """

    def __init__(self) -> None:
        self._inline = _InlineThinking()
        self._text: list[str] = []
        self._reasoning = ""
        self._inline_reasoning = ""
        self._model_ids: set[str] = set()
        self._tool_ids: set[str] = set()
        self._observed_calls: set[str] = set()
        self._result_ids: set[str] = set()
        self.summary_cutoffs: list[int] = []

    def _messages(self, stream_event: Any) -> Iterator[dict[str, Any]]:
        chunk, metadata = stream_event
        if (metadata.get("lc_source") == "summarization"
                or type(chunk).__name__ != "AIMessageChunk"
                or metadata.get("langgraph_node") not in FINAL_OUTPUT_NODES
                or getattr(chunk, "id", None) in self._model_ids):
            return
        reasoning = message_reasoning(chunk)
        if reasoning:
            self._reasoning += reasoning
            yield {"type": REASONING, "reasoning": reasoning}
        for kind, text in self._inline.feed(content_text(getattr(chunk, "content", ""))):
            if kind == REASONING:
                self._inline_reasoning += text
                yield {"type": REASONING, "reasoning": text}
            else:
                self._text.append(text)

    def _completed_model(self, message: Any) -> Iterator[dict[str, Any]]:
        mid = getattr(message, "id", None)
        if mid and mid in self._model_ids:
            return
        if mid:
            self._model_ids.add(mid)
        # Some integrations return a completed message without token callbacks.
        # Reconstruct its visible text with the same parser, never str(blocks).
        raw = content_text(getattr(message, "content", ""))
        parsed = list(_InlineThinking().feed(raw, final=True))
        text = "".join(value for kind, value in parsed if kind == TEXT)
        if not raw:
            text = "".join(self._text)
        reason = message_reasoning(message)
        if reason.startswith(self._reasoning):
            remaining = reason[len(self._reasoning):]
            if remaining:
                yield {"type": REASONING, "reasoning": remaining}
        inline_reason = "".join(value for kind, value in parsed if kind == REASONING)
        if inline_reason.startswith(self._inline_reasoning):
            remaining = inline_reason[len(self._inline_reasoning):]
            if remaining:
                yield {"type": REASONING, "reasoning": remaining}
        work = bool(getattr(message, "tool_calls", None) or getattr(message, "invalid_tool_calls", None))
        self._observed_calls.update(call["id"] for call in
                                    [*(getattr(message, "tool_calls", None) or []),
                                     *(getattr(message, "invalid_tool_calls", None) or [])] if call.get("id"))
        if text:
            yield {"type": REASONING, "reasoning": text} if work else {"type": TEXT, "text": text}
        self._inline = _InlineThinking()
        self._text = []
        self._reasoning = ""
        self._inline_reasoning = ""

    def _updates(self, update: Any, trace_id: str | None) -> Iterator[dict[str, Any]]:
        for node, value in update.items():
            if node not in FINAL_OUTPUT_NODES or not isinstance(value, dict):
                continue
            event = value.get(SUMMARIZATION_EVENT_KEY)
            cutoff = event.get("cutoff_index") if isinstance(event, dict) else None
            if type(cutoff) is int and cutoff > max(self.summary_cutoffs, default=0):
                self.summary_cutoffs.append(cutoff)
            messages = value.get("messages") or []
            if node == "model" and any(getattr(message, "id", None) == REMOVE_ALL_MESSAGES for message in messages):
                # LangChain repairs invalid calls by replacing all history in
                # this update. Old answers are state, not new presentation.
                last = max((i for i, message in enumerate(messages) if getattr(message, "type", "") == "ai"), default=-1)
                repaired = [message for message in messages[:last] if is_tool_message(message)
                            and message.tool_call_id in self._observed_calls]
                messages = [*repaired, *messages[last:]] if last >= 0 else []
            for message in messages:
                if node == "model" and getattr(message, "type", "") == "ai":
                    yield from self._completed_model(message)
                    names: dict[str, str] = {}
                    for event in tool_call_events(message, self._tool_ids):
                        if isinstance(event, ToolCallStarted):
                            names[event.call_id] = event.name
                            logger.info("[Tool Call] trace_id=%s tool_name=%s tool_id=%s",
                                        trace_id, event.name, event.call_id)
                        elif isinstance(event, ToolCallCompleted):
                            yield {"type": TOOL_CALL, "id": event.call_id,
                                   "name": names.pop(event.call_id, ""), "args": event.arguments}
                elif is_tool_message(message):
                    if message.tool_call_id in self._result_ids:
                        continue
                    self._result_ids.add(message.tool_call_id)
                    logger.info("[Tool Result] trace_id=%s tool_id=%s result_len=%d",
                                trace_id, message.tool_call_id, len(str(message.content)))
                    yield {"type": TOOL_RESULT, "tool_call_id": message.tool_call_id,
                           "content": message.content, **result_status(message)}

    def blocks(self, stream_type: str, stream_event: Any,
               trace_id: str | None = None) -> Iterator[dict[str, Any]]:
        """Render one graph event; malformed events carry only type names in logs."""
        try:
            if stream_type == "messages":
                yield from self._messages(stream_event)
            elif stream_type == "updates":
                yield from self._updates(stream_event, trace_id)
            elif stream_type == "custom" and isinstance(stream_event, dict):
                # Custom graph data is otherwise internal. Only this fixed,
                # content-free lifecycle reaches the transcript.
                if (stream_event.get("type") == CONTEXT
                        and stream_event.get("phase") == "summarization"
                        and stream_event.get("status") in {"started", "completed", "failed"}
                        and isinstance(stream_event.get("id"), str)):
                    yield {key: stream_event[key] for key in ("type", "phase", "status", "id")}
        except Exception as exc:
            logger.error("stream rendering failed: error_type=%s stream_type=%s event_type=%s trace_id=%s",
                         type(exc).__name__, stream_type, type(stream_event).__name__, trace_id,
                         exc_info=not is_driver_exception(exc))


class TokenUsageCallback(AsyncCallbackHandler):
    """Feeds every model call's normalised ``usage_metadata`` into one
    `usage.UsageAccumulator` for the turn. The provider-specific parsing that
    used to live here (``llm_output.token_usage``, Anthropic's
    ``cache_read_input_tokens``, OpenAI's ``prompt_tokens_details``) is what
    LangChain's ``usage_metadata`` already normalises: see `agent/models/usage.py`."""

    def __init__(self):
        self.usage = UsageAccumulator()
        self.summary_calls_started = 0
        self.summary_calls_completed = 0
        self.summary_calls_failed = 0
        self._summary_runs: set[str] = set()

    async def on_chat_model_start(self, serialized, messages, **kwargs):
        if (kwargs.get("metadata") or {}).get("lc_source") == "summarization":
            self.summary_calls_started += 1
            self._summary_runs.add(str(kwargs.get("run_id")))

    async def on_llm_error(self, error, **kwargs):
        run_id = str(kwargs.get("run_id"))
        if run_id in self._summary_runs:
            self._summary_runs.remove(run_id)
            self.summary_calls_failed += 1

    def context(self, presentation: TurnStream, model: Any) -> dict[str, Any]:
        """Counts only: no generated summary or health value leaves this receipt."""
        return {
            "summaries": len(presentation.summary_cutoffs),
            "summary_cutoffs": presentation.summary_cutoffs,
            "summary_calls_started": self.summary_calls_started,
            "summary_calls_completed": self.summary_calls_completed,
            "summary_calls_failed": self.summary_calls_failed,
            "input_window_tokens": (getattr(model, "profile", None) or {}).get("max_input_tokens"),
        }

    async def on_llm_end(self, response, **kwargs):
        run_id = str(kwargs.get("run_id"))
        if run_id in self._summary_runs:
            self._summary_runs.remove(run_id)
            self.summary_calls_completed += 1
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                usage = getattr(message, "usage_metadata", None) if message is not None else None
                if usage:
                    self.usage.add(usage)
        cache_read_tokens, cache_creation_tokens = self.usage.cache_read, self.usage.cache_creation
        logger.info(
            "[TokenUsage] input_tokens=%d output_tokens=%d cache_read_tokens=%d cache_creation_tokens=%d",
            self.usage.input_tokens, self.usage.output_tokens,
            cache_read_tokens, cache_creation_tokens,
        )
