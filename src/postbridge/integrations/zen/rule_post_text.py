"""Rule-based адаптация текста под Дзен."""

from __future__ import annotations

from typing import Any

from postbridge.integrations.text_rule.common import truncate_at_word

_LIMIT = 1200


def adapt_post_dict(post: dict[str, Any]) -> str:
    text = post.get("text") or ""
    title = post.get("title") or ""
    summary = post.get("summary") or ""
    cta = post.get("cta") or ""
    link_url = post.get("link_url") or ""

    parts: list[str] = []
    if title:
        parts.append(str(title).strip())
    if summary:
        parts.append(str(summary).strip())
    body = (text or "").strip() if isinstance(text, str) else ""
    if body:
        parts.append(body)

    combined = "\n\n".join(p for p in parts if p)
    if cta:
        combined = f"{combined}\n\n{cta}".strip()
    if link_url:
        combined = f"{combined}\n\n{link_url}".strip()
    return truncate_at_word(combined, _LIMIT)
