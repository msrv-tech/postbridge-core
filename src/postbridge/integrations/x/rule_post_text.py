"""Rule-based text adaptation for X."""

from __future__ import annotations

from postbridge.integrations.text_rule.common import truncate_at_word

X_TEXT_LIMIT = 280


def adapt_post_dict(post: dict[str, object]) -> str:
    return truncate_at_word(str(post.get("text") or "").strip(), X_TEXT_LIMIT)
