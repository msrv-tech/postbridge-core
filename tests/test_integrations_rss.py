"""Unit tests for RSS fetcher and publisher."""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import func, select

from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.domain.models import PostPayload
from postbridge.integrations.rss.fetcher import RSSFetcher, _resolve_rss_url
from postbridge.integrations.rss.publisher import RSSPublisher
from postbridge.integrations.registry import get_fetcher, get_publisher
from postbridge.db import Base, ENGINE, RssFeedItemOrm, SESSION_LOCAL, init_db
from postbridge.models.domain import ContentItemOrm, TenantOrm
from postbridge.services.postbridge_workspace_content import delete_postbridge_content_item


def _make_entry(entry_id: str, title: str, summary: str = "", link: str = "") -> MagicMock:
    e = MagicMock()
    e.id = entry_id
    e.title = title
    e.summary = summary
    e.link = link or f"https://example.com/{entry_id}"
    e.content = []
    e.media_content = []
    e.links = []
    return e


@pytest.fixture(autouse=True)
def _init_db():
    Base.metadata.create_all(bind=ENGINE)
    yield
    Base.metadata.drop_all(bind=ENGINE)


class TestResolveRssUrl:
    def test_resolve_from_source_channel_https(self):
        assert _resolve_rss_url("https://example.com/feed.xml", None) == "https://example.com/feed.xml"

    def test_resolve_from_source_channel_http(self):
        assert _resolve_rss_url("http://blog.test/rss", None) == "http://blog.test/rss"

    def test_resolve_from_credentials(self):
        from postbridge.api.schemas import RssCredentials
        creds = RssCredentials(rss_url="https://dzen.ru/feed")
        assert _resolve_rss_url("", creds) == "https://dzen.ru/feed"
        assert _resolve_rss_url("not-a-url", creds) == "https://dzen.ru/feed"

    def test_resolve_fails_without_url(self):
        with pytest.raises(ConfigurationError) as exc_info:
            _resolve_rss_url("", None)
        assert "RSS source_channel must be full URL" in str(exc_info.value)

    def test_resolve_fails_with_invalid_channel(self):
        with pytest.raises(ConfigurationError) as exc_info:
            _resolve_rss_url("zen/123", None)
        assert "full URL" in str(exc_info.value)


class TestRSSFetcher:
    def test_fetch_posts_parses_entries(self):
        import asyncio

        entries = [
            _make_entry("e1", "Title 1", "Summary 1"),
            _make_entry("e2", "Title 2", "Content 2"),
        ]
        with patch("postbridge.integrations.rss.fetcher.feedparser") as mock_fp:
            mock_fp.parse.return_value = MagicMock(bozo=False, entries=entries)
            fetcher = RSSFetcher()

            async def run():
                return await fetcher.fetch_posts(
                    source_channel="https://example.com/feed.xml",
                    limit=10,
                )

            posts = asyncio.run(run())
        assert len(posts) == 2
        assert posts[0].source_post_id == "e1"
        assert posts[0].text == "Title 1\n\nSummary 1"
        assert posts[1].source_post_id == "e2"
        assert posts[1].text == "Title 2\n\nContent 2"

    def test_fetch_posts_respects_limit(self):
        import asyncio

        entries = [_make_entry(f"e{i}", f"Title {i}") for i in range(5)]
        with patch("postbridge.integrations.rss.fetcher.feedparser") as mock_fp:
            mock_fp.parse.return_value = MagicMock(bozo=False, entries=entries)
            fetcher = RSSFetcher()

            async def run():
                return await fetcher.fetch_posts(
                    source_channel="https://example.com/feed.xml",
                    limit=2,
                )

            posts = asyncio.run(run())
        assert len(posts) == 2

    def test_fetch_posts_raises_on_invalid_feed(self):
        import asyncio

        with patch("postbridge.integrations.rss.fetcher.feedparser") as mock_fp:
            mock_fp.parse.return_value = MagicMock(bozo=True, entries=[])
            fetcher = RSSFetcher()

            async def run():
                await fetcher.fetch_posts(
                    source_channel="https://example.com/bad.xml",
                    limit=10,
                )

            with pytest.raises(ExternalApiError) as exc_info:
                asyncio.run(run())
        assert exc_info.value.code == "EXTERNAL_API_RSS_FETCH_ERROR"
        assert exc_info.value.source == "rss"


class TestRSSPublisher:
    def test_publish_post_stores_in_db(self):
        init_db()
        publisher = RSSPublisher()
        post = PostPayload(source_post_id="p1", text="Hello RSS", media_url=None)
        result = publisher.publish_post(target_channel="feed-abc", payload=post)
        assert result == "p1"
        session = SESSION_LOCAL()
        try:
            row = session.scalar(
                select(RssFeedItemOrm).where(
                    RssFeedItemOrm.feed_id == "feed-abc",
                    RssFeedItemOrm.source_post_id == "p1",
                )
            )
            assert row is not None
            assert row.text == "Hello RSS"
        finally:
            session.close()

    def test_publish_post_deduplicates(self):
        init_db()
        publisher = RSSPublisher()
        post = PostPayload(source_post_id="p2", text="Dup", media_url=None)
        r1 = publisher.publish_post(target_channel="feed-xyz", payload=post)
        r2 = publisher.publish_post(target_channel="feed-xyz", payload=post)
        assert r1 == r2 == "p2"
        session = SESSION_LOCAL()
        try:
            count = session.scalar(
                select(func.count()).select_from(RssFeedItemOrm).where(
                    RssFeedItemOrm.feed_id == "feed-xyz"
                )
            )
            assert count == 1
        finally:
            session.close()

    def test_publish_post_returns_none_for_empty_feed_id(self):
        publisher = RSSPublisher()
        post = PostPayload(source_post_id="p3", text="x", media_url=None)
        assert publisher.publish_post(target_channel="", payload=post) is None
        assert publisher.publish_post(target_channel="   ", payload=post) is None

    def test_delete_postbridge_content_removes_rss_feed_items(self):
        init_db()
        session = SESSION_LOCAL()
        try:
            tenant = TenantOrm(id="tenant-1", name="Tenant")
            session.add(tenant)
            session.flush()
            content = ContentItemOrm(
                id="content-1",
                tenant_id=tenant.id,
                source_type="postbridge",
                title="Deleted post",
                body_markdown="Deleted post",
                status="published",
            )
            rss_item = RssFeedItemOrm(
                feed_id="feed-abc",
                source_channel="pb/tenant-1",
                source_post_id=content.id,
                text="Deleted post",
            )
            session.add_all([content, rss_item])
            session.flush()

            delete_postbridge_content_item(session, row=content)
            session.commit()

            stale = session.scalar(
                select(RssFeedItemOrm).where(
                    RssFeedItemOrm.feed_id == "feed-abc",
                    RssFeedItemOrm.source_post_id == "content-1",
                )
            )
            assert stale is None
        finally:
            session.close()


class TestRegistry:
    def test_get_fetcher_rss(self):
        fetcher = get_fetcher("rss")
        assert isinstance(fetcher, RSSFetcher)

    def test_get_publisher_rss(self):
        publisher = get_publisher("rss")
        assert isinstance(publisher, RSSPublisher)
