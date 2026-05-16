"""Парсинг JSON-ответа generate (режим messages)."""

import pytest

from postbridge.ai.json_generate_reply import (
    GENERATE_JSON_SYSTEM_MESSAGE,
    extract_first_json_object,
    postprocess_generate_chat_json,
    strip_optional_json_fence,
    try_parse_generate_json_reply,
    try_parse_generate_json_reply_loose,
)
from postbridge.ai.schemas import GatewayChatMessage, GatewayGenerateRequest, GatewayTextResponse
from postbridge.domain.errors import ValidationError


def test_strip_json_fence() -> None:
    raw = '```json\n{"title": "A"}\n```'
    assert '"title"' in strip_optional_json_fence(raw)


def test_try_parse_valid() -> None:
    p = try_parse_generate_json_reply('{"title":"T","body_markdown":"B"}')
    assert p == {"title": "T", "body_markdown": "B"}


def test_try_parse_ignores_unknown_keys() -> None:
    p = try_parse_generate_json_reply('{"title":"T","extra":1}')
    assert p == {"title": "T"}


def test_try_parse_invalid_returns_none() -> None:
    assert try_parse_generate_json_reply("not json") is None
    assert try_parse_generate_json_reply('{"title":1}') is None


def test_postprocess_messages_mode_parses_json() -> None:
    req = GatewayGenerateRequest(
        messages=[GatewayChatMessage(role="user", content="hi")],
        prompt=None,
    )
    gw0 = GatewayTextResponse(
        body_text='{"title":"X","body_markdown":"Y","hashtags":"#a","mentions":"","link_url":null}'
    )
    out = postprocess_generate_chat_json(gw0, req)
    assert out.title == "X"
    assert out.body_text == "Y"
    assert out.hashtags == "#a"
    assert out.source_assistant_text


def test_postprocess_plain_markdown_raises() -> None:
    req = GatewayGenerateRequest(
        messages=[GatewayChatMessage(role="user", content="hi")],
        prompt=None,
    )
    gw0 = GatewayTextResponse(body_text="Just **markdown**")
    with pytest.raises(ValidationError) as exc_info:
        postprocess_generate_chat_json(gw0, req)
    assert exc_info.value.code == "VALIDATION_AI_GENERATE_JSON_REPLY"


def test_postprocess_empty_assistant_raises() -> None:
    req = GatewayGenerateRequest(
        messages=[GatewayChatMessage(role="user", content="hi")],
        prompt=None,
    )
    gw0 = GatewayTextResponse(body_text="")
    with pytest.raises(ValidationError) as exc_info:
        postprocess_generate_chat_json(gw0, req)
    assert exc_info.value.code == "VALIDATION_AI_GENERATE_JSON_REPLY"


def test_postprocess_all_null_json_raises() -> None:
    req = GatewayGenerateRequest(
        messages=[GatewayChatMessage(role="user", content="hi")],
        prompt=None,
    )
    gw0 = GatewayTextResponse(body_text='{"title":null,"body_markdown":null}')
    with pytest.raises(ValidationError) as exc_info:
        postprocess_generate_chat_json(gw0, req)
    assert exc_info.value.code == "VALIDATION_AI_GENERATE_JSON_REPLY"


def test_try_parse_loose_with_preamble() -> None:
    raw = 'Here you go:\n{"title":"A","body_markdown":"B"}\nThanks'
    p = try_parse_generate_json_reply_loose(raw)
    assert p == {"title": "A", "body_markdown": "B"}


def test_extract_first_json_object_nested_string() -> None:
    blob = extract_first_json_object('x {"a":"brace { in value}"} y')
    assert blob == '{"a":"brace { in value}"}'


def test_postprocess_prompt_only_unchanged() -> None:
    req = GatewayGenerateRequest(prompt="x", messages=None)
    gw0 = GatewayTextResponse(body_text='{"title":"ignored"}')
    out = postprocess_generate_chat_json(gw0, req)
    assert out.body_text == '{"title":"ignored"}'


def test_generate_json_system_message_nonempty() -> None:
    assert "body_markdown" in GENERATE_JSON_SYSTEM_MESSAGE
