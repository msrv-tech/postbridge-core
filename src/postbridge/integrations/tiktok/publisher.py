"""Durable TikTok Direct Post uploads. Lost init responses are never replayed."""
from urllib.parse import urlparse
from urllib.parse import quote
import re

import httpx

from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.config import get_settings
from postbridge.integrations.tiktok.api import API, creator_info, result, validate_post
from postbridge.integrations.video_delivery import DeliveryPending, VideoDeliveryReceipt, unknown



class TikTokPublisher:
    def publish_post(self, target_channel, payload, credentials=None):
        raise ConfigurationError("TikTok requires the reviewed video delivery workflow")

    def publish_video(self, target_channel, payload, credentials, delivery):
        if (not credentials or credentials.open_id != target_channel.removeprefix("tiktok/")
                or payload.publication_format != "tiktok" or not payload.tiktok_options):
            raise ConfigurationError("Review the TikTok account and per-video publishing choices")
        from postbridge.services.tiktok import authorized_client
        try:
            with authorized_client(delivery.session, delivery.channel_id, delivery.tenant_id) as (client, token, _):
                headers = {"Authorization": f"Bearer {token}"}
                state = delivery.state
                if not state or state.get("phase") == "rejected":
                    creator = creator_info(client, token)
                    settings = validate_post(payload.tiktok_options, creator, payload.video_duration_seconds, payload.text)
                    if not get_settings().tiktok_direct_post_audited and settings.privacy_level != "SELF_ONLY":
                        raise ConfigurationError("TikTok audit is pending; only private test posts are allowed")
                    source = urlparse(payload.media_url or "")
                    prefix = urlparse(get_settings().tiktok_media_url_prefix or "")
                    if (source.scheme != "https" or prefix.scheme != "https" or source.username or source.password or not prefix.hostname
                            or source.netloc != prefix.netloc or not prefix.path.endswith("/")
                            or not source.path.startswith(prefix.path) or source.query or source.fragment):
                        raise ConfigurationError("Configure and verify the TikTok media URL prefix before publishing")
                    with delivery.media_file() as path:
                        from postbridge.services.video_media import inspect_media
                        validate_post(payload.tiktok_options, creator, inspect_media(path)["duration_seconds"], payload.text)
                    delivery.save(phase="starting")
                    response = client.post(f"{API}/post/publish/video/init/", headers=headers, json={
                        "post_info": {"title": payload.text, "privacy_level": settings.privacy_level,
                            "disable_comment": not settings.allow_comment, "disable_duet": not settings.allow_duet,
                            "disable_stitch": not settings.allow_stitch, "brand_content_toggle": settings.branded_content,
                            "brand_organic_toggle": settings.brand_organic, "is_aigc": bool(payload.contains_synthetic_media)},
                        "source_info": {"source": "PULL_FROM_URL", "video_url": payload.media_url}})
                    try:
                        created = result(response)
                    except ExternalApiError as exc:
                        if exc.code == "EXTERNAL_API_TIKTOK_REJECTED":
                            delivery.save(phase="rejected")
                        raise
                    publish_id = created.get("publish_id")
                    if not isinstance(publish_id, str) or not 1 <= len(publish_id) <= 128:
                        raise unknown("tiktok")
                    delivery.save(phase="processing", publish_id=publish_id, creator_username=creator["creator_username"])
                if delivery.state.get("phase") == "starting":
                    raise unknown("tiktok", "The init response was lost; a duplicate TikTok post was not created")
                state = delivery.state
                if not state.get("publish_id"):
                    raise unknown("tiktok")
                # TikTok pulls the managed MP4. Poll this publish ID; never reinitialize
                # a delivery whose init response or later processing outcome is uncertain.
                data = result(client.post(f"{API}/post/publish/status/fetch/", headers=headers,
                    json={"publish_id": delivery.state["publish_id"]}))
                if data.get("status") == "FAILED":
                    delivery.save(phase="failed")
                    raise ExternalApiError(code="EXTERNAL_API_TIKTOK_PROCESSING_FAILED", message="TikTok could not publish this video",
                        source="tiktok", retryable=False, details={})
                if data.get("status") != "PUBLISH_COMPLETE":
                    raise DeliveryPending()
                ids = data.get("publicaly_available_post_id") or []
                public_id = str(ids[0]) if ids else None
                if public_id and not re.fullmatch(r"\d{1,30}", public_id):
                    raise unknown("tiktok")
                username = quote(delivery.state.get("creator_username", ""), safe="._-")
                url = f"https://www.tiktok.com/@{username}/video/{public_id}" if public_id and username else None
                external_id = public_id or delivery.state["publish_id"]
                delivery.save(phase="published", external_id=external_id, external_url=url)
                return VideoDeliveryReceipt(external_id, url)
        except httpx.RequestError:
            raise unknown("tiktok") from None
