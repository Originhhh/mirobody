"""`eval` code that ends with a top-level `return` runs instead of failing.

The REPL's result is the value of the last expression, and QuickJS refuses a
`return` outside a function at parse time: "A 'return' statement can only be
used within a function body." On the 24-question evaluation (2026-10-10/11)
the post-trained MiniCPM5-2B variants hit it 227 times, retrying the same code
or answering without the numbers; the base model never did. Fixed in the
training data too, this keeps any model that writes it from losing the turn.

A parse error means nothing ran, so the call is retried once with the code
wrapped in an async function, whose value the REPL awaits as the last
expression. Variables declared inside the wrapper do not persist to the next
call, which the failed call would not have done either.
"""

import logging

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)

_PARSE_ERROR = "'return' statement can only be used within a function body"


def _wrapped(request):
    """The same call with its code inside `(async () => { ... })()`, or None
    when it is not an eval call whose code says `return`."""
    call = getattr(request, "tool_call", None) or {}
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    code = args.get("code")
    if not isinstance(code, str) or "return" not in code:
        return None
    return request.override(tool_call={**call, "args": {**args, "code": f"(async () => {{\n{code}\n}})()"}})


def _refused_return(result: object) -> bool:
    return isinstance(result, ToolMessage) and _PARSE_ERROR in str(result.content)


class EvalTopLevelReturnMiddleware(AgentMiddleware):
    """Retries an `eval` call that failed only on a top-level `return`."""

    def __init__(self, *, tool_name: str = "eval") -> None:
        super().__init__()
        self._tool_name = tool_name

    async def awrap_tool_call(self, request, handler):
        result = await handler(request)
        if (request.tool_call or {}).get("name") != self._tool_name or not _refused_return(result):
            return result
        retry = _wrapped(request)
        if retry is None:
            return result
        logger.info("eval: retried a top-level return inside an async function")
        return await handler(retry)

    def wrap_tool_call(self, request, handler):
        result = handler(request)
        if (request.tool_call or {}).get("name") != self._tool_name or not _refused_return(result):
            return result
        retry = _wrapped(request)
        if retry is None:
            return result
        logger.info("eval: retried a top-level return inside an async function")
        return handler(retry)
