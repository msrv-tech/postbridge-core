"""Resolve reviewed video destinations from tenant-owned Postbridge bridges."""
from sqlalchemy import select
from postbridge.domain.errors import ValidationError
from postbridge.models.domain import BridgeOrm, ChannelOrm
from postbridge.services.video_media import video_error

FORMATS = {"telegram": "video", "instagram": "reel", "youtube": "short", "tiktok": "tiktok"}


def normalize_bridge_settings(settings, source, target, mode="live_sync"):
    from postbridge.services.video_publication import VideoDestinationOptions
    values = dict(settings or {})
    raw = values.get("video_delivery")
    if raw is None and target.platform not in {"youtube", "tiktok"}:
        return settings
    if source.platform != "postbridge" or target.platform not in FORMATS or mode != "live_sync":
        raise video_error("VIDEO_BRIDGE_UNSUPPORTED", "Video bridges require a Postbridge source and a supported video destination")
    try:
        options = VideoDestinationOptions.model_validate(raw or ({"format": "tiktok"} if target.platform == "tiktok" else {}))
    except ValueError:
        raise video_error("VIDEO_BRIDGE_SETTINGS_INVALID", "Review the receiving side video settings") from None
    if options.format != FORMATS[target.platform] or target.platform == "youtube" and options.made_for_kids is None:
        raise video_error("VIDEO_BRIDGE_SETTINGS_INVALID", "Choose the video format and YouTube audience on the receiving side")
    values["video_delivery"] = ({"format": "tiktok", "contains_synthetic_media": options.contains_synthetic_media}
        if target.platform == "tiktok" else options.model_dump(mode="json", exclude_none=True))
    return values


def context_for(session, tenant_id, bridge, post):
    from postbridge.services.video_publication import fingerprint, _load_extra
    source = session.get(ChannelOrm, bridge.source_channel_id, populate_existing=True)
    target = session.get(ChannelOrm, bridge.target_channel_id, populate_existing=True)
    if (bridge.tenant_id != tenant_id or bridge.status != "active" or bridge.mode != "live_sync"
            or not source or source.tenant_id != tenant_id or source.platform != "postbridge" or source.kind != "source" or source.status != "connected"
            or not target or target.tenant_id != tenant_id or target.platform not in FORMATS or target.kind != "destination" or target.status != "connected"):
        raise video_error("VIDEO_BRIDGE_UNAVAILABLE", "This video bridge is paused, disconnected or unavailable")
    workspace_id = _load_extra(post.body_structured_json).get("saas_workspace_id")
    if workspace_id and source.external_id != f"pb/{workspace_id}":
        raise video_error("VIDEO_BRIDGE_UNAVAILABLE", "The bridge belongs to another workspace source")
    settings = normalize_bridge_settings(bridge.settings_json, source, target, bridge.mode) or {}
    options = dict(settings.get("video_delivery") or {"format": FORMATS[target.platform]})
    if target.platform == "youtube":
        options["title"] = options.get("title") or (post.title or post.body_markdown or "Video").splitlines()[0].strip()[:100] or "Video"
    context = {"id": bridge.id, "source_channel_id": source.id, "target_channel_id": target.id,
               "settings_hash": fingerprint(bridge.settings_json or {})}
    return target, options, context


def reviewed_bridges(session, tenant_id, post):
    result = []
    for bridge in session.scalars(select(BridgeOrm).where(BridgeOrm.tenant_id == tenant_id,
            BridgeOrm.mode == "live_sync", BridgeOrm.status == "active").order_by(BridgeOrm.created_at, BridgeOrm.id)):
        try:
            channel, options, context = context_for(session, tenant_id, bridge, post)
        except ValidationError:
            continue
        result.append({"id": bridge.id, "channel_id": channel.id, "source_channel_id": bridge.source_channel_id,
                       "title": channel.title, "platform": channel.platform, "format": options["format"],
                       "options": options, "settings_hash": context["settings_hash"]})
    return result


def resolve_selection(session, tenant_id, post, payload):
    channels, options, contexts = [], {}, {}
    for bridge_id in payload.bridge_ids:
        bridge = session.scalar(select(BridgeOrm).where(BridgeOrm.id == bridge_id, BridgeOrm.tenant_id == tenant_id).with_for_update().execution_options(populate_existing=True))
        if bridge is None:
            raise video_error("VIDEO_BRIDGE_UNAVAILABLE", "Video bridge not found")
        channel, values, context = context_for(session, tenant_id, bridge, post)
        if payload.expected_bridges.get(bridge_id) != context["settings_hash"]:
            raise video_error("VIDEO_BRIDGE_CHANGED", "The bridge settings changed; review the video delivery again")
        if channel.id in contexts:
            raise video_error("VIDEO_BRIDGE_SETTINGS_INVALID", "Choose only one bridge per destination")
        channels.append(channel.id); options[channel.id] = values; contexts[channel.id] = context
    return sorted(channels), options, contexts


def assert_approved_bridge(session, tenant_id, post, destination):
    expected = destination.get("bridge")
    if not expected:
        return  # Existing approvals retain their original contract.
    bridge = session.get(BridgeOrm, expected["id"], populate_existing=True)
    if bridge is None:
        raise video_error("VIDEO_BRIDGE_UNAVAILABLE", "The approved bridge was deleted")
    _, options, context = context_for(session, tenant_id, bridge, post)
    approved_options = {key: value for key, value in destination["options"].items() if key != "tiktok"}
    if context != expected or options != approved_options:
        raise video_error("VIDEO_BRIDGE_CHANGED", "The approved bridge settings changed; delivery was stopped")
