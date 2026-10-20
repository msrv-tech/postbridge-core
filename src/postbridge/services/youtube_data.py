"""Refresh authorized channel data and erase revoked or expired YouTube API data."""
from datetime import UTC, datetime, timedelta
import json

import httpx
from sqlalchemy import and_, delete, func, or_, select

from postbridge.models.domain import (
    ChannelCredentialOrm, PublicationTargetOrm, VideoDeliveryStateOrm, VideoPublicationApprovalOrm,
)


def purge_channel_data(session, channel):
    """Keep user-authored source posts; remove channel data and upload receipts."""
    for approval in session.scalars(select(VideoPublicationApprovalOrm).where(
            VideoPublicationApprovalOrm.tenant_id == channel.tenant_id)):
        destinations = approval.snapshot.get("destinations", {})
        if channel.id in destinations:
            approval.snapshot = {**approval.snapshot,
                "destinations": {key: value for key, value in destinations.items() if key != channel.id}}
    targets = select(PublicationTargetOrm.id).where(
        PublicationTargetOrm.tenant_id == channel.tenant_id, PublicationTargetOrm.channel_id == channel.id)
    session.execute(delete(VideoDeliveryStateOrm).where(VideoDeliveryStateOrm.target_id.in_(targets)))
    session.execute(delete(ChannelCredentialOrm).where(ChannelCredentialOrm.channel_id == channel.id,
        ChannelCredentialOrm.tenant_id == channel.tenant_id))
    session.execute(delete(PublicationTargetOrm).where(
        PublicationTargetOrm.tenant_id == channel.tenant_id, PublicationTargetOrm.channel_id == channel.id))
    # Credentials, upload sessions, bridges and outbox rows cascade from these records.
    session.delete(channel)
    session.flush()


def _trim_receipts(session, channel, now):
    cutoff = now - timedelta(days=29)
    for target in session.scalars(select(PublicationTargetOrm).outerjoin(VideoDeliveryStateOrm,
            VideoDeliveryStateOrm.target_id == PublicationTargetOrm.id).where(
            PublicationTargetOrm.channel_id == channel.id,
            or_(VideoDeliveryStateOrm.updated_at < cutoff,
                and_(PublicationTargetOrm.external_post_id.isnot(None),
                    func.coalesce(PublicationTargetOrm.published_at, PublicationTargetOrm.updated_at) < cutoff)))):
        target.external_post_id = target.external_url = target.error_message = None
        session.execute(delete(VideoDeliveryStateOrm).where(VideoDeliveryStateOrm.target_id == target.id))
        if target.status not in {"published", "cancelled", "failed"}:
            target.status, target.error_code = "failed", "YOUTUBE_DATA_EXPIRED"


def maintain_connection(session, channel, *, now=None):
    """Return only safe registry metadata; never return credentials to the caller."""
    now = now or datetime.now(UTC)
    _trim_receipts(session, channel, now)
    try:
        config = json.loads(channel.config_json or "{}")
        checked = datetime.fromisoformat(config["youtube_data_checked_at"])
    except (KeyError, ValueError, TypeError):
        config, checked = {}, channel.updated_at or channel.created_at
    checked = checked.replace(tzinfo=UTC) if checked.tzinfo is None else checked
    credential = session.scalar(select(ChannelCredentialOrm).where(
        ChannelCredentialOrm.channel_id == channel.id, ChannelCredentialOrm.tenant_id == channel.tenant_id))
    def disconnected():
        purge_channel_data(session, channel)
        return {"status": "disconnected"}
    if not credential:
        return disconnected()
    from postbridge.infrastructure.crypto.credentials import decode_channel_credential_raw
    try:
        secret = json.loads(decode_channel_credential_raw(credential))
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            token_response = client.post("https://oauth2.googleapis.com/token", data={
                "grant_type": "refresh_token", "refresh_token": secret["refresh_token"],
                "client_id": secret["client_id"], "client_secret": secret["client_secret"]})
            if token_response.status_code == 400 and token_response.json().get("error") == "invalid_grant":
                return disconnected()
            token_response.raise_for_status()
            token = token_response.json()["access_token"]
            response = client.get("https://www.googleapis.com/youtube/v3/channels",
                headers={"Authorization": f"Bearer {token}"}, params={"mine": "true", "part": "id,snippet"})
            response.raise_for_status()
            items = response.json()["items"]
            item = next((item for item in items if item.get("id") == channel.external_id), None)
            if item is None:
                return disconnected()
            title = item["snippet"]["title"]
            if not isinstance(title, str) or not title.strip():
                raise ValueError("invalid channel metadata")
    except (httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError):
        if checked <= now - timedelta(days=29):
            return disconnected()
        return {"status": "deferred"}
    channel.title = title[:255]
    channel.config_json = json.dumps({**config, "youtube_data_checked_at": now.isoformat()})
    # Cached names in past approvals are display metadata, not user-authored video titles.
    for approval in session.scalars(select(VideoPublicationApprovalOrm).where(
            VideoPublicationApprovalOrm.tenant_id == channel.tenant_id)):
        destinations = approval.snapshot.get("destinations", {})
        if channel.id in destinations:
            approval.snapshot = {**approval.snapshot, "destinations": {**destinations,
                channel.id: {**destinations[channel.id], "title": channel.title}}}
    return {"status": "active", "title": channel.title}
