"""External integrations: модули по платформам (fetcher/publisher)."""

from postbridge.integrations.base import ChannelAdapter, SourceFetcher, TargetPublisher
from postbridge.integrations.linkedin.publisher import LinkedInPublisher
from postbridge.integrations.max.fetcher import MaxFetcher
from postbridge.integrations.max.publisher import MaxPublisher
from postbridge.integrations.registry import (
    FETCHERS,
    PUBLISHERS,
    RegistryChannelAdapter,
    get_adapter,
    get_fetcher,
    get_publisher,
)
from postbridge.integrations.rss.fetcher import RSSFetcher
from postbridge.integrations.rss.publisher import RSSPublisher
from postbridge.integrations.telegram.fetcher import TelegramFetcher
from postbridge.integrations.telegram.publisher import TelegramPublisher
from postbridge.integrations.vk.fetcher import VKFetcher
from postbridge.integrations.vk.publisher import VKPublisher
from postbridge.integrations.zen.fetcher import ZenFetcher
from postbridge.integrations.zen.publisher import ZenPublisher

__all__ = [
    "ChannelAdapter",
    "RegistryChannelAdapter",
    "SourceFetcher",
    "TargetPublisher",
    "TelegramFetcher",
    "TelegramPublisher",
    "MaxFetcher",
    "MaxPublisher",
    "LinkedInPublisher",
    "VKFetcher",
    "VKPublisher",
    "ZenFetcher",
    "ZenPublisher",
    "RSSFetcher",
    "RSSPublisher",
    "FETCHERS",
    "PUBLISHERS",
    "get_adapter",
    "get_fetcher",
    "get_publisher",
]
