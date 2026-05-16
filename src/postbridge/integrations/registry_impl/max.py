from __future__ import annotations

from postbridge.integrations.max.credentials import decode_channel_credentials
from postbridge.integrations.max.fetcher import MaxFetcher
from postbridge.integrations.max.publisher import MaxPublisher
from postbridge.integrations.max.rule_post_text import (
    MAX_TEXT_LIMIT,
    adapt_post_dict as max_rule_adapt_post,
)
from postbridge.integrations.platform_ai import PlatformAiAdapter
from postbridge.integrations.platform_capabilities import PlatformCapabilities
from postbridge.integrations.registry_bundle import PlatformBundle
from postbridge.integrations.registry_impl.types import PlatformRegistration

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
        key="max",
        fetcher_cls=MaxFetcher,
        publisher_cls=MaxPublisher,
        bundle=PlatformBundle(
            fetcher_cls=MaxFetcher,
            publisher_cls=MaxPublisher,
            decode_publish_credentials=decode_channel_credentials,
            decode_fetch_credentials=decode_channel_credentials,
            capabilities=_CAP,
            ai_adapter=default_ai,
            rule_adapt_post=max_rule_adapt_post,
        ),
        rule_post_text_limit=MAX_TEXT_LIMIT,
    )
