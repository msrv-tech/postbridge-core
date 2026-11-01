"""Exercise real SDK model/tool execution against a local HTTP transport."""
import asyncio
import json
from dataclasses import replace

import httpx2 as httpx
import pytest
from pydantic import BaseModel, ConfigDict

from postbridge.agent.sdk_runtime import AgentExecution, AgentMessage, AgentTool, run_postbridge_agent
from postbridge.domain.errors import ExternalApiError, ValidationError


class ReadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str


def execution(**changes):
    return AgentExecution(base_url="https://gateway.example/api/v1", model="test-model",
        instructions="Use only the tools and facts supplied by the server.", question="Read the selected draft.",
        api_key="test-secret", usage_format="gitsell", **changes)


def reply(content="Done", *, tool=None, units=7, status=200):
    message = {"role": "assistant", "content": content}
    if tool:
        message["tool_calls"] = [{"id": "call_read", "type": "function",
            "function": {"name": tool[0], "arguments": json.dumps(tool[1])}}]
    return httpx.Response(status, json={"id": "provider-1", "object": "chat.completion", "created": 1,
        "model": "test-model", "choices": [{"index": 0, "message": message,
            "finish_reason": "tool_calls" if tool else "stop"}],
        "usage": {"total_tokens": units, "prompt_tokens": 3, "completion_tokens": 4}})


def run(config, handler, **callbacks):
    return asyncio.run(run_postbridge_agent(config, transport=httpx.MockTransport(handler), **callbacks))


def test_sdk_executes_validated_tool_and_keeps_receipts_before_parsing():
    requests, order = [], []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == "/api/v1/chat/completions"
        assert body["max_units"] == 50
        assert body["parallel_tool_calls"] is False
        order.append("http")
        return reply(None, tool=("read_draft", {"query": "selected"})) if len(requests) == 1 else reply()

    async def read(args):
        order.append("tool")
        assert args.query == "selected"
        return {"title": "Selected draft"}

    result = run(execution(tools=(AgentTool("read_draft", "Read the selected draft", ReadArguments, read),),
        required_tools=frozenset({"read_draft"}), max_units=50), handler,
        before_provider_call=lambda _: order.append("admit"),
        on_provider_receipt=lambda _: order.append("receipt"))
    assert result.output == "Done"
    assert order == ["admit", "http", "receipt", "tool", "admit", "http", "receipt"]
    assert [r.charged_units for r in result.receipts] == [7, 7]
    assert all(r.input_tokens is None for r in result.receipts)
    assert result.tool_events[0].status == "succeeded"
    assert any(item["role"] == "tool" for item in requests[1]["messages"])


def test_sdk_does_not_share_history_or_tools_between_executions():
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return reply()
    first = execution(history=(AgentMessage(role="user", content="Private first workspace history"),),
        tools=(AgentTool("read_private_draft", "Private workspace tool", ReadArguments, lambda _: {}),))
    run(first, handler)
    run(execution(), handler)
    assert "Private first workspace history" in str(seen[0])
    assert "Private first workspace history" not in str(seen[1])
    assert not seen[1].get("tools")
    assert "test-secret" not in repr(first)


def test_sdk_validates_tool_arguments_without_executing_side_effect():
    seen, executed = [], []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            return reply(None, tool=("read_draft", {"query": {"secret": "do-not-echo"}}))
        assert "INVALID_TOOL_ARGUMENTS" in str(body)
        assert "do-not-echo" not in body["messages"][-1]["content"]
        return reply("Arguments rejected")
    result = run(execution(tools=(AgentTool("read_draft", "Read draft", ReadArguments,
        lambda args: executed.append(args)),)), handler)
    assert not executed
    assert result.tool_events[0].error_code == "INVALID_TOOL_ARGUMENTS"


def test_missing_required_tool_rejects_plausible_model_answer():
    recorded = []
    tool = AgentTool("read_draft", "Read draft", ReadArguments, lambda _: {})
    with pytest.raises(ValidationError) as caught:
        run(execution(tools=(tool,), required_tools=frozenset({"read_draft"})), lambda _: reply(),
            on_provider_receipt=recorded.append)
    assert recorded[0].charged_units == 7
    assert caught.value.code == "VALIDATION_AGENT_REQUIRED_TOOLS"


def test_admission_failure_is_preserved_and_does_not_dispatch_or_retry():
    dispatched, received = [], []
    error = ValidationError(code="BUDGET_EXHAUSTED", message="No allowance")
    def deny(_):
        raise error
    with pytest.raises(ValidationError) as caught:
        run(execution(), lambda request: dispatched.append(request),
            before_provider_call=deny, on_provider_receipt=received.append)
    assert caught.value is error
    assert dispatched == received == []


def test_admission_rechecked_before_next_model_turn():
    dispatched, recorded, admissions = [], [], []
    def admit(call_id):
        admissions.append(call_id)
        if len(admissions) == 2:
            raise ValidationError(code="BUDGET_EXHAUSTED", message="No allowance")
    def handler(request):
        dispatched.append(request)
        return reply(None, tool=("read_draft", {"query": "selected"}))
    with pytest.raises(ValidationError):
        run(execution(tools=(AgentTool("read_draft", "Read draft", ReadArguments, lambda _: {}),)),
            handler, before_provider_call=admit, on_provider_receipt=recorded.append)
    assert len(dispatched) == len(recorded) == 1


@pytest.mark.parametrize("failure", ["malformed_json", "http_500", "timeout"])
def test_failed_paid_calls_are_recorded_without_retry(failure):
    dispatched, recorded = [], []
    def handler(request):
        dispatched.append(request)
        if failure == "malformed_json":
            return httpx.Response(200, content=b"not-json")
        if failure == "http_500":
            return reply(units=11, status=500)
        raise httpx.ReadTimeout("sensitive-upstream-body", request=request)
    with pytest.raises(ExternalApiError) as caught:
        run(execution(), handler, on_provider_receipt=recorded.append)
    assert len(dispatched) == len(recorded) == 1
    assert not caught.value.retryable
    assert "sensitive-upstream-body" not in str(caught.value.details)
    assert recorded[0].status == ("failed" if failure == "http_500" else "unknown")
    assert caught.value.details["provider_calls"][0]["status"] == recorded[0].status


def test_total_timeout_records_unknown_call_and_stops_run():
    recorded = []
    async def handler(request):
        await asyncio.sleep(1)
        return reply()
    with pytest.raises(ExternalApiError) as caught:
        run(execution(timeout_seconds=0.05), handler, on_provider_receipt=recorded.append)
    assert caught.value.details["failure_type"] == "TimeoutError"
    assert len(recorded) == 1 and recorded[0].status == "unknown"


def test_structured_output_preserves_usage_when_schema_validation_fails():
    class Output(BaseModel):
        message: str
    recorded = []
    with pytest.raises(ExternalApiError):
        run(execution(output_type=Output), lambda _: reply('{"wrong_field": true}'),
            on_provider_receipt=recorded.append)
    assert recorded[0].charged_units == 7
    assert run(execution(output_type=Output), lambda _: reply('{"message": "Ready"}')).output.message == "Ready"


def test_turn_limit_does_not_retry_completed_tools():
    executed, recorded = [], []
    tool = AgentTool("read_draft", "Read draft", ReadArguments, lambda args: executed.append(args.query))
    with pytest.raises(ExternalApiError) as caught:
        run(execution(tools=(tool,), max_turns=1),
            lambda _: reply(None, tool=("read_draft", {"query": "once"})),
            on_provider_receipt=recorded.append)
    assert caught.value.details["failure_type"] == "MaxTurnsExceeded"
    assert executed == ["once"]
    assert len(recorded) == 1


@pytest.mark.parametrize("change", [{"max_turns": 0}, {"required_tools": frozenset({"absent"})},
    {"tools": (AgentTool("bad.name", "Read", ReadArguments, lambda _: {}),)}, {"usage_format": "unknown"}])
def test_invalid_execution_fails_before_contacting_provider(change):
    with pytest.raises(ValidationError):
        run(replace(execution(), **change), lambda _: pytest.fail("must not contact provider"))


def test_provider_tokens_are_not_mistaken_for_charged_units():
    result = run(replace(execution(), usage_format="provider_tokens"), lambda _: reply(units=99))
    assert result.receipts[0].charged_units is None
    assert result.receipts[0].input_tokens == 3
    assert result.receipts[0].output_tokens == 4


def test_failing_tool_is_not_retried_after_side_effect():
    mutations, requests = [], []
    def mutate(args):
        mutations.append(args.query)
        raise RuntimeError("private-backend-response")
    def handler(request):
        requests.append(request)
        return reply(None, tool=("mutate", {"query": "once"}))
    with pytest.raises(ExternalApiError) as caught:
        run(execution(tools=(AgentTool("mutate", "Save a change", ReadArguments, mutate),)), handler)
    assert mutations == ["once"] and len(requests) == 1
    assert caught.value.details["tools"][0]["error_code"] == "AGENT_TOOL_FAILED"
    assert "private-backend-response" not in str(caught.value.details)


def test_cancellation_propagates_and_records_dispatched_call_as_unknown():
    recorded = []
    async def scenario():
        started = asyncio.Event()
        async def handler(request):
            started.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(run_postbridge_agent(execution(),
            transport=httpx.MockTransport(handler), on_provider_receipt=recorded.append))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    assert len(recorded) == 1 and recorded[0].status == "unknown"


def test_receipt_storage_failure_stops_before_another_model_call():
    dispatched = []
    def handler(request):
        dispatched.append(request)
        return reply()
    def fail(_):
        raise RuntimeError("ledger unavailable")
    with pytest.raises(ExternalApiError) as caught:
        run(execution(), handler, on_provider_receipt=fail)
    assert len(dispatched) == 1
    assert caught.value.details["provider_calls"][0]["charged_units"] == 7


@pytest.mark.parametrize("raw_units", [None, True, -1, "invalid", 1.5])
def test_missing_or_invalid_currency_is_never_reported_as_zero(raw_units):
    recorded = []
    if raw_units in ("invalid", 1.5):
        # The SDK may reject malformed usage while decoding the model response.
        with pytest.raises(ExternalApiError):
            run(execution(), lambda _: reply(units=raw_units), on_provider_receipt=recorded.append)
    else:
        run(execution(), lambda _: reply(units=raw_units), on_provider_receipt=recorded.append)
    assert len(recorded) == 1
    assert recorded[0].charged_units is None
    assert recorded[0].status == "unknown"


@pytest.mark.parametrize("charged_units", [0, 23])
def test_explicit_charged_units_take_precedence_over_sdk_token_totals(charged_units):
    def handler(_):
        response = reply(units=99)
        body = response.json()
        body["usage_tokens_charged"] = charged_units
        return httpx.Response(200, json=body)
    result = run(execution(), handler)
    assert result.receipts[0].charged_units == charged_units
    assert result.receipts[0].status == "succeeded"


def test_tools_remain_serial_when_gateway_ignores_parallel_call_setting():
    active, concurrency, executed, requests = 0, [], [], []
    async def read(args):
        nonlocal active
        active += 1
        concurrency.append(active)
        await asyncio.sleep(0.01)
        executed.append(args.query)
        active -= 1
        return {"query": args.query}
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            body = reply(None, tool=("read_draft", {"query": "first"})).json()
            body["choices"][0]["message"]["tool_calls"].append({"id": "call_second", "type": "function",
                "function": {"name": "read_draft", "arguments": '{"query": "second"}'}})
            return httpx.Response(200, json=body)
        return reply()
    result = run(execution(tools=(AgentTool("read_draft", "Read", ReadArguments, read),)), handler)
    assert max(concurrency) == 1
    assert sorted(executed) == ["first", "second"]
    assert len(result.tool_events) == 2


def test_unexpected_mutation_failure_blocks_queued_tool_calls():
    mutations = []
    async def mutate(args):
        mutations.append(args.query)
        await asyncio.sleep(0.01)
        raise RuntimeError("unknown mutation outcome")
    def handler(_):
        body = reply(None, tool=("mutate", {"query": "first"})).json()
        body["choices"][0]["message"]["tool_calls"].append({"id": "call_second", "type": "function",
            "function": {"name": "mutate", "arguments": '{"query": "second"}'}})
        return httpx.Response(200, json=body)
    with pytest.raises(ExternalApiError):
        run(execution(tools=(AgentTool("mutate", "Save", ReadArguments, mutate),)), handler)
    assert mutations == ["first"]
