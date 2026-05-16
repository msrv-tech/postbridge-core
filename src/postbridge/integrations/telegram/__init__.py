"""Telegram integration: fetcher и publisher для чтения/публикации постов."""

from postbridge.integrations.telegram.fetcher import TelegramFetcher
from postbridge.integrations.telegram.publisher import TelegramPublisher

__all__ = ["TelegramFetcher", "TelegramPublisher"]
