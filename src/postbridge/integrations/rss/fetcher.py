"""Fetcher для чтения постов из любой RSS-ленты."""

from __future__ import annotations

import asyncio
import feedparser

from postbridge.api.schemas import RssCredentials
from postbridge.config import Settings, get_settings
from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.domain.models import PostPayload


def _resolve_rss_url(source_channel: str, creds: RssCredentials | None) -> str:
    """Определяет URL RSS-ленты по source_channel или credentials."""
    s = source_channel.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if creds and creds.rss_url:
        return creds.rss_url
    raise ConfigurationError(
        "RSS source_channel must be full URL (https://...) or provide rss_url in credentials."
    )


class RSSFetcher:
    """Клиент для импорта постов из любой RSS-ленты."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    async def fetch_posts(
        self,
        source_channel: str,
        limit: int,
        credentials: RssCredentials | None = None,
        *,
        tenant_id: str | None = None,
    ) -> list[PostPayload]:
        """Забирает посты из RSS-ленты. Возвращает в хронологическом порядке."""
        _ = tenant_id
        rss_url = _resolve_rss_url(source_channel, credentials)
        try:
            entries = await asyncio.to_thread(
                self._fetch_sync,
                rss_url,
                limit,
            )
        except ExternalApiError:
            raise
        except Exception as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_RSS_FETCH_ERROR",
                message="RSS fetch failed",
                source="rss",
                retryable=True,
                details={
                    "source_channel": source_channel,
                    "rss_url": rss_url[:100],
                    "reason": str(exc),
                },
            ) from exc

        posts: list[PostPayload] = []
        for e in entries:
            guid = getattr(e, "id", None) or getattr(e, "link", "") or ""
            title = getattr(e, "title", "") or ""
            summary = getattr(e, "summary", "") or ""
            content_val = ""
            if hasattr(e, "content") and e.content:
                first = e.content[0] if isinstance(e.content, list) else {}
                content_val = first.get("value", "") if isinstance(first, dict) else ""
            content = summary or content_val
            text = f"{title}\n\n{content}".strip() if content else title
            media_url = None
            if hasattr(e, "media_content") and e.media_content:
                media_url = e.media_content[0].get("url")
            elif hasattr(e, "links"):
                for link in e.links:
                    if link.get("type", "").startswith("image/"):
                        media_url = link.get("href")
                        break
            posts.append(
                PostPayload(
                    source_post_id=guid or str(len(posts)),
                    text=text[:50000] if text else "",
                    media_url=media_url,
                )
            )
        return posts

    def _fetch_sync(self, rss_url: str, limit: int) -> list:
        """Синхронный парсинг RSS."""
        parsed = feedparser.parse(
            rss_url,
            agent="Postbridge/1.0",
        )
        if parsed.bozo and not parsed.entries:
            exc = getattr(parsed, "bozo_exception", None)
            raise ExternalApiError(
                code="EXTERNAL_API_RSS_FETCH_ERROR",
                message="Invalid or unreachable RSS feed",
                source="rss",
                retryable=True,
                details={"rss_url": rss_url[:100], "reason": str(exc) if exc else "parse error"},
            )
        return list(parsed.entries[:limit])
