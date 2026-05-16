from __future__ import annotations

from postbridge.integrations.platform_ai import PlatformAiAdapter
from postbridge.integrations.platform_capabilities import PlatformCapabilities
from postbridge.integrations.registry_bundle import PlatformBundle
from postbridge.integrations.registry_impl.types import PlatformRegistration
from postbridge.integrations.telegram.credentials import decode_channel_credentials
from postbridge.integrations.telegram.fetcher import TelegramFetcher
from postbridge.integrations.telegram.publisher import TelegramPublisher
from postbridge.integrations.telegram.rule_post_text import adapt_post_dict as telegram_rule_adapt_post

_CAP = PlatformCapabilities(
    supports_source=True,
    supports_target=True,
    live_sync_publish_supported=True,
    live_sync_source_supported=True,
    historical_migration_source_supported=True,
    historical_migration_target_supported=True,
    ai_adapt_supported=True,
    fetch_credentials_required=True,
)


def make_registration(default_ai: PlatformAiAdapter) -> PlatformRegistration:
    return PlatformRegistration(
        key="telegram",
        fetcher_cls=TelegramFetcher,
        publisher_cls=TelegramPublisher,
        bundle=PlatformBundle(
            fetcher_cls=TelegramFetcher,
            publisher_cls=TelegramPublisher,
            decode_publish_credentials=decode_channel_credentials,
            decode_fetch_credentials=decode_channel_credentials,
            capabilities=_CAP,
            ai_adapter=default_ai,
            rule_adapt_post=telegram_rule_adapt_post,
        ),
        rule_post_text_limit=4096,
    )
