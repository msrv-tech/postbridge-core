from __future__ import annotations

from postbridge.integrations.linkedin.credentials import decode_channel_credentials
from postbridge.integrations.linkedin.publisher import LinkedInPublisher
from postbridge.integrations.linkedin.rule_post_text import (
    LINKEDIN_TEXT_LIMIT,
    adapt_post_dict as linkedin_rule_adapt_post,
)
from postbridge.integrations.platform_ai import PlatformAiAdapter
from postbridge.integrations.platform_capabilities import PlatformCapabilities
from postbridge.integrations.registry_bundle import PlatformBundle
from postbridge.integrations.registry_impl.types import PlatformRegistration

_CAP = PlatformCapabilities(
    supports_source=False,
    supports_target=True,
    live_sync_publish_supported=True,
    live_sync_source_supported=False,
    historical_migration_source_supported=False,
    historical_migration_target_supported=True,
    ai_adapt_supported=True,
    fetch_credentials_required=False,
)


def make_registration(default_ai: PlatformAiAdapter) -> PlatformRegistration:
    return PlatformRegistration(
        key="linkedin",
        fetcher_cls=None,
        publisher_cls=LinkedInPublisher,
        bundle=PlatformBundle(
            fetcher_cls=None,
            publisher_cls=LinkedInPublisher,
            decode_publish_credentials=decode_channel_credentials,
            decode_fetch_credentials=None,
            capabilities=_CAP,
            ai_adapter=default_ai,
            rule_adapt_post=linkedin_rule_adapt_post,
        ),
        rule_post_text_limit=LINKEDIN_TEXT_LIMIT,
    )
