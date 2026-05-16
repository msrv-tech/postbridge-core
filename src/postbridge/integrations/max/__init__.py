"""MAX integration: fetcher и publisher для чтения/публикации постов."""

from postbridge.integrations.max.fetcher import MaxFetcher
from postbridge.integrations.max.publisher import MaxPublisher

__all__ = ["MaxFetcher", "MaxPublisher"]
