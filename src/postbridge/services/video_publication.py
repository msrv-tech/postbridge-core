"""Exact-content video approval. Delivery uses the shared publication target executor."""
from datetime import UTC, datetime
import hashlib
import ipaddress
import json
import re
from urllib.parse import urlparse
from uuid import UUID, uuid4
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from postbridge.config import get_settings
from postbridge.domain.models import PostPayload
from postbridge.infrastructure.media_storage import managed_media_url
from postbridge.models.domain import (ChannelOrm, ContentItemOrm, MediaAssetOrm, PublicationPlanOrm,
    PublicationTargetOrm, VideoPostDraftOrm, VideoProductionJobOrm, VideoPublicationApprovalOrm, VideoRevisionOrm)
from postbridge.services.postbridge_workspace_content import _load_extra
from postbridge.services.video_media import video_error
from postbridge.services.video_delivery_time import DeliveryTiming, DELIVERY_GRACE, now_utc, timing_value, validate_future, delivery_view, assert_window
from postbridge.integrations.tiktok.api import TikTokPostOptions


class VideoDestinationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["video", "reel", "short", "tiktok"] = "video"
    title: str | None = Field(default=None, min_length=1, max_length=100)
    visibility: Literal["private", "unlisted", "public"] = "private"
    made_for_kids: bool | None = None
    contains_synthetic_media: bool = True


class ApproveVideoPublication(DeliveryTiming):
    model_config = ConfigDict(extra="forbid")
    expected_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    channel_ids: list[UUID] = Field(default_factory=list, max_length=10)
    bridge_ids: list[str] = Field(default_factory=list, max_length=10)
    expected_bridges: dict[str, str] = Field(default_factory=dict)
    options: dict[UUID, VideoDestinationOptions] = Field(default_factory=dict)
    tiktok_options: dict[UUID, TikTokPostOptions] = Field(default_factory=dict, max_length=10)

    @model_validator(mode="after")
    def check_selection(self):
        if bool(self.channel_ids) == bool(self.bridge_ids) or len(set(self.bridge_ids)) != len(self.bridge_ids):
            raise ValueError("Choose unique bridges or legacy direct destinations")
        if self.bridge_ids and (self.options or set(self.expected_bridges) != set(self.bridge_ids)):
            raise ValueError("Review every selected bridge; options come from the receiving side")
        if not self.bridge_ids and self.expected_bridges:
            raise ValueError("Bridge hashes require bridge selection")
        return self

    @field_validator("channel_ids")
    @classmethod
    def unique_channels(cls, value):
        if len(set(value)) != len(value):
            raise PydanticCustomError("video_duplicate_destinations", "Destinations must be unique")
        return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def approval_for_content(session, content_id):
    return session.scalar(select(VideoPublicationApprovalOrm).join(VideoPostDraftOrm,
        VideoPostDraftOrm.id == VideoPublicationApprovalOrm.post_draft_id).where(VideoPostDraftOrm.content_item_id == content_id))


def approval_for_plan(session, plan_id):
    return session.scalar(select(VideoPublicationApprovalOrm).where(VideoPublicationApprovalOrm.publication_plan_id == plan_id))


def is_video_post(session, content_id):
    return session.scalar(select(VideoPostDraftOrm.id).where(VideoPostDraftOrm.content_item_id == content_id)) is not None


def reject_unapproved_route(session, tenant_id, *, content_id=None, urls=None):
    """Also reject copied managed outputs, which may have lost source metadata."""
    if content_id and is_video_post(session, content_id):
        raise video_error("VIDEO_APPROVAL_REQUIRED", "Use the video approval card to publish this post")
    asset_ids = {match for url in (urls or []) if isinstance(url, str)
                 for match in re.findall(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", url)}
    if asset_ids and session.scalar(select(VideoProductionJobOrm.id).where(
            VideoProductionJobOrm.tenant_id == tenant_id, VideoProductionJobOrm.output_asset_id.in_(asset_ids)).limit(1)):
        raise video_error("VIDEO_APPROVAL_REQUIRED", "Managed video outputs require explicit video approval")


def _post(session, tenant_id, content_id, *, lock=False):
    stmt = select(ContentItemOrm).where(ContentItemOrm.id == content_id, ContentItemOrm.tenant_id == tenant_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    post = session.scalar(stmt)
    receipt = session.scalar(select(VideoPostDraftOrm).where(VideoPostDraftOrm.content_item_id == content_id,
        VideoPostDraftOrm.tenant_id == tenant_id)) if post else None
    if not receipt:
        raise video_error("VIDEO_POST_NOT_FOUND", "Video post not found")
    return post, receipt


def _snapshot(session, post, receipt):
    extra = _load_extra(post.body_structured_json)
    revision = session.get(VideoRevisionOrm, receipt.revision_id)
    job = session.scalar(select(VideoProductionJobOrm).where(VideoProductionJobOrm.revision_id == revision.id))
    asset = session.get(MediaAssetOrm, job.output_asset_id) if job and job.output_asset_id else None
    url = managed_media_url(asset.id, asset.object_key) if asset else None
    if not asset or asset.tenant_id != post.tenant_id or asset.content_type != "video/mp4" or job.status != "succeeded":
        raise video_error("VIDEO_NOT_READY", "The original MP4 is unavailable")
    if post.media_url != url or (post.media_urls and post.media_urls != [url]):
        raise video_error("VIDEO_OUTPUT_CHANGED", "Restore the original video or prepare a new video revision")
    # Use the visible editor text verbatim. No AI adaptation, truncation or implicit link-back.
    parts = [post.title, post.body_markdown, extra.get("summary"), extra.get("cta"), extra.get("link_url")]
    text = "\n\n".join(str(part).strip() for part in parts if part and str(part).strip())
    source = {"title": post.title, "body": post.body_markdown, "extra": {k: v for k, v in extra.items() if k != "published_at"},
              "media_url": post.media_url, "media_urls": post.media_urls, "language": post.language}
    return {"project_id": revision.project_id, "version": revision.version, "text": text, "output_url": url,
        "output_asset_id": asset.id, "byte_size": asset.byte_size, "object_key": asset.object_key,
        "source_hash": fingerprint(source)}


def _slug(channel):
    from postbridge.services.publication_target_executor import _resolve_target_channel_slug
    return _resolve_target_channel_slug(channel)


VIDEO_FORMATS = {"telegram": "video", "instagram": "reel", "youtube": "short", "tiktok": "tiktok"}


def _source_duration(session, receipt):
    revision = session.get(VideoRevisionOrm, receipt.revision_id)
    return sum(float(scene.get("duration_seconds", 0)) for scene in revision.scenes)


def _options(payload):
    values = {str(key): value.model_dump(mode="json", exclude_none=True) for key, value in payload.options.items()}
    if set(values) - {str(key) for key in payload.channel_ids}:
        raise video_error("VIDEO_DESTINATION_OPTIONS_INVALID", "Options must belong to selected destinations")
    return values


def publication_view(session: Session, tenant_id: str, content_id: str):
    post, receipt = _post(session, tenant_id, content_id)
    approval = approval_for_content(session, content_id)
    snapshot = approval.snapshot if approval else _snapshot(session, post, receipt)
    targets = session.scalars(select(PublicationTargetOrm).where(
        PublicationTargetOrm.publication_plan_id == approval.publication_plan_id)).all() if approval else []
    destinations = session.scalars(select(ChannelOrm).where(ChannelOrm.tenant_id == tenant_id,
        ChannelOrm.platform.in_(VIDEO_FORMATS), ChannelOrm.kind == "destination", ChannelOrm.status == "connected")).all()
    from postbridge.services.video_bridges import reviewed_bridges
    return {"bridges": reviewed_bridges(session, tenant_id, post), "enabled": get_settings().video_publication_enabled, "content_item_id": content_id,
        "source_duration_seconds": _source_duration(session, receipt),
        "snapshot": {k: v for k, v in snapshot.items() if k not in {"destinations", "object_key"}},
        "snapshot_hash": fingerprint({k: v for k, v in snapshot.items() if k != "destinations"}),
        "approval_id": approval.id if approval else None,
        "delivery": delivery_view(session, approval),
        "destinations": [{"id": ch.id, "title": ch.title, "platform": ch.platform, "format": VIDEO_FORMATS[ch.platform]} for ch in destinations],
        "targets": [{"id": target.id, "channel_id": target.channel_id,
            "title": snapshot.get("destinations", {}).get(target.channel_id, {}).get("title", target.platform),
            "bridge_id": snapshot.get("destinations", {}).get(target.channel_id, {}).get("bridge", {}).get("id"),
            "platform": target.platform, "status": target.status, "external_post_id": target.external_post_id, "external_url": target.external_url,
            "format": snapshot.get("destinations", {}).get(target.channel_id, {}).get("options", {}).get("format", "video"),
            "options": snapshot.get("destinations", {}).get(target.channel_id, {}).get("options", {}),
            "error_code": target.error_code} for target in targets]}


def approve_publication(session: Session, tenant_id: str, content_id: str, payload: ApproveVideoPublication):
    requested_tiktok = {str(key): value.model_dump(mode="json") for key, value in payload.tiktok_options.items()}
    # Refresh tokens before taking the post lock: rotation commits independently
    # of the immutable approval. Replays do not need a working provider API.
    from postbridge.services.tiktok import get_creator_info
    creators = {} if approval_for_content(session, content_id) else {
        key: get_creator_info(session, tenant_id, key) for key in requested_tiktok}
    post, receipt = _post(session, tenant_id, content_id, lock=True)
    previous = approval_for_content(session, content_id)
    ids = sorted(str(cid) for cid in payload.channel_ids)
    options = _options(payload)
    contexts = {}
    if previous:
        previous_hash = fingerprint({k: v for k, v in previous.snapshot.items() if k != "destinations"})
        initial = previous.initial_timing or {"publish_at": None, "timezone": None}
        saved_tiktok = {key: value["options"]["tiktok"] for key, value in previous.snapshot["destinations"].items()
            if value.get("options", {}).get("tiktok")}
        previous_options = {key: {k: v for k, v in value["options"].items() if k != "tiktok"}
            for key, value in previous.snapshot["destinations"].items() if value.get("options")}
        if payload.bridge_ids:
            saved = {value.get("bridge", {}).get("id"): value for value in previous.snapshot["destinations"].values()}
            if set(payload.bridge_ids) != set(saved) or any(payload.expected_bridges.get(key) != saved[key]["bridge"]["settings_hash"] for key in saved):
                raise video_error("VIDEO_APPROVAL_CONFLICT", "This post already has a different bridge approval")
            ids, options = sorted(previous.snapshot["destinations"]), previous_options
        if payload.expected_snapshot != previous_hash or ids != sorted(previous.snapshot["destinations"]) or initial != timing_value(payload) or options != previous_options or requested_tiktok != saved_tiktok:
            raise video_error("VIDEO_APPROVAL_CONFLICT", "This post already has an immutable publication approval")
        return publication_view(session, tenant_id, content_id)
    if not get_settings().video_publication_enabled:
        raise video_error("VIDEO_PUBLICATION_DISABLED", "Video publication is not enabled")
    if payload.bridge_ids:
        from postbridge.services.video_bridges import resolve_selection
        ids, options, contexts = resolve_selection(session, tenant_id, post, payload)
    now = now_utc()
    validate_future(payload, now)
    snapshot = _snapshot(session, post, receipt)
    if payload.expected_snapshot != fingerprint(snapshot) or post.status != "draft":
        raise video_error("VIDEO_APPROVAL_CONFLICT", "The post changed; review it again before approving")
    parsed = urlparse(snapshot["output_url"])
    host = parsed.hostname or ""
    try:
        private_host = not ipaddress.ip_address(host).is_global
    except ValueError:
        private_host = host in {"localhost", ""} or host.endswith((".local", ".localhost"))
    channels = session.scalars(select(ChannelOrm).where(ChannelOrm.tenant_id == tenant_id, ChannelOrm.id.in_(ids))).all()
    if len(channels) != len(ids) or any(ch.platform not in VIDEO_FORMATS or ch.kind != "destination" or ch.status != "connected" for ch in channels):
        raise video_error("VIDEO_DESTINATION_UNAVAILABLE", "Choose connected video destinations")
    if set(requested_tiktok) != {ch.id for ch in channels if ch.platform == "tiktok"}:
        raise video_error("VIDEO_DESTINATION_OPTIONS_INVALID", "Review every selected TikTok account and its per-video settings")
    from postbridge.integrations.channel_credentials import load_channel_credential_row
    from postbridge.integrations.registry import decode_publish_credentials_for_platform
    for ch in channels:
        settings = options.get(ch.id)
        if ch.platform != "telegram" and (settings or {}).get("format") != VIDEO_FORMATS[ch.platform]:
            raise video_error("VIDEO_DESTINATION_OPTIONS_INVALID", "Explicitly choose Reel or Short for this destination")
        if ch.platform == "telegram":
            if settings and settings["format"] != "video":
                raise video_error("VIDEO_DESTINATION_OPTIONS_INVALID", "Telegram requires a video post")
            if len(snapshot["text"].encode("utf-16-le")) // 2 > 1024:
                raise video_error("VIDEO_CAPTION_TOO_LONG", "Telegram video captions may contain at most 1024 characters")
            if snapshot["byte_size"] > 20 * 1024 * 1024:
                raise video_error("VIDEO_DELIVERY_FILE_TOO_LARGE", "URL-based Telegram delivery is limited to 20 MB")
        if ch.platform == "instagram" and len(snapshot["text"]) > 2200:
            raise video_error("VIDEO_CAPTION_TOO_LONG", "Instagram captions may contain at most 2200 characters")
        if ch.platform == "youtube" and (not settings.get("title", "").strip() or settings.get("made_for_kids") is None
                or len(snapshot["text"].encode("utf-8")) > 5000 or any(c in settings["title"] + snapshot["text"] for c in "<>")):
            raise video_error("VIDEO_DESTINATION_OPTIONS_INVALID", "Review the YouTube title, description, audience and visibility")
        if ch.platform == "tiktok":
            from postbridge.integrations.tiktok.api import validate_post
            validate_post(requested_tiktok[ch.id], creators[ch.id], _source_duration(session, receipt), snapshot["text"])
            options[ch.id] = {**settings, "tiktok": requested_tiktok[ch.id]}
        if ch.platform != "youtube" and (parsed.scheme != "https" or parsed.username or parsed.password or private_host):
            raise video_error("VIDEO_PUBLIC_MEDIA_REQUIRED", "A public HTTPS media URL is required for delivery")
        credentials = decode_publish_credentials_for_platform(ch.platform, load_channel_credential_row(session, ch.id, tenant_id))
        if not credentials or ch.platform == "telegram" and not credentials.bot_token:
            raise video_error("VIDEO_DESTINATION_UNAVAILABLE", "Connect bot credentials for every selected channel")
        if ch.platform == "youtube" and credentials.channel_id != _slug(ch).removeprefix("youtube/"):
            raise video_error("VIDEO_DESTINATION_UNAVAILABLE", "YouTube credentials belong to another channel")
        if ch.platform == "tiktok" and credentials.open_id != _slug(ch).removeprefix("tiktok/"):
            raise video_error("VIDEO_DESTINATION_UNAVAILABLE", "TikTok credentials belong to another account")
        if ch.platform == "instagram" and credentials.instagram_user_id != _slug(ch).removeprefix("instagram/"):
            raise video_error("VIDEO_DESTINATION_UNAVAILABLE", "Instagram credentials belong to another account")
    snapshot["destinations"] = {ch.id: {"slug": _slug(ch), "platform": ch.platform, "title": ch.title,
        **({"options": options[ch.id]} if ch.id in options else {}),
        **({"bridge": contexts[ch.id]} if ch.id in contexts else {})} for ch in channels}
    from postbridge.services.publication_planning import create_plan_and_targets_for_content_item
    chain = create_plan_and_targets_for_content_item(session, tenant_id=tenant_id, content_item=post,
        channel_ids=ids, video_approval=True, scheduled_at=payload.publish_at)
    session.get(PublicationPlanOrm, chain.publication_plan_id).timezone = payload.timezone
    timing = timing_value(payload)
    approval = VideoPublicationApprovalOrm(id=str(uuid4()), tenant_id=tenant_id, post_draft_id=receipt.id,
        publication_plan_id=chain.publication_plan_id, snapshot=snapshot, initial_timing=timing,
        schedule_version=1, schedule_history=[{**timing, "version": 1, "created_at": now.isoformat()}],
        deadline_at=(payload.publish_at or now) + DELIVERY_GRACE)
    session.add(approval)
    session.commit()
    return publication_view(session, tenant_id, content_id)


def approved_payload(session, target, content, render, *, check_time=True):
    """Called by the shared executor immediately before any publisher is selected."""
    approval = approval_for_plan(session, target.publication_plan_id)
    if not approval:
        if content:
            reject_unapproved_route(session, target.tenant_id, content_id=content.id,
                urls=[content.media_url, *(content.media_urls or [])])
        return None
    if not get_settings().video_publication_enabled:
        raise video_error("VIDEO_PUBLICATION_DISABLED", "Video publication is paused")
    if check_time:
        assert_window(approval, now_utc())
    receipt = session.get(VideoPostDraftOrm, approval.post_draft_id)
    if not content or receipt.content_item_id != content.id or approval.tenant_id != target.tenant_id:
        raise video_error("VIDEO_APPROVAL_CONFLICT", "The approved source is unavailable")
    current = _snapshot(session, content, receipt)
    snapshot = approval.snapshot
    if current != {k: v for k, v in snapshot.items() if k != "destinations"}:
        raise video_error("VIDEO_APPROVAL_CONFLICT", "The approved post changed")
    channel = session.get(ChannelOrm, target.channel_id)
    destination = snapshot["destinations"].get(target.channel_id)
    if not destination or not channel or channel.tenant_id != target.tenant_id or channel.status != "connected" or channel.platform != destination["platform"] or _slug(channel) != destination["slug"] or target.platform != destination["platform"]:
        raise video_error("VIDEO_DESTINATION_UNAVAILABLE", "The approved destination changed or was disconnected")
    from postbridge.services.video_bridges import assert_approved_bridge
    assert_approved_bridge(session, target.tenant_id, content, destination)
    # Ignore render variants: the immutable approval is the only source for sending.
    settings = destination.get("options", {})
    return PostPayload(source_post_id=content.id, text=snapshot["text"], media_url=snapshot["output_url"],
        media_urls=[snapshot["output_url"]], media_type="video", publication_format=settings.get("format", "video"),
        video_title=settings.get("title"), visibility=settings.get("visibility"), made_for_kids=settings.get("made_for_kids"),
        contains_synthetic_media=settings.get("contains_synthetic_media"),
        tiktok_options=settings.get("tiktok"), video_duration_seconds=_source_duration(session, receipt))


def retry_failed_target(session, tenant_id, content_id, target_id):
    post, _ = _post(session, tenant_id, content_id, lock=True)
    approval = approval_for_content(session, post.id)
    target = session.get(PublicationTargetOrm, target_id)
    if not approval or not target or target.publication_plan_id != approval.publication_plan_id:
        raise video_error("VIDEO_DESTINATION_UNAVAILABLE", "Publication target not found")
    if target.status != "failed" or target.error_code not in {"EXTERNAL_API_TELEGRAM_PUBLISH_ERROR", "EXTERNAL_API_INSTAGRAM_REJECTED", "EXTERNAL_API_YOUTUBE_REJECTED", "EXTERNAL_API_TIKTOK_REJECTED"}:
        raise video_error("VIDEO_RETRY_UNSAFE", "Only a confirmed rejected delivery may be retried")
    approved_payload(session, target, post, None)
    target.status, target.error_code, target.error_message = "pending", None, None
    target.retry_count += 1
    target.updated_at = datetime.now(UTC)
    from postbridge.storage.publication_status_event_outbox import enqueue_publication_target_status_changed
    enqueue_publication_target_status_changed(session, target, correlation_id="video-delivery-update")
    session.commit()
    return publication_view(session, tenant_id, content_id)


def cancel_pending(session, tenant_id, content_id):
    post, _ = _post(session, tenant_id, content_id, lock=True)
    approval = approval_for_content(session, post.id)
    if not approval:
        raise video_error("VIDEO_APPROVAL_REQUIRED", "No publication has been approved")
    ids = session.scalars(update(PublicationTargetOrm).where(
        PublicationTargetOrm.publication_plan_id == approval.publication_plan_id,
        PublicationTargetOrm.status == "pending").values(status="cancelled", updated_at=datetime.now(UTC))
        .returning(PublicationTargetOrm.id)).all()
    from postbridge.storage.publication_status_event_outbox import enqueue_publication_target_status_changed
    for target_id in ids:
        enqueue_publication_target_status_changed(session, session.get(PublicationTargetOrm, target_id), correlation_id="video-cancel")
    session.commit()
    return publication_view(session, tenant_id, content_id)


def reconcile_target(session, tenant_id, content_id, target_id):
    post, _ = _post(session, tenant_id, content_id, lock=True)
    approval = approval_for_content(session, content_id)
    target = session.get(PublicationTargetOrm, target_id)
    from postbridge.models.domain import VideoDeliveryStateOrm
    from postbridge.services.video_delivery_state import VideoDeliveryContext
    if not approval or not target or target.publication_plan_id != approval.publication_plan_id or target.platform not in {"youtube", "instagram", "tiktok"}:
        raise video_error("VIDEO_DESTINATION_UNAVAILABLE", "Choose an approved video destination")
    if target.status not in {"unknown", "failed"}:
        return publication_view(session, tenant_id, content_id)
    if not session.get(VideoDeliveryStateOrm, target_id):
        raise video_error("VIDEO_RETRY_UNSAFE", "There is no saved provider session to reconcile")
    context = VideoDeliveryContext(session, target_id, approval.snapshot)
    if context.state.get("phase") == "rejected":
        raise video_error("VIDEO_RETRY_UNSAFE", "Use retry for this explicitly rejected delivery")
    approved_payload(session, target, post, None, check_time=False)
    target.status, target.error_code, target.error_message = "processing", None, None
    target.updated_at = datetime.now(UTC)
    from postbridge.storage.publication_status_event_outbox import enqueue_publication_target_status_changed
    enqueue_publication_target_status_changed(session, target, correlation_id="video-delivery-update")
    session.commit()
    return publication_view(session, tenant_id, content_id)
