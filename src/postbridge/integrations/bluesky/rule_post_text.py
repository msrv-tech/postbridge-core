"""Rule-based text adaptation for Bluesky."""

from __future__ import annotations

from postbridge.integrations.text_rule.common import truncate_at_word

BLUESKY_TEXT_LIMIT = 300


def adapt_post_dict(post: dict[str, object]) -> str:
    return truncate_at_word(str(post.get("text") or "").strip(), BLUESKY_TEXT_LIMIT)
