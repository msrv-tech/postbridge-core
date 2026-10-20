"""
Исполнение publication_target через registry publishers (фаза 2).

Строка канала для publish_post: ключ target_channel в ChannelOrm.config_json (JSON-объект),
иначе используется external_id.
Креды MAX/VK/Telegram: JSON в channel_credentials (Fernet в encrypted_secret / fallback meta_json);
при отсутствии — publishers могут взять креды из env (как в существующих интеграциях).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from postbridge.domain.errors import InternalError, PostbridgeError, ValidationError
from postbridge.domain.models import PostPayload
from postbridge.integrations.base import TargetPublisher
from postbridge.integrations.channel_credentials import load_channel_credential_row
from postbridge.integrations.registry import decode_publish_credentials_for_platform, get_publisher
from postbridge.observability.failure_class import classify_publication_failure
from postbridge.observability.metrics import inc_publication_failure
from postbridge.storage.publication_status_event_outbox import (
    enqueue_publication_target_status_changed,
)
from postbridge.models.domain import (
    ChannelOrm,
    ContentItemOrm,
    PublicationPlanOrm,
    PublicationTargetOrm,
    RenderVariantOrm,
)

logger = logging.getLogger(__name__)

PUBLICATION_TARGET_PENDING = "pending"
PUBLICATION_TARGET_PUBLISHING = "publishing"
PUBLICATION_TARGET_PUBLISHED = "published"
PUBLICATION_TARGET_FAILED = "failed"


def _resolve_target_channel_slug(channel: ChannelOrm) -> str:
    import json

    if channel.config_json:
        try:
            cfg = json.loads(channel.config_json)
            if isinstance(cfg, dict):
                slug = cfg.get("target_channel")
                if isinstance(slug, str) and slug.strip():
                    return slug.strip()
        except json.JSONDecodeError:
            pass
    if channel.external_id and channel.external_id.strip():
        return channel.external_id.strip()
    raise ValidationError(
        code="VALIDATION_CHANNEL_NO_TARGET_SLUG",
        message="channel has no target_channel in config_json and no external_id",
        details={"channel_id": channel.id},
    )


def claim_publication_target_pending(
    session: Session,
    target_id: str,
    *,
    correlation_id: str | None = None,
) -> bool:
    """pending → publishing, commit. True если строка захвачена."""
    now = datetime.now(UTC)
    from postbridge.services.video_publication import approval_for_plan
    target = session.get(PublicationTargetOrm, target_id)
    approval = approval_for_plan(session, target.publication_plan_id) if target else None
    if approval:
        from postbridge.config import get_settings
        from postbridge.services.video_delivery_time import admit_target, now_utc
        if not get_settings().video_publication_enabled:
            session.rollback()
            return False
        if target.status != "processing" and not admit_target(session, approval):
            return False
        now = now_utc()
    result = session.execute(
        update(PublicationTargetOrm)
        .where(
            PublicationTargetOrm.id == target_id,
            PublicationTargetOrm.status == ("processing" if target and target.status == "processing" and approval else PUBLICATION_TARGET_PENDING),
        )
        .values(status=PUBLICATION_TARGET_PUBLISHING, updated_at=now)
    )
    if result.rowcount != 1:
        session.rollback()
        return False
    target = session.get(PublicationTargetOrm, target_id)
    if target is not None:
        enqueue_publication_target_status_changed(session, target, correlation_id=correlation_id)
    session.commit()
    return True


def mark_publication_target_failed(
    session: Session,
    target_id: str,
    error: PostbridgeError,
    *,
    correlation_id: str | None = None,
) -> None:
    """publishing → failed с полями ошибки."""
    inc_publication_failure(classify_publication_failure(error))
    now = datetime.now(UTC)
    target = session.get(PublicationTargetOrm, target_id)
    if target is None:
        return
    target.status = PUBLICATION_TARGET_FAILED
    target.error_code = error.code
    target.error_message = (error.message or "")[:2048]
    target.updated_at = now
    enqueue_publication_target_status_changed(session, target, correlation_id=correlation_id)
    session.commit()


def schedule_publication_target_retry(
    session: Session,
    target_id: str,
    exc: PostbridgeError,
    max_retries: int,
    *,
    correlation_id: str | None = None,
) -> bool:
    """failed → pending при допустимом retry (как JobStore.schedule_retry)."""
    from postbridge.services.video_publication import approval_for_plan
    candidate = session.get(PublicationTargetOrm, target_id)
    if candidate and approval_for_plan(session, candidate.publication_plan_id):
        return False
    if not exc.retryable:
        return False
    target = session.get(PublicationTargetOrm, target_id)
    if target is None or target.status != PUBLICATION_TARGET_FAILED:
        return False
    if target.retry_count >= max_retries:
        return False
    now = datetime.now(UTC)
    target.retry_count += 1
    target.status = PUBLICATION_TARGET_PENDING
    target.error_code = None
    target.error_message = None
    target.updated_at = now
    enqueue_publication_target_status_changed(session, target, correlation_id=correlation_id)
    session.commit()
    return True


def recover_stuck_publication_targets(session: Session, *, timeout_seconds: int) -> list[str]:
    """Recover ordinary targets; approved video claims require external reconciliation."""
    cutoff = datetime.now(UTC) - timedelta(seconds=timeout_seconds)
    now = datetime.now(UTC)
    stuck_ids = list(
        session.scalars(
            select(PublicationTargetOrm.id).where(
                PublicationTargetOrm.status == PUBLICATION_TARGET_PUBLISHING,
                PublicationTargetOrm.updated_at < cutoff,
            )
        ).all()
    )
    if not stuck_ids:
        return []
    from postbridge.services.video_publication import approval_for_plan
    recovered = []
    for target_id in stuck_ids:
        target = session.get(PublicationTargetOrm, target_id)
        video = approval_for_plan(session, target.publication_plan_id)
        values = {"status": "unknown" if video else PUBLICATION_TARGET_PENDING, "updated_at": now}
        if video:
            values.update(error_code="VIDEO_DELIVERY_UNKNOWN",
                error_message="Worker stopped after claiming delivery; check the destination before any resend")
        changed = session.execute(update(PublicationTargetOrm).where(PublicationTargetOrm.id == target_id,
            PublicationTargetOrm.status == PUBLICATION_TARGET_PUBLISHING, PublicationTargetOrm.updated_at < cutoff)
            .values(**values).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            continue
        session.refresh(target)
        if video:
            enqueue_publication_target_status_changed(session, target, correlation_id="video-stuck-recovery")
        else:
            recovered.append(target_id)
    session.commit()
    return recovered


def _build_post_payload(
    content_item_id: str,
    render: RenderVariantOrm | None,
    content: ContentItemOrm | None,
) -> PostPayload:
    text = ""
    if render:
        text = (render.body_text or render.title or "").strip()
    if not text and content:
        text = (content.body_markdown or content.title or "").strip()
    source_post_id = content_item_id
    media_url: str | None = None
    media_urls: list[str] | None = None
    if content:
        if content.media_url and str(content.media_url).strip():
            media_url = str(content.media_url).strip()
        raw_list = content.media_urls
        if isinstance(raw_list, list):
            media_urls = [u for u in raw_list if isinstance(u, str) and u]
            if not media_urls:
                media_urls = None
    return PostPayload(
        source_post_id=source_post_id,
        text=text or " ",
        media_url=media_url,
        media_urls=media_urls,
    )


class PublicationTargetExecutor:
    """Публикация одного publication_target через TargetPublisher."""

    def __init__(self, session: Session, publisher: TargetPublisher | None = None):
        self.session = session
        self._publisher = publisher

    def run(self, target_id: str, correlation_id: str | None = None) -> int:
        """
        Идемпотентно публикует target. Возвращает 1 при успешной публикации, 0 при пропуске.
        Бросает PostbridgeError при ошибке публикации (после перевода target в failed).
        """
        target = self.session.get(PublicationTargetOrm, target_id)
        if target is None:
            raise ValidationError(
                code="VALIDATION_PUBLICATION_TARGET_NOT_FOUND",
                message="publication target not found",
                details={"target_id": target_id},
            )

        if target.status == PUBLICATION_TARGET_PUBLISHED:
            return 0

        from postbridge.services.video_publication import approval_for_plan, approved_payload
        video_approval = approval_for_plan(self.session, target.publication_plan_id)
        resuming_video = bool(video_approval and target.status == "processing")
        if video_approval:
            from postbridge.config import get_settings
            if not get_settings().video_publication_enabled:
                return 0

        if not claim_publication_target_pending(
            self.session, target_id, correlation_id=correlation_id
        ):
            logger.info("publication_target %s claim skipped (not pending)", target_id)
            return 0

        target = self.session.get(PublicationTargetOrm, target_id)
        if target is None:
            return 0

        channel = self.session.get(ChannelOrm, target.channel_id)
        if channel is None:
            err = ValidationError(
                code="VALIDATION_CHANNEL_NOT_FOUND",
                message="channel not found for publication target",
                details={"channel_id": target.channel_id},
            )
            mark_publication_target_failed(
                self.session, target_id, err, correlation_id=correlation_id
            )
            raise err

        render = (
            self.session.get(RenderVariantOrm, target.render_variant_id)
            if target.render_variant_id
            else None
        )
        content: ContentItemOrm | None = None
        content_item_id_for_payload = target.id
        if render:
            content_item_id_for_payload = render.content_item_id
            content = self.session.get(ContentItemOrm, render.content_item_id)
        else:
            plan = self.session.get(PublicationPlanOrm, target.publication_plan_id)
            if plan:
                content_item_id_for_payload = plan.content_item_id
                content = self.session.get(ContentItemOrm, plan.content_item_id)

        logger.info(
            "publication_target_run target_id=%s tenant_id=%s channel_id=%s platform=%s correlation_id=%s",
            target_id,
            target.tenant_id,
            channel.id,
            target.platform,
            correlation_id or "",
        )
        try:
            post = approved_payload(self.session, target, content, render, check_time=not resuming_video) or _build_post_payload(
                content_item_id=content_item_id_for_payload, render=render, content=content)
            slug = _resolve_target_channel_slug(channel)
            cred_row = load_channel_credential_row(self.session, channel.id, target.tenant_id)
            credentials = decode_publish_credentials_for_platform(target.platform, cred_row)
            publisher = self._publisher or get_publisher(target.platform)
        except PostbridgeError as exc:
            mark_publication_target_failed(self.session, target_id, exc, correlation_id=correlation_id)
            raise

        def unknown(error):
            self.session.rollback()
            row = self.session.get(PublicationTargetOrm, target_id)
            row.status, row.error_code, row.error_message = "unknown", "VIDEO_DELIVERY_UNKNOWN", error.message[:2048]
            row.updated_at = datetime.now(UTC)
            enqueue_publication_target_status_changed(self.session, row, correlation_id=correlation_id)
            self.session.commit()

        from postbridge.services.video_delivery_state import DeliveryPending, VideoDeliveryContext
        receipt = None
        try:
            if video_approval and target.platform in {"youtube", "instagram"}:
                delivery = VideoDeliveryContext(self.session, target_id, video_approval.snapshot)
                receipt = publisher.publish_video(slug, post, credentials, delivery)
                external_id = receipt.external_id
            else:
                external_id = publisher.publish_post(slug, post, credentials=credentials)
            if video_approval and not external_id:
                raise InternalError("Video delivery returned no external identifier")
        except DeliveryPending:
            target = self.session.get(PublicationTargetOrm, target_id)
            target.status, target.error_code, target.error_message = "processing", None, None
            target.updated_at = datetime.now(UTC)
            enqueue_publication_target_status_changed(self.session, target, correlation_id=correlation_id)
            self.session.commit()
            return 0
        except PostbridgeError as exc:
            if video_approval and (exc.retryable or isinstance(exc, InternalError)):
                unknown(exc)
                raise
            mark_publication_target_failed(
                self.session, target_id, exc, correlation_id=correlation_id
            )
            raise
        except Exception as exc:
            internal = InternalError(
                "Unexpected publish error",
                details={
                    "exception_type": type(exc).__name__,
                    "target_id": target_id,
                },
            )
            if video_approval:
                unknown(internal)
            else:
                mark_publication_target_failed(self.session, target_id, internal, correlation_id=correlation_id)
            raise internal from exc

        now = datetime.now(UTC)
        target = self.session.get(PublicationTargetOrm, target_id)
        if target:
            target.status = PUBLICATION_TARGET_PUBLISHED
            target.published_at = now
            target.external_post_id = str(external_id) if external_id else None
            if receipt:
                target.external_url = receipt.url
            elif video_approval and external_id:
                raw_slug = slug.removeprefix("tg/").removeprefix("@")
                import re
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,}", raw_slug):
                    target.external_url = f"https://t.me/{raw_slug}/{external_id}"
            target.error_code = None
            target.error_message = None
            target.updated_at = now
            enqueue_publication_target_status_changed(
                self.session, target, correlation_id=correlation_id
            )
            if video_approval and content:
                self.session.flush()
                remaining = self.session.scalar(select(PublicationTargetOrm.id).where(
                    PublicationTargetOrm.publication_plan_id == target.publication_plan_id,
                    PublicationTargetOrm.status != PUBLICATION_TARGET_PUBLISHED).limit(1))
                if remaining is None:
                    from postbridge.services.postbridge_workspace_content import _load_extra, _dump_extra
                    content.status = "published"
                    extra = _load_extra(content.body_structured_json)
                    extra["published_at"] = now.isoformat()
                    content.body_structured_json = _dump_extra(extra)
            self.session.commit()
        return 1
