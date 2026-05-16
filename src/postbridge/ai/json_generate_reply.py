"""Парсинг JSON-ответа ассистента в режиме generate с messages (редактор)."""

from __future__ import annotations

import json
from typing import Any

from postbridge.ai.schemas import GatewayGenerateRequest, GatewayTextResponse
from postbridge.domain.errors import ValidationError

_JSON_REPLY_PREVIEW_LEN = 500
_ACTION_KEYS = ("title", "body_markdown", "hashtags", "mentions", "link_url")

# Поля из ответа модели (whitelist)
_KNOWN_KEYS = frozenset(
    {"title", "body_markdown", "hashtags", "mentions", "link_url"}
)

GENERATE_JSON_SYSTEM_MESSAGE = (
    "You must reply with a single JSON object only, no text before or after it, "
    "no markdown code fences. Schema (use null to leave a field unchanged when refining):\n"
    '{"title": string|null, "body_markdown": string|null, '
    '"hashtags": string|null, "mentions": string|null, "link_url": string|null}\n'
    "Rules: omit keys you do not need; null means do not change that part of the post. "
    "For body_markdown use GitHub-flavored markdown when non-null."
)


def strip_optional_json_fence(raw: str) -> str:
    s = raw.strip()
    if not s.startswith("```"):
        return s
    lines = s.split("\n")
    if not lines:
        return s
    if lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def extract_first_json_object(raw: str) -> str | None:
    """Первый сбалансированный JSON-объект по фигурным скобкам (игнорируя скобки внутри строк)."""
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(raw)):
        c = raw[i]
        if escape:
            escape = False
            continue
        if c == "\\" and in_str:
            escape = True
            continue
        if c == '"' and not escape:
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return None


def try_parse_generate_json_reply(raw: str) -> dict[str, Any] | None:
    """Возвращает dict с ключами из whitelist или None при невалидном JSON/типах."""
    cleaned = strip_optional_json_fence(raw)
    if not cleaned:
        return None
    try:
        obj = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    out: dict[str, Any] = {}
    for k, v in obj.items():
        if k not in _KNOWN_KEYS:
            continue
        if v is None:
            out[k] = None
            continue
        if k in ("title", "body_markdown", "hashtags", "mentions", "link_url"):
            if isinstance(v, str):
                sv = v.strip()
                out[k] = sv if sv else None
            else:
                return None
    return out


def try_parse_generate_json_reply_loose(raw: str) -> dict[str, Any] | None:
    """Как try_parse_generate_json_reply, плюс попытка вырезать вложенный `{...}` из текста с преамбулой."""
    p = try_parse_generate_json_reply(raw)
    if p is not None:
        return p
    blob = extract_first_json_object(raw)
    if blob:
        return try_parse_generate_json_reply(blob)
    return None


def gateway_text_response_from_parsed_json(
    parsed: dict[str, Any],
    *,
    usage: Any = None,
    total_tokens: int | None = None,
    source_assistant_text: str | None = None,
) -> GatewayTextResponse:
    title = parsed.get("title")
    body = parsed.get("body_markdown")
    hashtags = parsed.get("hashtags")
    mentions = parsed.get("mentions")
    link_url = parsed.get("link_url")
    return GatewayTextResponse(
        title=title if title is not None else None,
        body_text=body if body is not None else None,
        hashtags=hashtags if hashtags is not None else None,
        mentions=mentions if mentions is not None else None,
        link_url=link_url if link_url is not None else None,
        usage=usage,
        total_tokens=total_tokens,
        source_assistant_text=source_assistant_text,
    )


def _raise_invalid_editor_json_reply(raw_text: str) -> None:
    preview = raw_text[:_JSON_REPLY_PREVIEW_LEN]
    if len(raw_text) > _JSON_REPLY_PREVIEW_LEN:
        preview += "…"
    raise ValidationError(
        code="VALIDATION_AI_GENERATE_JSON_REPLY",
        message="Модель вернула ответ не в ожидаемом JSON; черновик не изменён.",
        details={"assistant_preview": preview},
    )


def postprocess_generate_chat_json(
    gw_raw: GatewayTextResponse, req: GatewayGenerateRequest
) -> GatewayTextResponse:
    """Если req.messages — разбор body_text как JSON объекта редактора; иначе без изменений."""
    if not req.messages:
        return gw_raw
    raw_text = (gw_raw.body_text or "").strip()
    if not raw_text:
        _raise_invalid_editor_json_reply("")
    parsed = try_parse_generate_json_reply_loose(raw_text)
    if parsed is None:
        _raise_invalid_editor_json_reply(raw_text)
    if not any(parsed.get(k) is not None for k in _ACTION_KEYS):
        _raise_invalid_editor_json_reply(raw_text)
    return gateway_text_response_from_parsed_json(
        parsed,
        usage=gw_raw.usage,
        total_tokens=gw_raw.total_tokens,
        source_assistant_text=raw_text,
    )


def last_user_message_text(messages: list[dict[str, str]]) -> str | None:
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            t = m["content"].strip()
            if t:
                return t
    return None
