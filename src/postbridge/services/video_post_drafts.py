"""Prepare a post, without approving or dispatching any external publication."""
from datetime import UTC
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from postbridge.infrastructure.media_storage import managed_media_url
from postbridge.models.domain import ContentItemOrm, MediaAssetOrm, VideoPostDraftOrm, VideoProductionJobOrm, VideoRevisionOrm
from postbridge.services.postbridge_workspace_content import create_postbridge_content_item, _dump_extra, _load_extra
from postbridge.services.video_media import video_error
from postbridge.services.video_studio import _project, require_enabled


class PrepareVideoPost(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: int = Field(ge=1)
    caption: str = Field(default="", max_length=4000)
    source_version: int | None = Field(default=None, ge=1)


def draft_payload(session: Session, draft: VideoPostDraftOrm) -> dict:
    content = session.get(ContentItemOrm, draft.content_item_id) if draft.content_item_id else None
    return {"content_item_id": content.id if content and content.tenant_id == draft.tenant_id else None,
            "status": content.status if content and content.tenant_id == draft.tenant_id else "deleted",
            "created_at": draft.created_at.replace(tzinfo=UTC) if draft.created_at.tzinfo is None else draft.created_at.astimezone(UTC)}


def prepare_post_draft(session: Session, tenant_id: str, project_id: str, payload: PrepareVideoPost) -> dict:
    require_enabled()
    # Serialize handoff and scene revisions on the same project. No network call or queue
    # operation belongs in this transaction; Core commits the post and receipt together.
    project = _project(session, tenant_id, project_id, lock=True)
    revision = session.scalar(select(VideoRevisionOrm).where(VideoRevisionOrm.project_id == project.id,
        VideoRevisionOrm.version == (payload.source_version or payload.expected_version)))
    if revision is None:
        raise video_error("VIDEO_VERSION_CONFLICT", "The project changed; refresh before preparing a post")
    receipt = session.scalar(select(VideoPostDraftOrm).where(VideoPostDraftOrm.revision_id == revision.id))
    if receipt:
        if receipt.caption != payload.caption:
            raise video_error("VIDEO_POST_DRAFT_EXISTS", "This version already has a post; edit its caption in the post editor")
        return {"project_id": project.id, "version": revision.version, **draft_payload(session, receipt)}
    if project.current_version != payload.expected_version:
        raise video_error("VIDEO_VERSION_CONFLICT", "The project changed; refresh before preparing a post")
    job = session.scalar(select(VideoProductionJobOrm).where(VideoProductionJobOrm.revision_id == revision.id))
    asset = session.get(MediaAssetOrm, job.output_asset_id) if job and job.output_asset_id else None
    if not job or job.status != "succeeded" or not asset or asset.tenant_id != tenant_id or asset.content_type != "video/mp4":
        raise video_error("VIDEO_NOT_READY", "Wait for the video to finish before preparing a post")
    cover = session.get(MediaAssetOrm, (job.progress_json.get("cover") or {}).get("asset_id")) if (job.progress_json or {}).get("cover") else None
    cover_url = managed_media_url(cover.id, cover.object_key) if cover and cover.tenant_id == tenant_id else None
    url = managed_media_url(asset.id, asset.object_key)
    origin = {"project_id": project.id, "version": revision.version, "output_asset_id": asset.id, "output_url": url}
    # The production brief can contain private instructions. Only the explicit caption
    # becomes publishable text. A later video revision never rewrites this post.
    content = create_postbridge_content_item(session, tenant_id=tenant_id, author_user_id=None,
        content_md=payload.caption, content_plain=None, title=None, media_url=url, media_urls=[url],
        summary=None, link_url=None, cta=None, tags=None, author=None, cover_image_url=cover_url, status="draft")
    content.language = (revision.production_options or {}).get("language")
    content.body_structured_json = _dump_extra({**_load_extra(content.body_structured_json), "video_origin": origin})
    receipt = VideoPostDraftOrm(id=str(uuid4()), tenant_id=tenant_id, revision_id=revision.id,
                               content_item_id=content.id, caption=payload.caption)
    session.add(receipt)
    session.commit()
    return {"project_id": project.id, "version": revision.version, **draft_payload(session, receipt)}
