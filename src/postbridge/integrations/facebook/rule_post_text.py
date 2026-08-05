"""Rule-based text adaptation for Facebook Pages."""

from __future__ import annotations

from postbridge.integrations.text_rule.common import truncate_at_word

FACEBOOK_TEXT_LIMIT = 63206


def adapt_post_dict(post: dict[str, object]) -> str:
    return truncate_at_word(str(post.get("text") or "").strip(), FACEBOOK_TEXT_LIMIT)
