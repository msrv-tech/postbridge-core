from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import Client as RealHttpxClient

from postbridge.agent.providers.openai_compatible import (
    OpenAICompatibleProvider,
    _extract_first_json_object,
    _extract_message_text,
    _parse_json_object_loose,
    _strip_optional_json_fence,
    ensure_openai_compatible_provider,
)
from postbridge.domain.errors import ConfigurationError, ExternalApiError


def test_strip_optional_json_fence_handles_plain_and_fenced_text() -> None:
    assert _strip_optional_json_fence("  {\"ok\":true}  ") == '{"ok":true}'
    assert _strip_optional_json_fence("```json\n{\"ok\": true}\n```") == '{"ok": true}'
    assert _strip_optional_json_fence("```\n{\"ok\": true}\n```") == '{"ok": true}'


def test_extract_first_json_object_handles_nested_and_string_escapes() -> None:
    raw = 'prefix {"a": {"b": 1, "c": "{\\"d\\": 2}"}} suffix'
    assert _extract_first_json_object(raw) == '{"a": {"b": 1, "c": "{\\"d\\": 2}"}}'
    assert _extract_first_json_object("no-json-here") is None


def test_parse_json_object_loose_accepts_fenced_or_embedded_json() -> None:
    assert _parse_json_object_loose("```json\n{\"x\": 1}\n```") == {"x": 1}
    assert _parse_json_object_loose("junk {\"x\": 2, \"y\": 3} trailing") == {"x": 2, "y": 3}
    assert _parse_json_object_loose("not-json") is None


def test_extract_message_text_supports_string_and_rich_content() -> None:
    assert _extract_message_text({"content": "  hello "}) == "hello"
    assert (
        _extract_message_text({"content": [{"type": "text", "text": "hello"}, {"type": "other"}]})
        == "hello"
    )
    assert _extract_message_text({"content": []}) == ""


def test_provider_post_to_wraps_transport_and_response_shape_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            httpx.TimeoutException("slow"),
            httpx.ConnectError("down"),
            httpx.Response(429, json={"error": "rate"}),
            httpx.Response(200, content=b"not-json"),
            httpx.Response(200, json=["not-object"]),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.httpx.Client",
        lambda **kwargs: RealHttpxClient(transport=httpx.MockTransport(handler), **kwargs),
    )
    provider = OpenAICompatibleProvider(base_url="https://ai.example", model_name="m", api_key="k", timeout_seconds=5)

    with pytest.raises(ExternalApiError) as timeout_exc:
        provider._post_to("/v1/chat/completions", {"x": 1})
    with pytest.raises(ExternalApiError) as transport_exc:
        provider._post_to("/v1/chat/completions", {"x": 1})
    with pytest.raises(ExternalApiError) as http_exc:
        provider._post_to("/v1/chat/completions", {"x": 1})
    with pytest.raises(ExternalApiError) as json_exc:
        provider._post_to("/v1/chat/completions", {"x": 1})
    with pytest.raises(ExternalApiError) as shape_exc:
        provider._post_to("/v1/chat/completions", {"x": 1})

    ok = provider._post_to("/v1/chat/completions", {"x": 1})

    assert captured[0].headers["Authorization"] == "Bearer k"
    assert timeout_exc.value.code == "EXTERNAL_AI_GATEWAY_TIMEOUT"
    assert transport_exc.value.code == "EXTERNAL_AI_GATEWAY_TRANSPORT"
    assert http_exc.value.code == "EXTERNAL_AI_GATEWAY_HTTP_ERROR"
    assert http_exc.value.retryable is True
    assert json_exc.value.code == "EXTERNAL_AI_GATEWAY_INVALID_RESPONSE"
    assert shape_exc.value.code == "EXTERNAL_AI_GATEWAY_INVALID_RESPONSE"
    assert ok == {"ok": True}


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
def test_invoke_text_rejects_invalid_response_shapes(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    monkeypatch.setattr(OpenAICompatibleProvider, "_post", lambda self, p: payload)
    provider = OpenAICompatibleProvider(base_url="https://ai.example", model_name="m")

    with pytest.raises(ExternalApiError) as exc_info:
        provider.invoke_text(messages=[{"role": "user", "content": "hi"}])

    assert exc_info.value.code == "EXTERNAL_AI_GATEWAY_INVALID_RESPONSE"


def test_invoke_json_retries_when_truncated_then_parses_loose_json(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            {
                "choices": [
                    {
                        "message": {"content": "{\"ok\": true"},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"total_tokens": 1},
            },
            {
                "choices": [
                    {
                        "message": {"content": "```json\n{\"ok\": true, \"n\": 2}\n```"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 2},
            },
        ]
    )
    monkeypatch.setattr(OpenAICompatibleProvider, "_post", lambda self, p: next(responses))
    provider = OpenAICompatibleProvider(base_url="https://ai.example", model_name="m", max_tokens=10)

    payload, usage = provider.invoke_json(messages=[{"role": "user", "content": "hi"}])

    assert payload == {"ok": True, "n": 2}
    assert usage["total_tokens"] == 2


def test_invoke_json_rejects_invalid_json_when_not_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        OpenAICompatibleProvider,
        "_post",
        lambda self, p: {
            "choices": [{"message": {"content": "{\"ok\": true"}, "finish_reason": "stop"}],
        },
    )
    provider = OpenAICompatibleProvider(base_url="https://ai.example", model_name="m", max_tokens=10)

    with pytest.raises(ExternalApiError) as exc_info:
        provider.invoke_json(messages=[{"role": "user", "content": "hi"}])

    assert exc_info.value.code == "EXTERNAL_AI_GATEWAY_INVALID_RESPONSE"


def test_invoke_embedding_validates_shape_and_casts_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            {"data": []},
            {"data": ["bad"]},
            {"data": [{"embedding": ["bad"]}]},
            {"data": [{"embedding": [1, 2.5]}], "usage": {"total_tokens": 5}},
        ]
    )
    monkeypatch.setattr(OpenAICompatibleProvider, "_post_to", lambda self, path, payload: next(responses))
    provider = OpenAICompatibleProvider(base_url="https://ai.example", model_name="m")

    with pytest.raises(ExternalApiError):
        provider.invoke_embedding(text="x")
    with pytest.raises(ExternalApiError):
        provider.invoke_embedding(text="x")
    with pytest.raises(ExternalApiError):
        provider.invoke_embedding(text="x")

    vector, usage = provider.invoke_embedding(text="x")

    assert vector == [1.0, 2.5]
    assert usage["total_tokens"] == 5


def test_invoke_rerank_normalizes_results_and_limits_top_k(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        OpenAICompatibleProvider,
        "invoke_json",
        lambda self, messages, temperature=0.0: (
            {
                "results": [
                    {"index": 1, "score": 0.9, "reason": "ok"},
                    {"index": "bad", "score": 0.1},
                    {"index": 2, "score": 0.7},
                ]
            },
            {"total_tokens": 1},
        ),
    )
    provider = OpenAICompatibleProvider(base_url="https://ai.example", model_name="m")

    ranked, usage = provider.invoke_rerank(query="q", items=[{"x": 1}], top_k=1)

    assert ranked == [{"index": 1, "score": 0.9, "reason": "ok"}]
    assert usage["total_tokens"] == 1


def test_invoke_rerank_requires_results_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(OpenAICompatibleProvider, "invoke_json", lambda self, messages, temperature=0.0: ({}, {}))
    provider = OpenAICompatibleProvider(base_url="https://ai.example", model_name="m")

    with pytest.raises(ExternalApiError) as exc_info:
        provider.invoke_rerank(query="q", items=[], top_k=2)

    assert exc_info.value.code == "EXTERNAL_AI_GATEWAY_INVALID_RESPONSE"


def test_ensure_openai_compatible_provider_validates_provider_type(monkeypatch: pytest.MonkeyPatch) -> None:
    row = SimpleNamespace(
        base_url="https://ai.example",
        model_name="m",
        api_key=None,
        capabilities_json=None,
        provider_type="not-openai",
    )
    with pytest.raises(ConfigurationError):
        ensure_openai_compatible_provider(row)

    monkeypatch.delenv("AGENT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("AI_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_LLM_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("AI_GATEWAY_DEFAULT_MODEL", raising=False)
    with pytest.raises(ConfigurationError):
        ensure_openai_compatible_provider(None)

    monkeypatch.setenv("AGENT_LLM_BASE_URL", "https://ai.example")
    monkeypatch.setenv("AGENT_LLM_DEFAULT_MODEL", "m")
    provider = ensure_openai_compatible_provider(None)
    assert provider.base_url == "https://ai.example"
    assert provider.model_name == "m"

