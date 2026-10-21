"""The last model call a turn's budget allows is spent on the answer.

`ModelCallLimitMiddleware(exit_behavior="end")` stops a run that has made its
budget of model calls by jumping to the end before the next one. Nothing then
asks the model to answer: a model that called a tool on every step ended the
turn with "Model call limits exceeded" in the checkpoint and the empty-turn
line on the screen (measured with a model that always calls a tool,
2026-10-07).

This subclass keeps that stop, and makes the last allowed call an answer: one
instruction joins the conversation before it, and the call is made with tool
calls switched off. Both are append-only on purpose. The tools stay declared
(`tool_choice` says none may be called) and the instruction stays in the
history afterwards, because removing a tool or a message edits the prefix a
reasoning model's earlier thinking blocks are bound to, which Anthropic's
current models refuse ("preserved thinking").

The same last call comes early when the turn's latest tool results are all
failures (refused, invalid arguments, an `eval` error): on the 2026-10-10
evaluation every answer behind five or more failures in a row graded 4 of 10
or less, the longest after 48 failures and 52 calls, and none recovered.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain.agents.middleware import ModelCallLimitMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from mirobody.agent.models.messages import LAST_CALL_NAME

_LAST_CALL = (
    "This is your last step for this question: no more tools can be called. "
    "Answer the person now from the tool results above, and say plainly what you "
    "could not look up."
)


#: Failed tool results in a row, since the person last spoke, that end the tools.
FAILURE_STREAK = 5


def _failed(message: ToolMessage) -> bool:
    artifact = getattr(message, "artifact", None)
    status = artifact.get("status") if isinstance(artifact, Mapping) else getattr(artifact, "status", None)
    return (message.status == "error" or status == "error"
            or str(message.content).lstrip().startswith("<error"))


def failure_streak(messages: list) -> int:
    """Failed tool results at the end of `messages`, back to the last success
    or the last human message. The last-call instruction is read past: it is
    appended before the call that must then be made without tools."""
    streak = 0
    for message in reversed(messages or []):
        if isinstance(message, AIMessage) or getattr(message, "name", None) == LAST_CALL_NAME:
            continue
        if not isinstance(message, ToolMessage) or not _failed(message):
            break
        streak += 1
    return streak


def no_tool_choice(model: Any) -> str | dict[str, str]:
    """`tool_choice` that lets `model` call no tool, in its integration's
    spelling: LangChain's Anthropic clients read a bare string as a tool's
    name, so "none" there would force a tool called none."""
    if any("Anthropic" in base.__name__ for base in type(model).__mro__):
        return {"type": "none"}
    return "none"


class ModelCallBudgetMiddleware(ModelCallLimitMiddleware):
    """A run-level model-call budget whose last call is an answer."""

    def __init__(self, *, run_limit: int) -> None:
        super().__init__(run_limit=run_limit, exit_behavior="end")

    def _last_call(self, state: Any) -> bool:
        return (state.get("run_model_call_count", 0) == self.run_limit - 1
                or failure_streak(state.get("messages")) >= FAILURE_STREAK)

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        stop = super().before_model(state, runtime)
        if stop is not None or not self._last_call(state):
            return stop
        return {"messages": [HumanMessage(content=_LAST_CALL, name=LAST_CALL_NAME)]}

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self.before_model(state, runtime)

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        if self._last_call(request.state):
            request = request.override(tool_choice=no_tool_choice(request.model))
        return handler(request)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        if self._last_call(request.state):
            request = request.override(tool_choice=no_tool_choice(request.model))
        return await handler(request)


__all__ = ["FAILURE_STREAK", "LAST_CALL_NAME", "ModelCallBudgetMiddleware", "failure_streak", "no_tool_choice"]
