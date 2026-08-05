"""Rule-based text adaptation for Mastodon."""

from __future__ import annotations

from postbridge.integrations.text_rule.common import truncate_at_word

MASTODON_TEXT_LIMIT = 500


def adapt_post_dict(post: dict[str, object]) -> str:
    return truncate_at_word(str(post.get("text") or "").strip(), MASTODON_TEXT_LIMIT)
