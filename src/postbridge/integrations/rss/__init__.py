"""RSS integration: fetcher и publisher для чтения/публикации постов."""

from postbridge.integrations.rss.fetcher import RSSFetcher
from postbridge.integrations.rss.publisher import RSSPublisher

__all__ = ["RSSFetcher", "RSSPublisher"]
