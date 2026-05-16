from __future__ import annotations

from postbridge.integrations.platform_ai import PlatformAiAdapter
from postbridge.integrations.platform_capabilities import PlatformCapabilities
from postbridge.integrations.registry_bundle import PlatformBundle
from postbridge.integrations.registry_impl.types import PlatformRegistration
from postbridge.integrations.zen.credentials import decode_fetch_credentials as zen_fetch_creds
from postbridge.integrations.zen.fetcher import ZenFetcher
from postbridge.integrations.zen.publisher import ZenPublisher
from postbridge.integrations.zen.rule_post_text import adapt_post_dict as zen_rule_adapt_post

_CAP = PlatformCapabilities(
    supports_source=True,
    supports_target=True,
    live_sync_publish_supported=True,
    live_sync_source_supported=False,
    historical_migration_source_supported=True,
    historical_migration_target_supported=False,
    ai_adapt_supported=True,
    fetch_credentials_required=False,
)


def make_registration(default_ai: PlatformAiAdapter) -> PlatformRegistration:
    return PlatformRegistration(
        key="zen",
        fetcher_cls=ZenFetcher,
        publisher_cls=ZenPublisher,
        bundle=PlatformBundle(
            fetcher_cls=ZenFetcher,
            publisher_cls=ZenPublisher,
            decode_publish_credentials=None,
            decode_fetch_credentials=zen_fetch_creds,
            capabilities=_CAP,
            ai_adapter=default_ai,
            rule_adapt_post=zen_rule_adapt_post,
        ),
        rule_post_text_limit=1200,
    )
