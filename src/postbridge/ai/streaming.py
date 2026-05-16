"""Разбор OpenAI-стиля SSE для chat/completions со stream=true."""

from __future__ import annotations

import json
from typing import Any, Iterator

from postbridge.ai.schemas import GatewayUsageStats


def _delta_content_to_fragment(content: Any) -> str | None:
    """Извлекает текст из delta.content (строка или список частей, как у OpenAI multimodal)."""
    if isinstance(content, str):
        return content if content else None
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, dict):
                if p.get("type") == "text" and isinstance(p.get("text"), str):
                    parts.append(p["text"])
                elif isinstance(p.get("content"), str):
                    parts.append(p["content"])
        joined = "\n".join(parts).strip()
        return joined if joined else None
    return None


def extract_stream_delta_text(obj: dict[str, Any]) -> str | None:
    """Текстовый фрагмент из одного JSON-чанка стрима (choices[0].delta.content)."""
    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    c0 = choices[0]
    if not isinstance(c0, dict):
        return None
    delta = c0.get("delta")
    if not isinstance(delta, dict):
        return None
    return _delta_content_to_fragment(delta.get("content"))


def extract_usage_from_chunk(obj: dict[str, Any]) -> GatewayUsageStats | None:
    raw_u = obj.get("usage")
    if not isinstance(raw_u, dict):
        return None
    return GatewayUsageStats(
        total_tokens=raw_u.get("total_tokens"),
        prompt_tokens=raw_u.get("prompt_tokens"),
        completion_tokens=raw_u.get("completion_tokens"),
    )


def root_total_tokens(obj: dict[str, Any]) -> int | None:
    t = obj.get("total_tokens")
    if isinstance(t, int):
        return max(0, t)
    return None


def iter_openai_sse_json_payloads(line_iter: Iterator[str]) -> Iterator[dict[str, Any]]:
    """
    Читает строки из httpx iter_lines(); для каждой строки data: {...} отдаёт распарсенный объект.
    Пустые строки и комментарии : пропускаются; [DONE] завершает итерацию без yield.
    """
    for raw_line in line_iter:
        if raw_line is None:
            continue
        line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", errors="replace")
        line = line.rstrip("\r\n")
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            break
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj
