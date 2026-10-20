from postbridge.integrations.tiktok.credentials import decode_channel_credentials
from postbridge.integrations.tiktok.publisher import TikTokPublisher
from postbridge.integrations.platform_capabilities import PlatformCapabilities
from postbridge.integrations.registry_bundle import PlatformBundle
from postbridge.integrations.registry_impl.types import PlatformRegistration


def make_registration(default_ai):
    cap = PlatformCapabilities(supports_source=False, supports_target=True, live_sync_publish_supported=False,
        live_sync_source_supported=False, historical_migration_source_supported=False,
        historical_migration_target_supported=False, ai_adapt_supported=False, fetch_credentials_required=False,
        video_formats=("tiktok",))
    return PlatformRegistration(key="tiktok", fetcher_cls=None, publisher_cls=TikTokPublisher,
        bundle=PlatformBundle(fetcher_cls=None, publisher_cls=TikTokPublisher,
            decode_publish_credentials=decode_channel_credentials, decode_fetch_credentials=None,
            capabilities=cap, ai_adapter=default_ai, rule_adapt_post=lambda post: post.get("text", "")),
        rule_post_text_limit=2200)
