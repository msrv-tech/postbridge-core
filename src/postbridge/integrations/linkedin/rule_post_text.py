from __future__ import annotations

from typing import Any

from postbridge.integrations.text_rule.common import truncate_at_word

LINKEDIN_TEXT_LIMIT = 3000


def adapt_post_dict(post: dict[str, Any]) -> str:
    parts: list[str] = []
    title = (post.get("title") or "").strip()
    text = (post.get("text") or "").strip()
    cta = (post.get("cta") or "").strip()
    link_url = (post.get("link_url") or "").strip()
    if title:
        parts.append(title)
    if text:
        parts.append(text)
    if cta:
        parts.append(cta)
    if link_url:
        parts.append(link_url)
    return truncate_at_word("\n\n".join(parts), LINKEDIN_TEXT_LIMIT)
