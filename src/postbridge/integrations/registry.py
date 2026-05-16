"""Реестр платформ: fetcher, publisher, креды, capabilities, AI-адаптер (фасад над registry_impl)."""

from __future__ import annotations

from typing import Any

from postbridge.domain.models import PostPayload
from postbridge.integrations.base import ChannelAdapter, SourceFetcher, TargetPublisher
from postbridge.integrations.channel_credentials import load_channel_credential_row
from postbridge.integrations.platform_ai import PlatformAiAdapter
from postbridge.integrations.platform_capabilities import PlatformCapabilities
from postbridge.integrations.registry_bundle import (
    CredentialDecoder,
    PlatformBundle,
    RuleAdaptPost,
)
from postbridge.integrations.registry_impl.assemble import (
    DEFAULT_AI as _DEFAULT_AI,
    FETCHERS,
    PLATFORM_BUNDLES,
    PUBLISHERS,
    RULE_POST_TEXT_LIMITS,
)

__all__ = [
    "CredentialDecoder",
    "FETCHERS",
    "PLATFORM_BUNDLES",
    "PUBLISHERS",
    "PlatformBundle",
    "RULE_POST_TEXT_LIMITS",
    "RegistryChannelAdapter",
    "RuleAdaptPost",
    "adapt_post_dict_for_platform",
    "decode_fetch_credentials_for_platform",
    "decode_publish_credentials_for_platform",
    "get_adapter",
    "get_ai_adapter",
    "get_fetcher",
    "get_platform_bundle",
    "get_platform_capabilities",
    "get_publisher",
    "platform_capabilities_public_map",
    "resolve_fetch_credentials_for_core_channel",
]


def get_platform_bundle(platform: str) -> PlatformBundle | None:
    return PLATFORM_BUNDLES.get(platform)


def get_platform_capabilities(platform: str) -> PlatformCapabilities | None:
    b = PLATFORM_BUNDLES.get(platform)
    return b.capabilities if b else None


def platform_capabilities_public_map() -> dict[str, dict[str, Any]]:
    """Снимок capabilities по всем платформам из реестра (для BFF / internal API)."""
    return {
        platform: {
            **bundle.capabilities.to_dict(),
            "rule_post_text_limit": RULE_POST_TEXT_LIMITS.get(platform),
        }
        for platform, bundle in PLATFORM_BUNDLES.items()
    }


def decode_publish_credentials_for_platform(platform: str, row: Any) -> Any:
    b = PLATFORM_BUNDLES.get(platform)
    if b is None or b.decode_publish_credentials is None:
        return None
    return b.decode_publish_credentials(row)


def decode_fetch_credentials_for_platform(platform: str, row: Any) -> Any:
    b = PLATFORM_BUNDLES.get(platform)
    if b is None:
        return None
    dec = b.decode_fetch_credentials or b.decode_publish_credentials
    if dec is None:
        return None
    return dec(row)


def get_ai_adapter(platform: str) -> PlatformAiAdapter:
    b = PLATFORM_BUNDLES.get(platform)
    if b is None:
        return _DEFAULT_AI
    return b.ai_adapter


def adapt_post_dict_for_platform(post: dict[str, Any], platform: str) -> str:
    """Детерминированная сборка текста под платформу (без LLM)."""
    if not isinstance(post, dict):
        return ""
    b = PLATFORM_BUNDLES.get(platform)
    if b is None or b.rule_adapt_post is None:
        t = post.get("text")
        if isinstance(t, str):
            return t
        return str(t) if t is not None else ""
    return b.rule_adapt_post(post)


class RegistryChannelAdapter:
    """Объединяет SourceFetcher и TargetPublisher для одной платформы."""

    def __init__(
        self,
        platform: str,
        fetcher: SourceFetcher | None,
        publisher: TargetPublisher | None,
        capabilities: PlatformCapabilities,
    ) -> None:
        self._platform = platform
        self._fetcher = fetcher
        self._publisher = publisher
        self._capabilities = capabilities

    @property
    def platform(self) -> str:
        return self._platform

    def get_capabilities(self) -> dict[str, Any]:
        d = self._capabilities.to_dict()
        d["has_fetcher"] = self._fetcher is not None
        d["has_publisher"] = self._publisher is not None
        return d

    async def fetch_posts(
        self,
        source_channel: str,
        limit: int,
        credentials: Any,
        *,
        tenant_id: str | None = None,
    ) -> list[PostPayload]:
        if self._fetcher is None:
            raise ValueError(f"fetch is not available for platform={self._platform}")
        return await self._fetcher.fetch_posts(
            source_channel, limit, credentials, tenant_id=tenant_id
        )

    def publish_post(
        self,
        target_channel: str,
        post: PostPayload,
        credentials: Any,
    ) -> str | None:
        if self._publisher is None:
            raise ValueError(f"publish is not available for platform={self._platform}")
        return self._publisher.publish_post(target_channel, post, credentials)


def get_adapter(platform: str) -> ChannelAdapter:
    """Возвращает фасад ChannelAdapter для platform (или ValueError)."""
    b = PLATFORM_BUNDLES.get(platform)
    fc = FETCHERS.get(platform)
    pc = PUBLISHERS.get(platform)
    if b is None and fc is None and pc is None:
        raise ValueError(
            f"Unknown platform adapter: {platform}. Available fetchers: {list(FETCHERS)} "
            f"publishers: {list(PUBLISHERS)}"
        )
    caps = b.capabilities if b else PlatformCapabilities(
        supports_source=fc is not None,
        supports_target=pc is not None,
        live_sync_publish_supported=False,
        live_sync_source_supported=False,
        historical_migration_source_supported=fc is not None,
        historical_migration_target_supported=pc is not None,
        ai_adapt_supported=False,
        fetch_credentials_required=False,
    )
    return RegistryChannelAdapter(
        platform,
        fc() if fc else None,
        pc() if pc else None,
        caps,
    )


def get_fetcher(platform: str) -> SourceFetcher:
    """Возвращает экземпляр fetcher для платформы."""
    cls = FETCHERS.get(platform)
    if cls is None:
        raise ValueError(f"Unknown source platform: {platform}. Available: {list(FETCHERS)}")
    return cls()


def get_publisher(platform: str) -> TargetPublisher:
    """Возвращает экземпляр publisher для платформы."""
    cls = PUBLISHERS.get(platform)
    if cls is None:
        raise ValueError(f"Unknown target platform: {platform}. Available: {list(PUBLISHERS)}")
    return cls()


def resolve_fetch_credentials_for_core_channel(
    session: Any,
    *,
    tenant_id: str,
    source_core_channel_id: str,
    source_platform: str,
) -> Any:
    """Креды fetcher из channel_credentials (live-sync fetch-posts, миграция)."""
    from postbridge.domain.errors import ValidationError

    row = load_channel_credential_row(session, source_core_channel_id, tenant_id)
    creds = decode_fetch_credentials_for_platform(source_platform, row)
    caps = get_platform_capabilities(source_platform)
    if caps and caps.fetch_credentials_required and creds is None:
        raise ValidationError(
            code="VALIDATION_CHANNEL_CREDENTIALS_MISSING",
            message="Active channel_credentials required for this source platform",
            details={"source_core_channel_id": source_core_channel_id, "source_platform": source_platform},
        )
    return creds
