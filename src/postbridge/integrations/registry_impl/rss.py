from __future__ import annotations

from postbridge.integrations.platform_ai import PlatformAiAdapter
from postbridge.integrations.platform_capabilities import PlatformCapabilities
from postbridge.integrations.registry_bundle import PlatformBundle
from postbridge.integrations.registry_impl.types import PlatformRegistration
from postbridge.integrations.rss.credentials import decode_fetch_credentials as rss_fetch_creds
from postbridge.integrations.rss.fetcher import RSSFetcher
from postbridge.integrations.rss.publisher import RSSPublisher

_CAP = PlatformCapabilities(
    supports_source=True,
    supports_target=True,
    live_sync_publish_supported=True,
    live_sync_source_supported=False,
    historical_migration_source_supported=True,
    historical_migration_target_supported=True,
    ai_adapt_supported=False,
    fetch_credentials_required=False,
)


def make_registration(default_ai: PlatformAiAdapter) -> PlatformRegistration:
    return PlatformRegistration(
        key="rss",
        fetcher_cls=RSSFetcher,
        publisher_cls=RSSPublisher,
        bundle=PlatformBundle(
            fetcher_cls=RSSFetcher,
            publisher_cls=RSSPublisher,
            decode_publish_credentials=None,
            decode_fetch_credentials=rss_fetch_creds,
            capabilities=_CAP,
            ai_adapter=default_ai,
            rule_adapt_post=None,
        ),
        rule_post_text_limit=None,
    )
