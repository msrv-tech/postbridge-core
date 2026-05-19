"""Контент «постов Postbridge» в каноне Core: content_items с source_type=postbridge."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from postbridge.domain.errors import ValidationError
from postbridge.db import RssFeedItemOrm
from postbridge.models.domain import ChannelOrm, ContentItemOrm

SOURCE_TYPE = "postbridge"
_EXTRA_KEY = "postbridge_extra"
POSTBRIDGE_SCHEDULE_UNSET = object()
_SCHEDULE_UNSET = POSTBRIDGE_SCHEDULE_UNSET


def _dump_extra(extra: dict[str, Any]) -> str | None:
    clean = {k: v for k, v in extra.items() if v is not None}
    if not clean:
        return None
    return json.dumps({_EXTRA_KEY: clean}, ensure_ascii=True)


def _load_extra(body_structured_json: str | None) -> dict[str, Any]:
    if not body_structured_json or not body_structured_json.strip():
        return {}
    try:
        data = json.loads(body_structured_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    inner = data.get(_EXTRA_KEY)
    return inner if isinstance(inner, dict) else {}


def _assert_live_sync_source_channel(
    session: Session, *, tenant_id: str, channel_id: str
) -> None:
    ch = session.get(ChannelOrm, channel_id)
    if ch is None or ch.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_CHANNEL_NOT_FOUND",
            message="live_sync_source_core_channel_id not found or wrong tenant",
            details={"channel_id": channel_id},
        )
    if ch.platform != "postbridge":
        raise ValidationError(
            code="VALIDATION_POSTBRIDGE_SOURCE_CHANNEL",
            message="live_sync_source_core_channel_id must be a postbridge channel",
            details={"channel_id": channel_id, "platform": ch.platform},
        )


def _validate_scheduled_publish_at(at: datetime) -> None:
    if at.tzinfo is None:
        raise ValidationError(
            code="VALIDATION_SCHEDULE_DATETIME",
            message="scheduled_publish_at must be timezone-aware (UTC)",
            details={},
        )
    u = at.astimezone(UTC)
    if u <= datetime.now(UTC):
        raise ValidationError(
            code="VALIDATION_SCHEDULE_FUTURE",
            message="scheduled_publish_at must be in the future",
            details={},
        )
    if u.second != 0 or u.microsecond != 0:
        raise ValidationError(
            code="VALIDATION_SCHEDULE_GRID",
            message="scheduled_publish_at seconds must be 0",
            details={},
        )
    if u.minute % 5 != 0:
        raise ValidationError(
            code="VALIDATION_SCHEDULE_GRID",
            message="scheduled_publish_at minute must be a multiple of 5 (UTC wall)",
            details={},
        )


def content_item_to_api_dict(row: ContentItemOrm) -> dict[str, Any]:
    extra = _load_extra(row.body_structured_json)
    published_raw = extra.get("published_at")
    published_at = None
    if isinstance(published_raw, str) and published_raw.strip():
        try:
            published_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
        except ValueError:
            published_at = None
    sched_raw = extra.get("scheduled_publish_at")
    scheduled_publish_at = None
    if isinstance(sched_raw, str) and sched_raw.strip():
        try:
            scheduled_publish_at = datetime.fromisoformat(sched_raw.replace("Z", "+00:00"))
        except ValueError:
            scheduled_publish_at = None
    tags = extra.get("tags")
    if tags is not None and not isinstance(tags, list):
        tags = None
    status = row.status if row.status in ("draft", "published") else "draft"
    src_ch = extra.get("live_sync_source_core_channel_id")
    if not isinstance(src_ch, str):
        src_ch = None
    if status == "draft" and scheduled_publish_at is None:
        src_ch = None
    ws_id = extra.get("saas_workspace_id")
    if not isinstance(ws_id, str):
        ws_id = None
    return {
        "id": row.id,
        "content_md": row.body_markdown or "",
        "content_plain": extra.get("content_plain"),
        "media_url": row.media_url,
        "media_urls": list(row.media_urls) if row.media_urls else None,
        "title": row.title,
        "summary": extra.get("summary"),
        "link_url": extra.get("link_url"),
        "cta": extra.get("cta"),
        "tags": tags,
        "author": extra.get("author"),
        "cover_image_url": extra.get("cover_image_url"),
        "status": status,
        "published_at": published_at,
        "scheduled_publish_at": scheduled_publish_at,
        "live_sync_source_core_channel_id": src_ch,
        "saas_workspace_id": ws_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def create_postbridge_content_item(
    session: Session,
    *,
    tenant_id: str,
    author_user_id: str | None,
    content_md: str,
    content_plain: str | None,
    media_url: str | None,
    media_urls: list[str] | None,
    title: str | None,
    summary: str | None,
    link_url: str | None,
    cta: str | None,
    tags: list[str] | None,
    author: str | None,
    cover_image_url: str | None,
    status: str,
    scheduled_publish_at: datetime | None = None,
    live_sync_source_core_channel_id: str | None = None,
    saas_workspace_id: str | None = None,
) -> ContentItemOrm:
    if status not in ("draft", "published"):
        raise ValidationError(
            code="VALIDATION_INVALID_STATUS",
            message="status must be draft or published",
            details={"status": status},
        )
    if scheduled_publish_at is not None:
        if status == "published":
            raise ValidationError(
                code="VALIDATION_SCHEDULE_CONFLICT",
                message="cannot set scheduled_publish_at when publishing",
                details={},
            )
        if status != "draft":
            raise ValidationError(
                code="VALIDATION_SCHEDULE_REQUIRES_DRAFT",
                message="scheduled_publish_at requires status draft",
                details={},
            )
        if not live_sync_source_core_channel_id:
            raise ValidationError(
                code="VALIDATION_SCHEDULE_SOURCE_REQUIRED",
                message="live_sync_source_core_channel_id is required when scheduling",
                details={},
            )
        _assert_live_sync_source_channel(
            session,
            tenant_id=tenant_id,
            channel_id=live_sync_source_core_channel_id,
        )
        _validate_scheduled_publish_at(scheduled_publish_at)
    elif status == "published" and live_sync_source_core_channel_id:
        _assert_live_sync_source_channel(
            session,
            tenant_id=tenant_id,
            channel_id=live_sync_source_core_channel_id,
        )
    should_store_live_sync_source = (
        bool(live_sync_source_core_channel_id)
        and (scheduled_publish_at is not None or status == "published")
    )
    extra: dict[str, Any] = {
        "content_plain": content_plain,
        "summary": summary,
        "link_url": link_url,
        "cta": cta,
        "tags": tags,
        "author": author,
        "cover_image_url": cover_image_url,
    }
    if status == "published":
        extra["published_at"] = datetime.now(UTC).isoformat()
    if should_store_live_sync_source:
        extra["live_sync_source_core_channel_id"] = live_sync_source_core_channel_id
    if scheduled_publish_at is not None:
        extra["scheduled_publish_at"] = scheduled_publish_at.astimezone(UTC).isoformat()
        if saas_workspace_id:
            extra["saas_workspace_id"] = saas_workspace_id
    structured = _dump_extra(extra)
    cid = str(uuid4())
    row = ContentItemOrm(
        id=cid,
        tenant_id=tenant_id,
        author_user_id=author_user_id,
        source_type=SOURCE_TYPE,
        title=title,
        body_markdown=content_md,
        body_structured_json=structured,
        language=None,
        status=status,
        media_url=media_url,
        media_urls=media_urls,
    )
    session.add(row)
    session.flush()
    return row


def get_postbridge_content_item(
    session: Session, *, tenant_id: str, content_id: str
) -> ContentItemOrm | None:
    row = session.get(ContentItemOrm, content_id)
    if row is None or row.tenant_id != tenant_id or row.source_type != SOURCE_TYPE:
        return None
    return row


def list_postbridge_content_items(
    session: Session,
    *,
    tenant_id: str,
    status: str | None,
    limit: int,
    offset: int,
) -> list[ContentItemOrm]:
    stmt = select(ContentItemOrm).where(
        ContentItemOrm.tenant_id == tenant_id,
        ContentItemOrm.source_type == SOURCE_TYPE,
    )
    if status:
        stmt = stmt.where(ContentItemOrm.status == status)
    stmt = stmt.order_by(ContentItemOrm.created_at.desc()).limit(limit).offset(offset)
    return list(session.scalars(stmt).all())


def update_postbridge_content_item(
    session: Session,
    *,
    row: ContentItemOrm,
    content_md: str | None = None,
    content_plain: str | None = None,
    media_url: str | None = None,
    media_urls: list[str] | None = None,
    title: str | None = None,
    summary: str | None = None,
    link_url: str | None = None,
    cta: str | None = None,
    tags: list[str] | None = None,
    author: str | None = None,
    cover_image_url: str | None = None,
    status: str | None = None,
    scheduled_publish_at: datetime | None | object = _SCHEDULE_UNSET,
    live_sync_source_core_channel_id: str | None | object = _SCHEDULE_UNSET,
    saas_workspace_id: str | None | object = _SCHEDULE_UNSET,
) -> ContentItemOrm:
    extra = _load_extra(row.body_structured_json)
    if content_md is not None:
        row.body_markdown = content_md
    if title is not None:
        row.title = title
    if media_url is not None:
        row.media_url = media_url
    if media_urls is not None:
        row.media_urls = media_urls
    if content_plain is not None:
        extra["content_plain"] = content_plain
    if summary is not None:
        extra["summary"] = summary
    if link_url is not None:
        extra["link_url"] = link_url
    if cta is not None:
        extra["cta"] = cta
    if tags is not None:
        extra["tags"] = tags
    if author is not None:
        extra["author"] = author
    if cover_image_url is not None:
        extra["cover_image_url"] = cover_image_url

    if live_sync_source_core_channel_id is not _SCHEDULE_UNSET:
        if live_sync_source_core_channel_id is None:
            extra.pop("live_sync_source_core_channel_id", None)
        else:
            _assert_live_sync_source_channel(
                session,
                tenant_id=row.tenant_id,
                channel_id=live_sync_source_core_channel_id,
            )
            extra["live_sync_source_core_channel_id"] = live_sync_source_core_channel_id

    if saas_workspace_id is not _SCHEDULE_UNSET:
        if saas_workspace_id is None or saas_workspace_id == "":
            extra.pop("saas_workspace_id", None)
        else:
            extra["saas_workspace_id"] = saas_workspace_id

    if scheduled_publish_at is not _SCHEDULE_UNSET:
        if scheduled_publish_at is None:
            extra.pop("scheduled_publish_at", None)
            extra.pop("live_sync_source_core_channel_id", None)
        else:
            eff_status = status if status is not None else row.status
            if eff_status == "published":
                raise ValidationError(
                    code="VALIDATION_SCHEDULE_CONFLICT",
                    message="cannot set scheduled_publish_at when publishing",
                    details={},
                )
            if eff_status != "draft":
                raise ValidationError(
                    code="VALIDATION_SCHEDULE_REQUIRES_DRAFT",
                    message="scheduled_publish_at requires status draft",
                    details={},
                )
            _validate_scheduled_publish_at(scheduled_publish_at)
            src = extra.get("live_sync_source_core_channel_id")
            if not isinstance(src, str) or not src.strip():
                raise ValidationError(
                    code="VALIDATION_SCHEDULE_SOURCE_REQUIRED",
                    message="live_sync_source_core_channel_id is required when scheduling",
                    details={},
                )
            extra["scheduled_publish_at"] = scheduled_publish_at.astimezone(UTC).isoformat()

    if status is not None:
        if status not in ("draft", "published"):
            raise ValidationError(
                code="VALIDATION_INVALID_STATUS",
                message="status must be draft or published",
                details={"status": status},
            )
        row.status = status
        if status == "published":
            extra.pop("scheduled_publish_at", None)
            if not extra.get("published_at"):
                extra["published_at"] = datetime.now(UTC).isoformat()
    effective_status = status if status is not None else row.status
    if effective_status == "draft" and "scheduled_publish_at" not in extra:
        extra.pop("live_sync_source_core_channel_id", None)
    structured = _dump_extra(extra)
    row.body_structured_json = structured
    row.updated_at = datetime.now(UTC)
    session.flush()
    return row


def delete_postbridge_content_item(session: Session, *, row: ContentItemOrm) -> None:
    session.execute(
        delete(RssFeedItemOrm).where(RssFeedItemOrm.source_post_id == row.id)
    )
    session.delete(row)
    session.flush()
