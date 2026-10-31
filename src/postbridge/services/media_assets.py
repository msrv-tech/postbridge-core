"""Media asset persistence helpers."""

from __future__ import annotations

import logging
import hashlib
import tempfile
from pathlib import Path
from datetime import UTC, datetime
from uuid import uuid4, uuid5, NAMESPACE_URL

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from postbridge.config import get_settings
from postbridge.domain.errors import ValidationError
from postbridge.infrastructure.media_storage import (
    delete_media_object,
    upload_media_object,
    managed_media_url,
)
from postbridge.models.domain import (ContentItemOrm, MediaAssetOrm, TenantOrm, VideoRevisionOrm,
    VideoProductionJobOrm, AgentTaskOrm, AgentRunOrm, VideoAgentExecutionOrm, VideoMaterialOrm)

logger = logging.getLogger(__name__)


def _public_media_url(*, asset_id: str, storage_url: str) -> str:
    settings = get_settings()
    base_url = (settings.media_base_url or "").strip().rstrip("/")
    if settings.media_storage_type == "s3" and base_url:
        return f"{base_url}/{asset_id}"
    return storage_url


def store_media_asset(
    session: Session,
    *,
    tenant_id: str,
    data: bytes,
    content_type: str,
    deduplicate: bool = False,
    library_name: str | None = None,
) -> dict[str, str]:
    content_type = (content_type or "").split(";")[0].strip().lower()
    row = (session.scalar(select(TenantOrm).where(TenantOrm.id == tenant_id).with_for_update())
           if deduplicate else session.get(TenantOrm, tenant_id))
    if row is None:
        raise ValidationError(
            code="VALIDATION_TENANT_NOT_FOUND",
            message="tenant not found",
            message_key="error.validation.tenant_not_found",
            details={"tenant_id": tenant_id},
        )
    if len(data) > 10 * 1024 * 1024:
        raise ValidationError(
            code="VALIDATION_FILE_TOO_LARGE",
            message="file too large (max 10 MB)",
            message_key="error.validation.file_too_large",
            details={},
        )
    if not content_type.startswith("image/"):
        raise ValidationError(
            code="VALIDATION_INVALID_MEDIA_TYPE",
            message="only image files are allowed",
            message_key="error.validation.invalid_media_type",
            details={"content_type": content_type},
        )
    ext = "png"
    if "jpeg" in content_type or "jpg" in content_type:
        ext = "jpg"
    elif "gif" in content_type:
        ext = "gif"
    elif "webp" in content_type:
        ext = "webp"
    asset_id = (str(uuid5(NAMESPACE_URL, f"postbridge-media:{tenant_id}:{content_type}:{hashlib.sha256(data).hexdigest()}"))
                if deduplicate else str(uuid4()))
    existing = session.get(MediaAssetOrm, asset_id) if deduplicate else None
    metadata = None
    if deduplicate and library_name and session.get(VideoMaterialOrm, asset_id) is None:
        from postbridge.services.video_media import inspect_media
        with tempfile.TemporaryDirectory(prefix="postbridge-source-image-") as directory:
            source = Path(directory) / ("source." + ext)
            source.write_bytes(data)
            metadata = inspect_media(source)
        if metadata["kind"] != "image":
            raise ValidationError(code="VALIDATION_INVALID_MEDIA_TYPE", message="only image files are allowed",
                message_key="error.validation.invalid_media_type", details={})
    def register_material():
        if metadata is not None:
            session.add(VideoMaterialOrm(asset_id=asset_id, tenant_id=tenant_id, name=library_name[:180],
                folder="", metadata_json=metadata))
    if existing is not None:
        register_material()
        session.commit()
        return {"media_asset_id": asset_id, "url": managed_media_url(asset_id, existing.object_key)}
    object_key = f"tenants/{tenant_id}/media/{asset_id}.{ext}"
    try:
        storage_url = upload_media_object(object_key, data, content_type)
    except RuntimeError as exc:
        raise ValidationError(
            code="MEDIA_STORAGE_NOT_CONFIGURED",
            message=str(exc),
            message_key="error.validation.media_storage_not_configured",
            details={},
        ) from exc
    url = _public_media_url(asset_id=asset_id, storage_url=storage_url)
    now = datetime.now(UTC)
    session.add(
        MediaAssetOrm(
            id=asset_id,
            tenant_id=tenant_id,
            object_key=object_key,
            content_type=content_type,
            byte_size=len(data),
            created_at=now,
            updated_at=now,
        )
    )
    register_material()
    session.commit()
    return {"media_asset_id": asset_id, "url": url}


def _media_asset_url_marker(media_asset_id: str) -> str:
    return f"/media/{media_asset_id}."


def _media_asset_referenced_by_content(
    session: Session,
    *,
    tenant_id: str,
    media_asset_id: str,
) -> bool:
    marker = _media_asset_url_marker(media_asset_id)
    referenced = session.scalar(
        select(ContentItemOrm.id)
        .where(
            ContentItemOrm.tenant_id == tenant_id,
            or_(
                ContentItemOrm.media_url.contains(marker),
                cast(ContentItemOrm.media_urls, String).contains(marker),
                ContentItemOrm.body_structured_json.contains(marker),
                ContentItemOrm.body_markdown.contains(marker),
            ),
        )
        .limit(1)
    )
    return referenced is not None


def delete_media_asset(session: Session, *, tenant_id: str, media_asset_id: str) -> None:
    session.scalar(select(TenantOrm).where(TenantOrm.id == tenant_id).with_for_update())
    row = session.get(MediaAssetOrm, media_asset_id)
    if row is None or row.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_MEDIA_ASSET_NOT_FOUND",
            message="media asset not found",
            message_key="error.validation.media_asset_not_found",
            details={"media_asset_id": media_asset_id},
        )
    video_reference = session.scalar(select(VideoRevisionOrm.id).where(
        VideoRevisionOrm.tenant_id == tenant_id, cast(VideoRevisionOrm.scenes, String).contains(media_asset_id)
    ).limit(1))
    video_output = session.scalar(select(VideoProductionJobOrm.id).where(
        VideoProductionJobOrm.tenant_id == tenant_id, or_(VideoProductionJobOrm.output_asset_id == media_asset_id, cast(VideoProductionJobOrm.progress_json, String).contains(media_asset_id))
    ).limit(1))
    video_template = session.scalar(select(AgentTaskOrm.id).where(AgentTaskOrm.tenant_id == tenant_id,
        AgentTaskOrm.mode == "video_autopilot", AgentTaskOrm.status != "archived",
        AgentTaskOrm.task_config_json.contains(media_asset_id)).limit(1))
    video_intent = session.scalar(select(VideoAgentExecutionOrm.id).join(AgentRunOrm,
        AgentRunOrm.id == VideoAgentExecutionOrm.id).where(VideoAgentExecutionOrm.tenant_id == tenant_id,
        AgentRunOrm.status.in_(["queued", "running"]),
        cast(VideoAgentExecutionOrm.input_snapshot, String).contains(media_asset_id)).limit(1))
    from postbridge.models.domain import VideoBatchOrm, VideoBatchItemOrm
    video_batch = session.scalar(select(VideoBatchItemOrm.id).join(VideoBatchOrm,
        VideoBatchOrm.id == VideoBatchItemOrm.batch_id).where(VideoBatchOrm.tenant_id == tenant_id,
        VideoBatchOrm.status.in_(["queued", "running"]),
        cast(VideoBatchItemOrm.payload_json, String).contains(media_asset_id)).limit(1))
    if video_reference or video_output or video_template or video_intent or video_batch:
        raise ValidationError(code="VALIDATION_MEDIA_ASSET_IN_USE", message="media asset is used by a video project", details={})
    if _media_asset_referenced_by_content(
        session,
        tenant_id=tenant_id,
        media_asset_id=media_asset_id,
    ):
        raise ValidationError(
            code="VALIDATION_MEDIA_ASSET_IN_USE",
            message="media asset is referenced by workspace content",
            message_key="error.validation.media_asset_in_use",
            details={"media_asset_id": media_asset_id},
        )
    object_key = row.object_key
    session.delete(row)
    session.commit()
    try:
        delete_media_object(object_key)
    except RuntimeError as exc:
        logger.warning(
            "Media asset %s deleted from database but storage cleanup failed: %s",
            media_asset_id,
            exc,
        )
