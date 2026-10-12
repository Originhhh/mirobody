"""Keep genotype tool rows out of DeepAgents summarization and history files."""

from __future__ import annotations

from typing import Any

from deepagents.middleware.summarization import (
    SUMMARIZATION_EVENT_KEY,
    SUMMARIZATION_SESSION_ID_KEY,
    SummarizationMiddleware as DeepSummarizationMiddleware,
    compute_summarization_defaults,
)
from langchain_core.messages import HumanMessage

from mirobody.agent.middleware.genotype_row_guard import (
    GenotypeRowGuardMiddleware,
    redact_genotype_history,
)

_SAFE_SUMMARY = "Earlier conversation context was omitted because it may include genotype results. Query the active upload again."
_SAFE_MARKER = "genotype_rows_redacted"


#: A model with at most this input window is a local one (the `local` entry
#: declares 48000).
_LOCAL_WINDOW = 65536

#: Recent context a compaction keeps for a local model. deepagents
#: keeps 10% of the window, 4.8k of 48k: less than the report pages the turn had
#: just read, so the model read them again.
_MIN_KEEP_TOKENS = 12000


def _is_local(model: Any) -> bool:
    window = (getattr(model, "profile", None) or {}).get("max_input_tokens")
    return isinstance(window, int) and window <= _LOCAL_WINDOW


def history_path_for(model: Any, file_path: str | None) -> str | None:
    """The history file a compaction summary names: none for a local model.
    Told the path, MiniCPM5-2B read it page by page, each read compacting the
    context again and lengthening the file (270 to 1,653 lines) until the turn
    ran out. No cloud model read it in the 570 answers of 2026-10-10."""
    return None if _is_local(model) else file_path


def _defaults(model: Any) -> dict:
    defaults = compute_summarization_defaults(model)
    if _is_local(model):
        defaults = {**defaults, "keep": ("tokens", _MIN_KEEP_TOKENS)}
    return defaults


class GenotypeSafeSummarizationMiddleware(DeepSummarizationMiddleware):
    """Replace the DeepAgents core slot while preserving its normal thresholds."""

    def __init__(self, model: Any, backend: Any, guard: GenotypeRowGuardMiddleware) -> None:
        super().__init__(model=model, backend=backend, **_defaults(model))
        self._genotype_guard = guard

    @property
    def name(self) -> str:
        # DeepAgents replaces a core middleware only when the names match.
        return "SummarizationMiddleware"

    def _safe_request(self, request: Any) -> Any:
        messages, *_ = self._genotype_guard._guard_messages(request.messages)
        messages = [
            HumanMessage(content=_SAFE_SUMMARY,
                         additional_kwargs={"lc_source": "summarization", _SAFE_MARKER: True})
            if isinstance(message, HumanMessage)
            and message.additional_kwargs.get("lc_source") == "summarization"
            and not message.additional_kwargs.get(_SAFE_MARKER)
            else message
            for message in messages
        ]
        event = request.state.get(SUMMARIZATION_EVENT_KEY)
        if not isinstance(event, dict):
            return request.override(messages=messages)
        summary = event.get("summary_message")
        if getattr(summary, "additional_kwargs", {}).get(_SAFE_MARKER):
            return request.override(messages=messages)
        # Old checkpoints may contain only the summary and its history path;
        # the raw tool message can already have been pruned from messages.
        self._genotype_guard.mark_genetic_seen()
        safe_event = {**event, "summary_message": HumanMessage(content=_SAFE_SUMMARY,
                      additional_kwargs={"lc_source": "summarization", _SAFE_MARKER: True}),
                      "file_path": None}
        return request.override(messages=messages,
                                state={**request.state, SUMMARIZATION_EVENT_KEY: safe_event,
                                       SUMMARIZATION_SESSION_ID_KEY: None})

    def _build_new_messages_with_path(self, summary: str, file_path: str | None) -> list[Any]:
        messages = super()._build_new_messages_with_path(summary, history_path_for(self.model, file_path))
        return [message.model_copy(update={"additional_kwargs": {
            **message.additional_kwargs, _SAFE_MARKER: True,
        }}) for message in messages]

    def _offload_inline_media(self, backend: Any, messages: list[Any]) -> tuple[list[Any], int]:
        return super()._offload_inline_media(backend, redact_genotype_history(messages))

    async def _aoffload_inline_media(self, backend: Any, messages: list[Any]) -> tuple[list[Any], int]:
        return await super()._aoffload_inline_media(backend, redact_genotype_history(messages))

    def _offload_to_backend(self, backend: Any, messages: list[Any], session_id: str) -> str | None:
        return super()._offload_to_backend(backend, redact_genotype_history(messages), session_id)

    async def _aoffload_to_backend(self, backend: Any, messages: list[Any], session_id: str) -> str | None:
        return await super()._aoffload_to_backend(backend, redact_genotype_history(messages), session_id)

    def _create_summary(self, messages_to_summarize: list[Any]) -> str:
        return super()._create_summary(redact_genotype_history(messages_to_summarize))

    async def _acreate_summary(self, messages_to_summarize: list[Any]) -> str:
        return await super()._acreate_summary(redact_genotype_history(messages_to_summarize))

    def _call_with_budget(self, request: Any, handler: Any, *, error: Exception | None = None,
                          rejected: Any = None) -> Any:
        # Overflow recovery can persist clipped trailing tool results under
        # /large_tool_results; clip only the redacted view.
        if error is not None or self._over_budget(request):
            request = request.override(messages=redact_genotype_history(request.messages))
        return super()._call_with_budget(request, handler, error=error, rejected=rejected)

    async def _acall_with_budget(self, request: Any, handler: Any, *, error: Exception | None = None,
                                 rejected: Any = None) -> Any:
        if error is not None or self._over_budget(request):
            request = request.override(messages=redact_genotype_history(request.messages))
        return await super()._acall_with_budget(request, handler, error=error, rejected=rejected)

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return super().wrap_model_call(self._safe_request(request), handler)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await super().awrap_model_call(self._safe_request(request), handler)


__all__ = ["GenotypeSafeSummarizationMiddleware"]
