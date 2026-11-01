"""The shared Postbridge agent definition and request-scoped SDK execution.

Adapters supply authorized tools, instructions, history and accounting callbacks.
This module has no database, workspace billing or hosted integration dependencies.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

import httpx2 as httpx
from agents import Agent, AgentOutputSchema, FunctionTool, ModelSettings, OpenAIChatCompletionsModel, RunConfig, Runner
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError as ModelValidationError

from postbridge.ai.urls import join_openai_compatible_path
from postbridge.domain.errors import ExternalApiError, PostbridgeError, ValidationError

AGENT_NAME = "Postbridge Assistant"
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class AgentMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=32000)


class ProviderReceipt(BaseModel):
    """Provider facts, never SDK token totals interpreted as currency."""
    model_config = ConfigDict(frozen=True)
    call_id: str
    status: Literal["unknown", "succeeded", "failed"] = "unknown"
    provider_request_id: str | None = None
    http_status: int | None = None
    charged_units: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class ToolEvent(BaseModel):
    tool_name: str
    status: Literal["succeeded", "failed"]
    error_code: str | None = None


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    arguments: type[BaseModel]
    execute: Callable[[Any], Any]


@dataclass(frozen=True)
class AgentExecution:
    base_url: str
    model: str
    instructions: str
    question: str
    api_key: str | None = field(default=None, repr=False)
    history: tuple[AgentMessage, ...] = ()
    tools: tuple[AgentTool, ...] = ()
    required_tools: frozenset[str] = frozenset()
    output_type: type[BaseModel] | None = None
    max_turns: int = 8
    max_output_tokens: int = 2000
    timeout_seconds: float = 60.0
    usage_format: str = "provider_tokens"
    max_units: int | None = None


@dataclass
class AgentResult:
    output: Any
    model: str
    receipts: list[ProviderReceipt]
    tool_events: list[ToolEvent]


async def _notify(callback: Callable | None, value: Any) -> None:
    if callback is not None:
        result = callback(value)
        if inspect.isawaitable(result):
            await result


def _nonnegative_int(value: Any) -> int | None:
    # Do not invent a zero price when the gateway supplies no receipt.
    if value is None or isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if result >= 0 and (not isinstance(value, float) or value == result) else None


def _receipt(response: httpx.Response, call_id: str, usage_format: str) -> ProviderReceipt:
    try:
        body = response.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    usage = body.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    units = _nonnegative_int(body.get("usage_tokens_charged"))
    if units is None and usage_format == "gitsell":
        units = _nonnegative_int(usage.get("total_tokens"))
    status = "succeeded" if response.is_success else "failed"
    if units is None and (response.is_success or response.status_code >= 500):
        status = "unknown"
    return ProviderReceipt(call_id=call_id, status=status,
        http_status=response.status_code,
        provider_request_id=str(response.headers.get("x-request-id") or body.get("id") or "")[:128] or None,
        charged_units=units,
        input_tokens=_nonnegative_int(usage.get("prompt_tokens")) if usage_format == "provider_tokens" else None,
        output_tokens=_nonnegative_int(usage.get("completion_tokens")) if usage_format == "provider_tokens" else None)


def _validate(execution: AgentExecution) -> None:
    names = [tool.name for tool in execution.tools]
    if (not execution.base_url.strip() or not execution.model.strip() or not execution.instructions.strip()
            or not execution.question.strip() or not 1 <= execution.max_turns <= 32
            or not 16 <= execution.max_output_tokens <= 32000 or execution.timeout_seconds <= 0
            or execution.usage_format not in {"gitsell", "provider_tokens"}
            or (execution.max_units is not None and execution.max_units <= 0)
            or len(names) != len(set(names)) or any(not _TOOL_NAME.fullmatch(name) for name in names)
            or not execution.required_tools.issubset(names)):
        raise ValidationError(code="VALIDATION_AGENT_EXECUTION", message="Invalid agent execution configuration")


def _sdk_tool(tool: AgentTool, events: list[ToolEvent], lock: asyncio.Lock, stopped: asyncio.Event) -> FunctionTool:
    async def invoke(context: Any, arguments: str) -> str:
        try:
            parsed = tool.arguments.model_validate_json(arguments)
        except ModelValidationError as exc:
            events.append(ToolEvent(tool_name=tool.name, status="failed", error_code="INVALID_TOOL_ARGUMENTS"))
            return json.dumps({"error": "INVALID_TOOL_ARGUMENTS", "fields": [
                {"field": ".".join(map(str, error["loc"])), "type": error["type"]}
                for error in exc.errors(include_input=False, include_url=False)
            ]})
        # parallel_tool_calls is a model hint. Enforce serialization locally even
        # when a compatible gateway emits multiple calls in the same response.
        async with lock:
            if stopped.is_set():
                events.append(ToolEvent(tool_name=tool.name, status="failed", error_code="AGENT_EXECUTION_ABORTED"))
                return json.dumps({"error": "AGENT_EXECUTION_ABORTED"})
            try:
                # Synchronous operations stay on the session's owning thread.
                result = tool.execute(parsed)
                if inspect.isawaitable(result):
                    result = await result
                encoded = json.dumps(result, ensure_ascii=False, default=str)
            except PostbridgeError as exc:
                events.append(ToolEvent(tool_name=tool.name, status="failed", error_code=exc.code))
                return json.dumps({"error": exc.code, "message": exc.message})
            except Exception:
                stopped.set()
                events.append(ToolEvent(tool_name=tool.name, status="failed", error_code="AGENT_TOOL_FAILED"))
                # Unexpected failures abort: repeating a mutation may duplicate it.
                raise
            events.append(ToolEvent(tool_name=tool.name, status="succeeded"))
            return encoded

    return FunctionTool(name=tool.name, description=tool.description,
        params_json_schema=tool.arguments.model_json_schema(), strict_json_schema=False,
        on_invoke_tool=invoke)


async def run_postbridge_agent(
    execution: AgentExecution,
    *,
    before_provider_call: Callable[[str], Awaitable[None] | None] | None = None,
    on_provider_receipt: Callable[[ProviderReceipt], Awaitable[None] | None] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AgentResult:
    """Execute one isolated turn; callbacks run before dispatch and before SDK parsing.

    Adapters own durable idempotency and history. Failed/unknown paid calls are
    never automatically retried by the SDK or this runner.
    """
    _validate(execution)
    receipts: list[ProviderReceipt] = []
    pending: dict[str, ProviderReceipt] = {}
    events: list[ToolEvent] = []
    tool_lock = asyncio.Lock()
    tools_stopped = asyncio.Event()
    admission_error: Exception | None = None

    async def before(request: httpx.Request) -> None:
        nonlocal admission_error
        call_id = uuid4().hex
        try:
            await _notify(before_provider_call, call_id)
        except Exception as exc:
            admission_error = exc
            raise
        pending[call_id] = ProviderReceipt(call_id=call_id)
        request.extensions["postbridge_call_id"] = call_id

    async def after(response: httpx.Response) -> None:
        await response.aread()
        call_id = response.request.extensions["postbridge_call_id"]
        receipt = _receipt(response, call_id, execution.usage_format)
        receipts.append(receipt)
        pending.pop(call_id)
        await _notify(on_provider_receipt, receipt)

    # Convert /api or /v1 to the versioned root without duplicating /v1.
    completion_url = join_openai_compatible_path(execution.base_url, "/v1/chat/completions")
    sdk_base = completion_url.removesuffix("/chat/completions")
    async with httpx.AsyncClient(transport=transport, timeout=execution.timeout_seconds,
            event_hooks={"request": [before], "response": [after]}) as http_client:
        async with AsyncOpenAI(api_key=execution.api_key or "postbridge-local",
                base_url=sdk_base, timeout=execution.timeout_seconds, max_retries=0,
                http_client=http_client) as client:
            agent = Agent(name=AGENT_NAME, instructions=execution.instructions,
                model=OpenAIChatCompletionsModel(model=execution.model, openai_client=client),
                tools=[_sdk_tool(tool, events, tool_lock, tools_stopped) for tool in execution.tools],
                output_type=AgentOutputSchema(execution.output_type, strict_json_schema=False) if execution.output_type else None,
                model_settings=ModelSettings(max_tokens=execution.max_output_tokens,
                    parallel_tool_calls=False,
                    extra_body={"max_units": execution.max_units} if execution.max_units is not None else None))
            messages = [message.model_dump() for message in execution.history]
            messages.append({"role": "user", "content": execution.question})
            try:
                async with asyncio.timeout(execution.timeout_seconds):
                    result = await Runner.run(agent, messages, max_turns=execution.max_turns,
                        run_config=RunConfig(tracing_disabled=True))
                called = {event.tool_name for event in events if event.status == "succeeded"}
                if not execution.required_tools.issubset(called):
                    raise ValidationError(code="VALIDATION_AGENT_REQUIRED_TOOLS",
                        message="Agent did not execute the required tools",
                        details={"missing_tools": sorted(execution.required_tools - called)})
            except PostbridgeError:
                raise
            except Exception as exc:
                if admission_error is not None:
                    raise admission_error
                raise ExternalApiError(code="EXTERNAL_AGENT_EXECUTION_FAILED",
                    message="Agent execution failed", source="agent_llm", retryable=False,
                    details={"model": execution.model, "failure_type": type(exc).__name__,
                        "provider_calls": [receipt.model_dump() for receipt in [*receipts, *pending.values()]],
                        "tools": [event.model_dump() for event in events]}) from exc
            finally:
                for receipt in pending.values():
                    receipts.append(receipt)
                    await _notify(on_provider_receipt, receipt)
    return AgentResult(output=result.final_output, model=execution.model,
        receipts=receipts, tool_events=events)
