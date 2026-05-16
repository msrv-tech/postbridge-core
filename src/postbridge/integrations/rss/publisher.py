"""Publisher для публикации постов в RSS-ленту (сохранение в rss_feed_items)."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from postbridge.db import RssFeedItemOrm, SESSION_LOCAL
from postbridge.domain.models import PostPayload


class RSSPublisher:
    """Клиент для публикации постов в RSS-ленту (target_channel = feed_id)."""

    def publish_post(
        self,
        target_channel: str,
        payload: PostPayload,
        credentials: object = None,
    ) -> str | None:
        """Сохраняет пост в rss_feed_items. target_channel = feed_id."""
        feed_id = target_channel.strip()
        if not feed_id:
            return None
        media_urls_json = None
        if payload.media_urls:
            media_urls_json = json.dumps(payload.media_urls)
        session: Session = SESSION_LOCAL()
        try:
            existing = session.scalar(
                select(RssFeedItemOrm).where(
                    RssFeedItemOrm.feed_id == feed_id,
                    RssFeedItemOrm.source_post_id == payload.source_post_id,
                )
            )
            if existing:
                return payload.source_post_id
            item = RssFeedItemOrm(
                feed_id=feed_id,
                source_channel="",  # заполняется из контекста live_sync
                source_post_id=payload.source_post_id,
                text=payload.text or "",
                media_url=payload.media_url,
                media_urls_json=media_urls_json,
            )
            session.add(item)
            session.commit()
            return payload.source_post_id
        finally:
            session.close()

    def edit_message(
        self,
        message_id: str,
        text: str = "",
        media_url: str | None = None,
        media_urls: list[str] | None = None,
        credentials: object = None,
        target_channel: str | None = None,
    ) -> None:
        """Обновляет пост в rss_feed_items. message_id = source_post_id, target_channel = feed_id."""
        if not target_channel:
            return
        feed_id = target_channel.strip()
        if not feed_id:
            return
        media_urls_json = None
        if media_urls:
            media_urls_json = json.dumps(media_urls)
        session: Session = SESSION_LOCAL()
        try:
            item = session.scalar(
                select(RssFeedItemOrm).where(
                    RssFeedItemOrm.feed_id == feed_id,
                    RssFeedItemOrm.source_post_id == message_id,
                )
            )
            if item:
                item.text = text or ""
                item.media_url = media_url
                item.media_urls_json = media_urls_json
                session.commit()
        finally:
            session.close()
