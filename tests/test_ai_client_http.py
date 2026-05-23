from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from httpx import Client as RealHttpxClient

from postbridge.ai.client import (
    EchoAiGatewayClient,
    HttpAiGatewayClient,
    gateway_response_to_warnings_json,
    parse_openai_chat_completion_to_gateway_response,
)
from postbridge.ai.schemas import (
    GatewayAdaptRequest,
    GatewayChatMessage,
    GatewayGenerateRequest,
    GatewayTextResponse,
    GatewayTranslateRequest,
    GatewayUsageStats,
)
from postbridge.domain.errors import ExternalApiError


def _completion(content: Any = "Hello", *, usage: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": usage or {"total_tokens": 12, "prompt_tokens": 7, "completion_tokens": 5},
    }


def test_parse_openai_chat_completion_accepts_string_and_list_content() -> None:
    plain = parse_openai_chat_completion_to_gateway_response(_completion("  Hello  "))
    rich = parse_openai_chat_completion_to_gateway_response(
        _completion(
            [
                {"type": "text", "text": "Hello"},
                {"content": "world"},
                {"type": "image", "url": "ignored"},
            ],
            usage={"total_tokens": 9},
        )
    )

    assert plain.body_text == "Hello"
    assert plain.usage is not None
    assert plain.usage.total_tokens == 12
    assert rich.body_text == "Hello\nworld"
    assert rich.usage is not None
    assert rich.usage.total_tokens == 9


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": ["bad"]},
        {"choices": [{"message": "bad"}]},
        {"choices": [{"message": {"content": ""}}]},
    ],
)
def test_parse_openai_chat_completion_rejects_invalid_shapes(payload: dict[str, Any]) -> None:
    with pytest.raises(ExternalApiError) as exc_info:
        parse_openai_chat_completion_to_gateway_response(payload)

    assert exc_info.value.code == "EXTERNAL_AI_GATEWAY_INVALID_RESPONSE"


def test_http_ai_gateway_client_sends_adapt_request_with_auth_and_constraints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json=_completion("Adapted"))

    monkeypatch.setattr(
        "postbridge.ai.client.httpx.Client",
        lambda **kwargs: RealHttpxClient(transport=httpx.MockTransport(handler), **kwargs),
    )
    client = HttpAiGatewayClient(
        base_url="https://ai.example/",
        api_key="secret",
        timeout_seconds=5,
        default_model="default-model",
    )

    out = client.adapt_for_platform(
        GatewayAdaptRequest(
            source_text="Hello",
            title="T",
            platform="telegram",
            target_language="en",
            capabilities_hint={"max_length": 4096},
        )
    )

    req = captured["request"]
    body = json.loads(req.content.decode())
    assert out.body_text == "Adapted"
    assert req.url.path == "/v1/chat/completions"
    assert req.headers["Authorization"] == "Bearer secret"
    assert body["model"] == "default-model"
    assert "telegram" in body["messages"][0]["content"]
    assert "max_length" in body["messages"][0]["content"]
    assert "Title: T" in body["messages"][1]["content"]


def test_http_ai_gateway_client_translate_and_generate_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode()))
        return httpx.Response(200, json=_completion('{"title":"Generated","body_markdown":"Body"}'))

    monkeypatch.setattr(
        "postbridge.ai.client.httpx.Client",
        lambda **kwargs: RealHttpxClient(transport=httpx.MockTransport(handler), **kwargs),
    )
    client = HttpAiGatewayClient(
        base_url="https://ai.example",
        api_key=None,
        timeout_seconds=5,
        default_model="default-model",
    )

    translated = client.translate(
        GatewayTranslateRequest(
            source_text="Hello",
            title="Title",
            target_language="de",
        )
    )
    generated = client.generate_post(
        GatewayGenerateRequest(
            messages=[GatewayChatMessage(role="user", content="Write")],
            target_language="ru",
        )
    )

    assert translated.body_text == '{"title":"Generated","body_markdown":"Body"}'
    assert generated.title == "Generated"
    assert generated.body_text == "Body"
    assert "Target language code: de" in captured[0]["messages"][0]["content"]
    assert captured[1]["response_format"] == {"type": "json_object"}
    assert any("JSON fields" in msg["content"] for msg in captured[1]["messages"])


@pytest.mark.parametrize(
    ("exception", "code"),
    [
        (httpx.TimeoutException("slow"), "EXTERNAL_AI_GATEWAY_TIMEOUT"),
        (httpx.ConnectError("down"), "EXTERNAL_AI_GATEWAY_TRANSPORT"),
    ],
)
def test_http_ai_gateway_client_wraps_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
    exception: Exception,
    code: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exception

    monkeypatch.setattr(
        "postbridge.ai.client.httpx.Client",
        lambda **kwargs: RealHttpxClient(transport=httpx.MockTransport(handler), **kwargs),
    )
    client = HttpAiGatewayClient(
        base_url="https://ai.example",
        api_key=None,
        timeout_seconds=5,
        default_model="default-model",
    )

    with pytest.raises(ExternalApiError) as exc_info:
        client.generate_post(GatewayGenerateRequest(prompt="Write"))

    assert exc_info.value.code == code
    assert exc_info.value.retryable is True


def test_http_ai_gateway_client_wraps_http_and_invalid_json_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            httpx.Response(429, json={"error": "rate"}),
            httpx.Response(200, content=b"not-json"),
            httpx.Response(200, json=["not-object"]),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    monkeypatch.setattr(
        "postbridge.ai.client.httpx.Client",
        lambda **kwargs: RealHttpxClient(transport=httpx.MockTransport(handler), **kwargs),
    )
    client = HttpAiGatewayClient(
        base_url="https://ai.example",
        api_key=None,
        timeout_seconds=5,
        default_model="default-model",
    )

    with pytest.raises(ExternalApiError) as http_exc:
        client.generate_post(GatewayGenerateRequest(prompt="Write"))
    with pytest.raises(ExternalApiError) as json_exc:
        client.generate_post(GatewayGenerateRequest(prompt="Write"))
    with pytest.raises(ExternalApiError) as shape_exc:
        client.generate_post(GatewayGenerateRequest(prompt="Write"))

    assert http_exc.value.code == "EXTERNAL_AI_GATEWAY_HTTP_ERROR"
    assert http_exc.value.retryable is True
    assert json_exc.value.code == "EXTERNAL_AI_GATEWAY_INVALID_RESPONSE"
    assert shape_exc.value.code == "EXTERNAL_AI_GATEWAY_INVALID_RESPONSE"


def test_http_ai_gateway_client_requires_model() -> None:
    client = HttpAiGatewayClient(
        base_url="https://ai.example",
        api_key=None,
        timeout_seconds=5,
    )

    with pytest.raises(ExternalApiError) as exc_info:
        client.generate_post(GatewayGenerateRequest(prompt="Write"))

    assert exc_info.value.code == "EXTERNAL_AI_GATEWAY_VALIDATION"


def test_echo_ai_gateway_client_and_warning_serialization() -> None:
    client = EchoAiGatewayClient()
    generated = client.generate_post(GatewayGenerateRequest(prompt="Hello"))
    chat_generated = client.generate_post(
        GatewayGenerateRequest(messages=[GatewayChatMessage(role="user", content="Chat")])
    )
    chunks = list(client.iter_generate_post(GatewayGenerateRequest(prompt="Hello")))

    assert generated.title == "Generated"
    assert generated.body_text == "[generate] Hello"
    assert chat_generated.title == "Generated"
    assert chat_generated.body_text == "[generate-chat] Chat"
    assert chunks[-1]["type"] == "complete"
    assert gateway_response_to_warnings_json(GatewayTextResponse(body_text="ok")) is None
    assert gateway_response_to_warnings_json(
        GatewayTextResponse(body_text="ok", warnings=[{"code": "w"}], usage=GatewayUsageStats(total_tokens=1))
    ) == '[{"code": "w"}]'
