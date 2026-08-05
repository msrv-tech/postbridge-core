"""Rule-based text adaptation for Instagram."""

from __future__ import annotations

from postbridge.integrations.text_rule.common import truncate_at_word

INSTAGRAM_CAPTION_LIMIT = 2200


def adapt_post_dict(post: dict[str, object]) -> str:
    return truncate_at_word(str(post.get("text") or "").strip(), INSTAGRAM_CAPTION_LIMIT)
