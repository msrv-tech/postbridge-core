"""Отложенная публикация постов Postbridge: draft→published по UTC и fan-out live-sync в Core."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from postbridge.models.domain import BridgeOrm, ChannelOrm, ContentItemOrm
from postbridge.services.bridge_adaptation import adapt_post_for_bridge
from postbridge.services.live_sync_queue import queue_live_sync_publish
from postbridge.services.postbridge_workspace_content import (
    SOURCE_TYPE,
    _dump_extra,
    _load_extra,
)

logger = logging.getLogger(__name__)

_DEFAULT_BATCH = 50


class LiveSyncEnqueueError(RuntimeError):
    def __init__(self, queued_count: int):
        super().__init__("live sync job queueing failed")
        self.queued_count = queued_count


def _enqueue_live_sync_jobs(jobs: list[_LiveSyncEnqueue]) -> None:
    queued_count = 0
    for job in jobs:
        try:
            queue_live_sync_publish(
                source_channel=job.source_channel,
                target_channel=job.target_channel,
                post=job.post,
                workspace_id=job.workspace_id,
                target_platform=job.target_platform,
                core_tenant_id=job.core_tenant_id,
                target_core_channel_id=job.target_core_channel_id,
                producer="scheduled_postbridge",
            )
            queued_count += 1
        except Exception as exc:
            raise LiveSyncEnqueueError(queued_count) from exc


@dataclass(slots=True)
class _LiveSyncEnqueue:
    source_channel: str
    target_channel: str
    post: dict[str, Any]
    workspace_id: str
    target_platform: str
    core_tenant_id: str
    target_core_channel_id: str


def _list_due_content_ids(
    session: Session, *, now_utc: datetime, limit: int
) -> list[str]:
    bind = session.get_bind()
    dialect = bind.dialect.name if bind is not None else ""
    if dialect == "postgresql":
        rows = session.execute(
            text(
                """
                SELECT id FROM content_items
                WHERE source_type = :st AND status = 'draft'
                  AND body_structured_json IS NOT NULL
                  AND (body_structured_json::jsonb->'postbridge_extra'->>'scheduled_publish_at') IS NOT NULL
                  AND (body_structured_json::jsonb->'postbridge_extra'->>'scheduled_publish_at')::timestamptz <= :now_ts
                ORDER BY (body_structured_json::jsonb->'postbridge_extra'->>'scheduled_publish_at')::timestamptz ASC
                LIMIT :lim
                """
            ),
            {
                "st": SOURCE_TYPE,
                "now_ts": now_utc,
                "lim": limit,
            },
        )
        return [str(r[0]) for r in rows]
    stmt = (
        select(ContentItemOrm.id)
        .where(
            ContentItemOrm.source_type == SOURCE_TYPE,
            ContentItemOrm.status == "draft",
            ContentItemOrm.body_structured_json.isnot(None),
        )
        .limit(limit * 20)
    )
    ids: list[str] = []
    for cid in session.scalars(stmt):
        row = session.get(ContentItemOrm, cid)
        if row is None:
            continue
        extra = _load_extra(row.body_structured_json)
        raw = extra.get("scheduled_publish_at")
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if at.tzinfo is None:
            continue
        if at.astimezone(UTC) <= now_utc:
            ids.append(row.id)
        if len(ids) >= limit:
            break
    return ids


def _str_field(extra: dict[str, Any], key: str) -> str | None:
    v = extra.get(key)
    return v if isinstance(v, str) else None


def _preserve_link_url(text: str, link_url: str | None) -> str:
    cleaned = (text or "").strip()
    link = (link_url or "").strip()
    if not link or link in cleaned:
        return cleaned
    return f"{cleaned}\n\n{link}".strip()


def try_publish_scheduled_postbridge_item(
    session: Session, *, content_id: str, now_utc: datetime
) -> tuple[bool, list[_LiveSyncEnqueue]]:
    """Одна строка: блокировка, идемпотентная проверка, публикация, список задач live-sync (до commit)."""
    row = session.scalar(
        select(ContentItemOrm)
        .where(
            ContentItemOrm.id == content_id,
            ContentItemOrm.source_type == SOURCE_TYPE,
            ContentItemOrm.status == "draft",
        )
        .with_for_update(skip_locked=True)
    )
    if row is None:
        return False, []

    extra = _load_extra(row.body_structured_json)
    raw_sched = extra.get("scheduled_publish_at")
    if not isinstance(raw_sched, str) or not raw_sched.strip():
        return False, []
    try:
        at = datetime.fromisoformat(raw_sched.replace("Z", "+00:00"))
    except ValueError:
        return False, []
    if at.tzinfo is None:
        return False, []
    if at.astimezone(UTC) > now_utc:
        return False, []

    src_uuid = _str_field(extra, "live_sync_source_core_channel_id")
    workspace_id = _str_field(extra, "saas_workspace_id") or ""

    content_plain = _str_field(extra, "content_plain")
    text_base = (
        content_plain
        if content_plain is not None and content_plain != ""
        else (row.body_markdown or "").strip()
    )
    out_media_url = row.media_url or _str_field(extra, "cover_image_url")
    out_media_urls = list(row.media_urls) if row.media_urls else None
    if out_media_url and (not out_media_urls or out_media_url not in out_media_urls):
        out_media_urls = [out_media_url] + [
            u for u in (out_media_urls or []) if u != out_media_url
        ]

    base_post: dict[str, Any] = {
        "source_post_id": row.id,
        "media_url": out_media_url,
        "media_urls": out_media_urls,
    }
    source: dict[str, Any] = {
        "text": text_base or "",
        "title": row.title,
        "summary": _str_field(extra, "summary"),
        "cta": _str_field(extra, "cta"),
        "link_url": _str_field(extra, "link_url"),
    }

    extra.pop("scheduled_publish_at", None)
    if not extra.get("published_at"):
        extra["published_at"] = now_utc.isoformat()
    row.status = "published"
    row.body_structured_json = _dump_extra(extra)
    row.updated_at = now_utc
    session.flush()

    jobs: list[_LiveSyncEnqueue] = []
    if not src_uuid:
        logger.warning(
            "scheduled postbridge publish: no live_sync_source_core_channel_id, skip fan-out: content_id=%s",
            row.id,
        )
        return True, jobs

    source_ch = session.get(ChannelOrm, src_uuid)
    if (
        source_ch is None
        or source_ch.tenant_id != row.tenant_id
        or source_ch.platform != "postbridge"
    ):
        logger.warning(
            "scheduled postbridge publish: invalid source channel, skip fan-out: content_id=%s channel_id=%s",
            row.id,
            src_uuid,
        )
        return True, jobs

    source_logical = source_ch.external_id or source_ch.id
    bridges = list(
        session.scalars(
            select(BridgeOrm).where(
                BridgeOrm.tenant_id == row.tenant_id,
                BridgeOrm.source_channel_id == src_uuid,
                BridgeOrm.status == "active",
                BridgeOrm.mode == "live_sync",
            )
        ).all()
    )
    for b in bridges:
        tgt = session.get(ChannelOrm, b.target_channel_id)
        if tgt is None or tgt.tenant_id != row.tenant_id:
            continue
        adaptation = adapt_post_for_bridge(
            session,
            tenant_id=row.tenant_id,
            post=source,
            platform=tgt.platform,
            bridge_settings=b.settings_json,
            target_channel_id=tgt.id,
            content_item_id=row.id,
        )
        if adaptation.status == "needs_review":
            logger.info(
                "scheduled postbridge publish: bridge adaptation requires review, skip target fan-out: content_id=%s bridge_id=%s target_channel_id=%s",
                row.id,
                b.id,
                tgt.id,
            )
            continue
        link_url = _str_field(extra, "link_url")
        adapted_text = _preserve_link_url(adaptation.text, link_url)
        post_data = {**base_post, "text": adapted_text}
        if link_url:
            post_data["link_url"] = link_url
        jobs.append(
            _LiveSyncEnqueue(
                source_channel=source_logical,
                target_channel=tgt.external_id or tgt.id,
                post=post_data,
                workspace_id=workspace_id,
                target_platform=tgt.platform,
                core_tenant_id=row.tenant_id,
                target_core_channel_id=tgt.id,
            )
        )
    session.flush()
    return True, jobs


def process_due_scheduled_postbridge_publishes(
    session: Session, *, now_utc: datetime | None = None, batch_size: int = _DEFAULT_BATCH
) -> int:
    """Обрабатывает пачку постов с наступившим scheduled_publish_at; после каждого успеха — commit и очередь."""
    now = now_utc or datetime.now(UTC)
    ids = _list_due_content_ids(session, now_utc=now, limit=batch_size)
    published = 0
    for cid in ids:
        ok, jobs = try_publish_scheduled_postbridge_item(
            session, content_id=cid, now_utc=now
        )
        if not ok:
            session.rollback()
            continue
        try:
            _enqueue_live_sync_jobs(jobs)
        except LiveSyncEnqueueError as exc:
            if exc.queued_count == 0:
                session.rollback()
                logger.error(
                    "scheduled postbridge publish: live-sync queue failed before commit, "
                    "keeping draft for retry: content_id=%s",
                    cid,
                    exc_info=exc,
                )
                continue
            logger.warning(
                "scheduled postbridge publish: live-sync queue partially failed after %s jobs; "
                "committing published state to avoid duplicate retries: content_id=%s",
                exc.queued_count,
                cid,
            )
        session.commit()
        published += 1
    return published
