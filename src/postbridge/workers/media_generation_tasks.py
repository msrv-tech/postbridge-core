"""Celery tasks for background media generation."""

from __future__ import annotations

import logging
import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from postbridge.db import SESSION_LOCAL
from postbridge.domain.errors import PostbridgeError
from postbridge.models.domain import ContentItemOrm, MediaGenerationJobOrm, TenantOrm
from postbridge.services.ai_image_generation import build_post_image_prompt, generate_image_bytes
from postbridge.services.media_assets import store_media_asset
from postbridge.services.postbridge_workspace_content import update_postbridge_content_item
from postbridge.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _mark_failed(session: Session, job: MediaGenerationJobOrm, exc: Exception) -> None:
    job.status = "failed"
    job.completed_at = datetime.now(UTC)
    if isinstance(exc, PostbridgeError):
        job.error_code = exc.code
        job.error_message = exc.message
        job.error_payload = {
            "details": exc.details,
            "source": exc.source,
            "retryable": exc.retryable,
        }
    else:
        job.error_code = "MEDIA_GENERATION_FAILED"
        job.error_message = str(exc)
        job.error_payload = {}
    session.add(job)
    session.commit()


def _patch_content_item(session: Session, job: MediaGenerationJobOrm, url: str) -> None:
    if not job.content_item_id:
        return
    row = session.get(ContentItemOrm, job.content_item_id)
    if row is None or row.tenant_id != job.tenant_id:
        return
    if job.target == "cover":
        update_postbridge_content_item(session, row=row, cover_image_url=url)
    else:
        update_postbridge_content_item(session, row=row, media_url=url, media_urls=[url], cover_image_url=url)
    structured = {}
    if row.body_structured_json:
        try:
            loaded = json.loads(row.body_structured_json)
            if isinstance(loaded, dict):
                structured = loaded
        except json.JSONDecodeError:
            structured = {}
    structured["cover_image_url"] = url
    postbridge_extra = structured.get("postbridge") if isinstance(structured.get("postbridge"), dict) else {}
    postbridge_extra["cover_image_url"] = url
    structured["postbridge"] = postbridge_extra
    row.body_structured_json = json.dumps(structured, ensure_ascii=True)
    session.add(row)


@celery_app.task(name="postbridge.media_generation.process_job")
def process_media_generation_job_task(job_id: str, correlation_id: str | None = None) -> dict[str, str]:
    session: Session = SESSION_LOCAL()
    try:
        job = session.get(MediaGenerationJobOrm, job_id)
        if job is None:
            logger.warning("Media generation job %s not found", job_id)
            return {"status": "missing"}
        if job.status not in {"pending", "running"}:
            return {"status": job.status}

        job.status = "running"
        session.add(job)
        session.commit()
        session.refresh(job)

        try:
            payload = dict(job.request_payload or {})
            tenant = session.get(TenantOrm, job.tenant_id)
            if tenant is None:
                raise RuntimeError(f"Tenant {job.tenant_id} not found")
            final_prompt = build_post_image_prompt(
                user_prompt=payload.get("prompt"),
                title=payload.get("title"),
                summary=payload.get("summary"),
                content_md=payload.get("content_md"),
                style_prompt=payload.get("style_prompt") or (tenant.image_style_prompt or ""),
            )
            result = generate_image_bytes(
                final_prompt,
                model=payload.get("model"),
                correlation_id=correlation_id or job.correlation_id,
            )
            stored = store_media_asset(
                session,
                tenant_id=job.tenant_id,
                data=result.data,
                content_type=result.content_type,
            )
            _patch_content_item(session, job, stored["url"])
            job.status = "completed"
            job.url = stored["url"]
            job.media_asset_id = stored["media_asset_id"]
            job.prompt = final_prompt
            job.usage_tokens_charged = result.usage_tokens_charged
            job.error_code = None
            job.error_message = None
            job.error_payload = None
            job.completed_at = datetime.now(UTC)
            session.add(job)
            session.commit()
            return {"status": job.status}
        except Exception as exc:
            logger.warning("Media generation job %s failed: %s", job_id, exc, exc_info=True)
            session.rollback()
            session.refresh(job)
            _mark_failed(session, job, exc)
            return {"status": "failed"}
    finally:
        session.close()
