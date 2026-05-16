"""Zen (Дзен) integration: fetcher и publisher для чтения/публикации статей."""

from postbridge.integrations.zen.fetcher import ZenFetcher
from postbridge.integrations.zen.publisher import ZenPublisher

__all__ = ["ZenFetcher", "ZenPublisher"]
