from __future__ import annotations

from postbridge.integrations.platform_ai import PlatformAiAdapter
from postbridge.integrations.platform_capabilities import PlatformCapabilities
from postbridge.integrations.postbridge.credentials import decode_fetch_credentials as postbridge_fetch_creds
from postbridge.integrations.postbridge.fetcher import PostbridgeFetcher
from postbridge.integrations.registry_bundle import PlatformBundle
from postbridge.integrations.registry_impl.types import PlatformRegistration

_CAP = PlatformCapabilities(
    supports_source=True,
    supports_target=False,
    live_sync_publish_supported=False,
    live_sync_source_supported=True,
    historical_migration_source_supported=True,
    historical_migration_target_supported=False,
    ai_adapt_supported=False,
    fetch_credentials_required=False,
)


def make_registration(default_ai: PlatformAiAdapter) -> PlatformRegistration:
    return PlatformRegistration(
        key="postbridge",
        fetcher_cls=PostbridgeFetcher,
        publisher_cls=None,
        bundle=PlatformBundle(
            fetcher_cls=PostbridgeFetcher,
            publisher_cls=None,
            decode_publish_credentials=None,
            decode_fetch_credentials=postbridge_fetch_creds,
            capabilities=_CAP,
            ai_adapter=default_ai,
            rule_adapt_post=None,
        ),
        rule_post_text_limit=None,
    )
