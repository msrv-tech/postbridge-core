"""Базовые протоколы для fetcher и publisher по платформам."""

from __future__ import annotations

from typing import Any, Protocol

from postbridge.domain.models import PostPayload


class SourceFetcher(Protocol):
    """Протокол для чтения постов из канала (источник)."""

    async def fetch_posts(
        self,
        source_channel: str,
        limit: int,
        credentials: Any,
        *,
        tenant_id: str | None = None,
    ) -> list[PostPayload]: ...


class TargetPublisher(Protocol):
    """Протокол для публикации постов в канал (приёмник)."""

    def publish_post(
        self,
        target_channel: str,
        post: PostPayload,
        credentials: Any,
    ) -> str | None: ...


class ChannelAdapter(Protocol):
    """Единый фасад fetch + publish для платформы (architecture §4.4, фаза 5)."""

    @property
    def platform(self) -> str: ...

    def get_capabilities(self) -> dict[str, Any]: ...

    async def fetch_posts(
        self,
        source_channel: str,
        limit: int,
        credentials: Any,
        *,
        tenant_id: str | None = None,
    ) -> list[PostPayload]: ...

    def publish_post(
        self,
        target_channel: str,
        post: PostPayload,
        credentials: Any,
    ) -> str | None: ...
