"""Read available provider counters for approved video receipts, without new OAuth scopes."""
from datetime import UTC, datetime
import re
from time import monotonic
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from postbridge.models.domain import ChannelOrm, PublicationPlanOrm, PublicationTargetOrm
from postbridge.services.video_publication import _post


class VideoMetricsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_ids: list[UUID] = Field(min_length=1, max_length=30)


def _counter(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r"[0-9]{1,20}", str(value)):
        return None
    number = int(value)
    return number if number < 2**64 else None


def read_video_metrics(session, tenant_id, payload):
    content_ids = list({str(value) for value in payload.content_ids})
    for content_id in content_ids:
        _post(session, tenant_id, content_id)
    rows = session.execute(select(PublicationTargetOrm, PublicationPlanOrm.content_item_id).join(PublicationPlanOrm,
        PublicationTargetOrm.publication_plan_id == PublicationPlanOrm.id).where(PublicationTargetOrm.tenant_id == tenant_id,
        PublicationPlanOrm.content_item_id.in_(content_ids))).all()
    result, groups = [], {}
    now = datetime.now(UTC).isoformat()
    for target, content_id in rows:
        entry = {"target_id": target.id, "content_item_id": content_id, "channel_id": target.channel_id,
            "platform": target.platform, "collected_at": now, "status": "unavailable", "metrics": {},
            "reason": "VIDEO_METRICS_NOT_CONFIGURED"}
        result.append(entry)
        if target.platform == "youtube" and target.status == "published" and target.external_post_id:
            groups.setdefault(target.channel_id, []).append((target, entry))
        elif target.status != "published":
            entry["reason"] = "VIDEO_NOT_PUBLISHED"
    from postbridge.integrations.channel_credentials import load_channel_credential_row
    from postbridge.integrations.registry import decode_publish_credentials_for_platform
    from postbridge.integrations.youtube.publisher import YouTubePublisher, API
    deadline = monotonic() + 70
    for channel_id, targets in groups.items():
        try:
            channel = session.get(ChannelOrm, channel_id)
            credentials = decode_publish_credentials_for_platform("youtube", load_channel_credential_row(session, channel_id, tenant_id))
            if not channel or channel.tenant_id != tenant_id or channel.status != "connected" or not credentials or monotonic() >= deadline:
                continue
            with httpx.Client(timeout=max(1, min(15, (deadline - monotonic()) / 2)), follow_redirects=False) as client:
                token = YouTubePublisher()._token(client, credentials)
                for offset in range(0, len(targets), 50):
                    chunk = targets[offset:offset + 50]
                    response = client.get(f"{API}/videos", headers={"Authorization": f"Bearer {token}"}, params={
                        "id": ",".join(t.external_post_id for t, _ in chunk), "part": "snippet,statistics",
                        "fields": "items(id,snippet/channelId,statistics(viewCount,likeCount,commentCount))"},
                        timeout=max(1, min(15, deadline - monotonic())))
                    response.raise_for_status()
                    values = {item["id"]: item for item in response.json().get("items", [])}
                    for target, entry in chunk:
                        item = values.get(target.external_post_id)
                        if not item or item.get("snippet", {}).get("channelId") != credentials.channel_id:
                            entry["reason"] = "VIDEO_METRICS_NOT_ACCESSIBLE"
                            continue
                        statistics = item.get("statistics", {})
                        entry.update(status="available", reason=None, metrics={
                            key: {"value": _counter(statistics.get(native)), "definition": f"youtube.video.statistics.{native}",
                                "definition_url": "https://developers.google.com/youtube/data/reference/rest/v3/videos#statistics"}
                            for key, native in [("views", "viewCount"), ("likes", "likeCount"), ("comments", "commentCount")]})
        except Exception:
            for _, entry in targets:
                entry.update(status="error", reason="VIDEO_METRICS_REFRESH_FAILED")
    return {"items": result}
