from __future__ import annotations

from postbridge.integrations.platform_ai import PlatformAiAdapter
from postbridge.integrations.platform_capabilities import PlatformCapabilities
from postbridge.integrations.registry_bundle import PlatformBundle
from postbridge.integrations.registry_impl.types import PlatformRegistration
from postbridge.integrations.vk.credentials import decode_channel_credentials
from postbridge.integrations.vk.fetcher import VKFetcher
from postbridge.integrations.vk.publisher import VKPublisher
from postbridge.integrations.vk.rule_post_text import adapt_post_dict as vk_rule_adapt_post

_CAP = PlatformCapabilities(
    supports_source=True,
    supports_target=True,
    live_sync_publish_supported=True,
    live_sync_source_supported=False,
    historical_migration_source_supported=True,
    historical_migration_target_supported=True,
    ai_adapt_supported=True,
    fetch_credentials_required=False,
)


def make_registration(default_ai: PlatformAiAdapter) -> PlatformRegistration:
    return PlatformRegistration(
        key="vk",
        fetcher_cls=VKFetcher,
        publisher_cls=VKPublisher,
        bundle=PlatformBundle(
            fetcher_cls=VKFetcher,
            publisher_cls=VKPublisher,
            decode_publish_credentials=decode_channel_credentials,
            decode_fetch_credentials=decode_channel_credentials,
            capabilities=_CAP,
            ai_adapter=default_ai,
            rule_adapt_post=vk_rule_adapt_post,
        ),
        rule_post_text_limit=800,
    )
