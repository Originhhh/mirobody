"""A thinking panel receives work; answer consumers receive only the final reply."""

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from mirobody.agent.wire.blocks import answer_text
from mirobody.agent.wire.stream import TurnStream


def test_custom_activity_only_publishes_the_fixed_content_free_lifecycle():
    stream = TurnStream()
    event = {"type": "context", "phase": "summarization", "status": "started", "id": "s1"}
    assert list(stream.blocks("custom", {**event, "summary": "private evidence"})) == [event]
    assert list(stream.blocks("custom", {"type": "reasoning", "reasoning": "internal"})) == []
    assert list(stream.blocks("custom", {**event, "status": "unknown"})) == []
    assert answer_text([event]) == ""


def chunk(stream, content="", *, id="m1", reasoning="", calls=None, **metadata):
    message = AIMessageChunk(content=content, id=id,
                             additional_kwargs={"reasoning_content": reasoning},
                             tool_call_chunks=calls or [])
    return list(stream.blocks("messages", (message, {"langgraph_node": "model", **metadata})))


def complete(stream, content="", *, id="m1", calls=None, invalid=None):
    message = AIMessage(content=content, id=id, tool_calls=calls or [], invalid_tool_calls=invalid or [])
    return list(stream.blocks("updates", {"model": {"messages": [message]}}))


def test_native_thinking_reaches_the_user_before_any_completed_step():
    stream = TurnStream()
    assert chunk(stream, reasoning="Checking the source.") == [
        {"type": "reasoning", "reasoning": "Checking the source."}]
    assert chunk(stream, "I will look it up.") == []
    blocks = complete(stream, "I will look it up.", calls=[
        {"id": "c1", "name": "query_health_indicators", "args": {"view": "latest"}}])
    assert blocks[0] == {"type": "reasoning", "reasoning": "I will look it up."}
    assert blocks[1]["type"] == "tool_call"
    assert answer_text(blocks) == ""


def test_only_final_reply_reaches_answer_and_speech_consumers():
    stream = TurnStream()
    calls = [{"id": "c1", "name": "query_health_indicators", "args": {}}]
    blocks = chunk(stream, "Searching first.") + complete(stream, "Searching first.", calls=calls)
    tool = ToolMessage(content="synthetic source", tool_call_id="c1")
    blocks += list(stream.blocks("updates", {"tools": {"messages": [tool]}}))
    assert chunk(stream, "The answer ", id="m2") == []
    assert chunk(stream, "is seven.", id="m2") == []
    blocks += complete(stream, "The answer is seven.", id="m2")
    assert answer_text(blocks) == "The answer is seven."
    assert complete(stream, "The answer is seven.", id="m2") == []
    assert complete(stream, "Searching first.", calls=calls) == []


def test_invalid_calls_and_incomplete_steps_never_publish_answer_text():
    stream = TurnStream()
    assert chunk(stream, "Trying the lookup.") == []
    blocks = complete(stream, "Trying the lookup.", invalid=[
        {"id": "bad", "name": "query_health_indicators", "args": "{", "error": "bad JSON"}])
    assert answer_text(blocks) == ""
    assert blocks == [{"type": "reasoning", "reasoning": "Trying the lookup."}]
    assert chunk(stream, "A partial answer", id="cancelled") == []
    # A different turn cannot publish this turn's buffered draft after cancellation.
    assert complete(TurnStream(), "Another answer", id="new") == [{"type": "text", "text": "Another answer"}]


def test_inline_think_tags_split_across_chunks_stay_out_of_the_answer():
    stream = TurnStream()
    blocks = []
    for part in ["<thi", "nk>Check ", "the record.</thi", "nk>The answer."]:
        blocks += chunk(stream, part)
    assert "".join(b["reasoning"] for b in blocks) == "Check the record."
    blocks += complete(stream, "<think>Check the record.</think>The answer.")
    assert answer_text(blocks) == "The answer."


def test_provider_reasoning_blocks_are_not_duplicated_into_visible_text():
    stream = TurnStream()
    native = [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "Checking."}]},
              {"type": "text", "text": "The answer."}]
    blocks = chunk(stream, native)
    assert blocks == [{"type": "reasoning", "reasoning": "Checking."}]
    blocks += complete(stream, native)
    assert answer_text(blocks) == "The answer."


def test_internal_summary_and_duplicate_tool_updates_do_not_leak():
    stream = TurnStream()
    assert chunk(stream, "Internal summary", reasoning="Internal work", lc_source="summarization") == []
    message = ToolMessage(content=[{"type": "image", "base64": "AA=="}], tool_call_id="c1")
    update = {"tools": {"messages": [message]}}
    first = list(stream.blocks("updates", update))
    assert first[0]["content"] == message.content
    assert list(stream.blocks("updates", update)) == []


def test_empty_tool_output_still_finishes_its_visible_work_step():
    message = ToolMessage(content="", tool_call_id="empty", status="error")
    blocks = list(TurnStream().blocks("updates", {"tools": {"messages": [message]}}))
    assert blocks == [{"type": "tool_result", "tool_call_id": "empty", "content": "", "status": "error"}]


def test_python_artifact_failures_reach_both_work_display_and_budget_governance():
    from mirobody.agent.middleware.model_budget import failure_streak

    failed = ToolMessage(content="error (exit 1): synthetic failure", tool_call_id="compute",
                         artifact={"status": "error", "error_kind": "unavailable", "truncated": True})
    blocks = list(TurnStream().blocks("updates", {"tools": {"messages": [failed]}}))
    assert blocks[0]["status"] == "error"
    assert blocks[0]["error_kind"] == "unavailable"
    assert blocks[0]["truncated"] is True
    assert failure_streak([failed]) == 1


def test_a_genotype_repair_keeps_current_work_and_redacts_previous_turn_work():
    from langchain_core.messages import HumanMessage
    from mirobody.agent.middleware.genotype_row_guard import GenotypeRowGuardMiddleware
    from mirobody.kernel.tools import Envelope, Meta

    guard = GenotypeRowGuardMiddleware()
    prior = ToolMessage(name="query_genetic_data", tool_call_id="old", content="previous synthetic row")
    current = ToolMessage(name="query_genetic_data", tool_call_id="current", content="current synthetic row",
                          artifact=Envelope("ok", data=[{"rsid": "rs1"}], meta=Meta(row_count=1)))
    guard._record(current)
    history = [HumanMessage("Previous question"), prior, AIMessage("previous interpretation"),
               HumanMessage("Current question"), current, AIMessage("current interpretation"),
               HumanMessage("Finish this question", name="__last_model_call__")]
    guarded, *_ = guard._guard_messages(history)
    assert guarded[2].content != "previous interpretation"
    assert guarded[5].content == "current interpretation"


def test_repaired_full_history_is_not_published_as_a_new_answer():
    from langchain_core.messages import HumanMessage, RemoveMessage
    from langgraph.graph.message import REMOVE_ALL_MESSAGES

    stream = TurnStream()
    old_call = AIMessage(content="Old lookup", id="old-call", tool_calls=[
        {"id": "old-tool", "name": "query_health_indicators", "args": {}}])
    old_result = ToolMessage(content="Old source", tool_call_id="old-tool")
    update = {"model": {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), HumanMessage("Old question"),
              old_call, old_result, AIMessage("OLD ANSWER", id="old-answer"),
              HumanMessage("Current question"), AIMessage("NEW ANSWER", id="new-answer")]}}
    blocks = list(stream.blocks("updates", update))
    assert blocks == [{"type": "text", "text": "NEW ANSWER"}]


async def test_real_harness_invalid_call_repair_does_not_replay_an_old_answer():
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import HumanMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import tool
    from langgraph.graph.message import REMOVE_ALL_MESSAGES

    from mirobody.agent import harness

    @tool
    def synthetic_read() -> str:
        """Return invented data for protocol repair verification."""
        return "7"

    class MixedModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "synthetic-mixed-calls"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if any(isinstance(message, ToolMessage) for message in messages):
                reply = AIMessage("NEW ANSWER", id="new_answer")
            else:
                reply = AIMessage("Looking up.", id="mixed_call", tool_calls=[
                    {"name": "synthetic_read", "args": {}, "id": "valid"}], invalid_tool_calls=[
                    {"name": "synthetic_read", "args": "{", "id": "invalid", "error": "invalid JSON"}])
            return ChatResult(generations=[ChatGeneration(message=reply)])

    backend, permissions = harness.read_only_mounts({})
    graph = harness.assemble(model=MixedModel(), tools=[synthetic_read], system_prompt="Read once, then answer.",
                             backend=backend, permissions=permissions,
                             middleware=harness.standard_middleware(model_call_limit=4), model_call_limit=4)
    presentation, blocks, replaced = TurnStream(), [], False
    history = [HumanMessage("Old question", id="old_q"), AIMessage("OLD ANSWER", id="old_a"),
               HumanMessage("New question", id="new_q")]
    async for kind, item in graph.astream({"messages": history}, stream_mode=["messages", "updates"]):
        if kind == "updates":
            replaced |= any(getattr(message, "id", None) == REMOVE_ALL_MESSAGES
                            for message in item.get("model", {}).get("messages", []))
        blocks.extend(presentation.blocks(kind, item))
    assert replaced, "The integration must exercise LangChain's full-history repair command"
    assert answer_text(blocks) == "NEW ANSWER"
    assert any(block.get("tool_call_id") == "invalid" and block.get("status") == "error" for block in blocks)


def test_text_next_to_a_tool_chunk_is_work_even_without_native_reasoning():
    stream = TurnStream()
    assert chunk(stream, "Look up the record.", calls=[
        {"id": "c1", "name": "query_health_indicators", "args": "{}", "index": 0}]) == []
    blocks = complete(stream, "Look up the record.", calls=[
        {"id": "c1", "name": "query_health_indicators", "args": {}}])
    assert blocks[0] == {"type": "reasoning", "reasoning": "Look up the record."}
    assert answer_text(blocks) == ""


def test_nonstreamed_inline_reasoning_and_unclosed_thinking_are_not_answers():
    from mirobody.agent.models.messages import message_text

    blocks = complete(TurnStream(), "<think>Checking.</think>Answer.")
    assert blocks == [{"type": "reasoning", "reasoning": "Checking."}, {"type": "text", "text": "Answer."}]
    blocks = complete(TurnStream(), "<think>Still checking")
    assert blocks == [{"type": "reasoning", "reasoning": "Still checking"}]
    assert answer_text(blocks) == ""
    assert message_text(AIMessage(content="<think>Still checking")) == ""


async def test_real_harness_streams_work_separately_from_its_final_answer():
    from langchain_core.language_models import BaseChatModel
    from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
    from langchain_core.tools import tool

    from mirobody.agent import harness

    @tool
    def synthetic_read() -> str:
        """Return a fixed invented reading for the stream contract."""
        return "7"

    class ScriptedModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "synthetic-thinking-model"

        def bind_tools(self, tools, **kwargs):
            return self

        def response(self, messages):
            if any(isinstance(message, ToolMessage) for message in messages):
                return AIMessage(content="The recorded value is 7.", id="answer-model",
                                 additional_kwargs={"reasoning_content": "The source answered."})
            return AIMessage(content="Looking up the source.", id="lookup-model",
                             additional_kwargs={"reasoning_content": "I need the source."},
                             tool_calls=[{"name": "synthetic_read", "args": {}, "id": "source-call"}])

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=self.response(messages))])

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            response = self.response(messages)
            yield ChatGenerationChunk(message=AIMessageChunk(content="", id=response.id,
                                      additional_kwargs=response.additional_kwargs))
            for part in response.content.split(" "):
                yield ChatGenerationChunk(message=AIMessageChunk(content=part + " ", id=response.id))
            if response.tool_calls:
                yield ChatGenerationChunk(message=AIMessageChunk(content="", id=response.id, tool_call_chunks=[
                    {"name": "synthetic_read", "args": "{}", "id": "source-call", "index": 0}]))

    backend, permissions = harness.read_only_mounts({})
    graph = harness.assemble(model=ScriptedModel(), tools=[synthetic_read], system_prompt="Use the source.",
                             backend=backend, permissions=permissions,
                             middleware=harness.standard_middleware(model_call_limit=4), model_call_limit=4)
    presentation, blocks = TurnStream(), []
    async for kind, item in graph.astream({"messages": [{"role": "user", "content": "Read it."}]},
                                         stream_mode=["messages", "updates"]):
        blocks.extend(presentation.blocks(kind, item))
    assert blocks[0] == {"type": "reasoning", "reasoning": "I need the source."}
    assert any(block["type"] == "tool_call" for block in blocks)
    assert any(block["type"] == "tool_result" for block in blocks)
    assert answer_text(blocks).strip() == "The recorded value is 7."
    assert "Looking up" not in answer_text(blocks)
