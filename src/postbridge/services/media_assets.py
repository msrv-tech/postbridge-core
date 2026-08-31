"""Media asset persistence helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from postbridge.domain.errors import ValidationError
from postbridge.infrastructure.media_storage import (
    delete_media_object,
    upload_media_object,
)
from postbridge.models.domain import MediaAssetOrm, TenantOrm


def store_media_asset(
    session: Session,
    *,
    tenant_id: str,
    data: bytes,
    content_type: str,
) -> dict[str, str]:
    content_type = (content_type or "").split(";")[0].strip().lower()
    row = session.get(TenantOrm, tenant_id)
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
    asset_id = str(uuid4())
    object_key = f"tenants/{tenant_id}/media/{asset_id}.{ext}"
    try:
        url = upload_media_object(object_key, data, content_type)
    except RuntimeError as exc:
        raise ValidationError(
            code="MEDIA_STORAGE_NOT_CONFIGURED",
            message=str(exc),
            message_key="error.validation.media_storage_not_configured",
            details={},
        ) from exc
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
    session.commit()
    return {"media_asset_id": asset_id, "url": url}


def delete_media_asset(session: Session, *, tenant_id: str, media_asset_id: str) -> None:
    row = session.get(MediaAssetOrm, media_asset_id)
    if row is None or row.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_MEDIA_ASSET_NOT_FOUND",
            message="media asset not found",
            message_key="error.validation.media_asset_not_found",
            details={"media_asset_id": media_asset_id},
        )
    try:
        delete_media_object(row.object_key)
    except RuntimeError as exc:
        raise ValidationError(
            code="MEDIA_STORAGE_NOT_CONFIGURED",
            message=str(exc),
            message_key="error.validation.media_storage_not_configured",
            details={},
        ) from exc
    session.delete(row)
    session.commit()
