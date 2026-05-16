"""Rule-based адаптация текста под Telegram."""

from __future__ import annotations

from typing import Any

from postbridge.integrations.text_rule.common import truncate_at_word

_LIMIT = 4096


def adapt_post_dict(post: dict[str, Any]) -> str:
    text = post.get("text") or ""
    title = post.get("title") or ""
    cta = post.get("cta") or ""
    link_url = post.get("link_url") or ""

    parts: list[str] = []
    if title:
        parts.append(str(title).strip())
    body = (text or "").strip() if isinstance(text, str) else ""
    if body:
        parts.append(body)

    result = "\n\n".join(p for p in parts if p)
    if cta:
        result = f"{result}\n\n{cta}".strip()
    if link_url:
        result = f"{result}\n\n{link_url}".strip()
    if len(result) > _LIMIT:
        result = truncate_at_word(result, _LIMIT)
    return result
