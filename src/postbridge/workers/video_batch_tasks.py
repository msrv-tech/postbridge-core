"""Advance one batch item at a time; provider calls stay in ordinary video jobs."""
from datetime import UTC, datetime, timedelta
import logging
from uuid import uuid4

import httpx
from sqlalchemy import func, or_, select

from postbridge.config import get_settings
from postbridge.db import SESSION_LOCAL
from postbridge.domain.errors import PostbridgeError
from postbridge.models.domain import VideoBatchOrm, VideoBatchItemOrm, VideoProductionJobOrm, VideoRevisionOrm
from postbridge.services.video_batches import VoiceBatchCreate, create_voice_batch, _batch
from postbridge.services.video_media import video_error
from postbridge.services.video_schedules import utc
from postbridge.services.video_speech import VoiceVariantRequest, create_voice_variant
from postbridge.services.video_studio import VideoCreate, create_project, job_usage
from postbridge.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
ACTIVE = {"queued", "running"}


def dispatch_video_batches(factory=None):
    if not get_settings().video_studio_enabled:
        return
    with (factory or SESSION_LOCAL)() as db:
        ids = db.scalars(select(VideoBatchOrm.id).where(VideoBatchOrm.status.in_(ACTIVE),
            or_(VideoBatchOrm.lease_until.is_(None), VideoBatchOrm.lease_until < datetime.now(UTC)))
            .order_by(VideoBatchOrm.created_at).limit(20)).all()
    for batch_id in ids:
        celery_app.send_task("postbridge.video.advance_batch", args=[batch_id], queue="video")


def _refresh(db, row):
    items = db.scalars(select(VideoBatchItemOrm).where(VideoBatchItemOrm.batch_id == row.id)
                       .order_by(VideoBatchItemOrm.position)).all()
    for item in items:
        if not item.revision_id or item.status not in ACTIVE:
            continue
        job = db.scalar(select(VideoProductionJobOrm).where(VideoProductionJobOrm.revision_id == item.revision_id))
        if not job:
            raise video_error("VIDEO_BATCH_RESULT_MISSING", "A batch item lost its production job")
        if job.status in ACTIVE:
            continue
        if job.status != "succeeded":
            item.status, item.error_code = job.status, job.error_code
            if job_usage(job)["uncertain"]:
                item.error_code = "VIDEO_RECONCILIATION_REQUIRED"
            continue
        variants = row.policy_json.get("variants") if row.kind == "projects" else None
        if variants:
            nested_id = (item.result_json or {}).get("voice_batch_id")
            if not nested_id:
                revision = db.get(VideoRevisionOrm, item.revision_id)
                nested = create_voice_batch(db, row.tenant_id, item.project_id, VoiceBatchCreate(
                    request_key=f"variants:{item.id}", created_by=row.created_by,
                    expected_version=revision.version, source_version=revision.version,
                    max_units_total=row.policy_json.get("max_units_total"), variants=variants),
                    policy={**row.policy_json, "root_batch_id": row.policy_json.get("root_batch_id", row.id)}, commit=False)
                nested_id = nested["id"]
                item.result_json = {"voice_batch_id": nested_id}
            nested_row = _batch(db, row.tenant_id, nested_id)
            if nested_row.status in ACTIVE:
                continue
            item.status, item.error_code = nested_row.status, nested_row.error_code
        else:
            item.status = "succeeded"
    failed = next((item for item in items if item.status in {"failed", "cancelled"}), None)
    if failed:
        row.status, row.error_code, row.completed_at = "failed", failed.error_code or "VIDEO_BATCH_STOPPED", datetime.now(UTC)
        for item in items:
            if item.status == "queued":
                item.status, item.error_code = "cancelled", "VIDEO_BATCH_STOPPED"
    elif all(item.status == "succeeded" for item in items):
        row.status, row.completed_at = "succeeded", datetime.now(UTC)
    return items


def _grant(db, row, item):
    settings = get_settings()
    if settings.postbridge_app_mode == "saas":
        with httpx.Client(timeout=30) as client:
            response = client.post(f"{settings.saas_base_url.rstrip('/')}/internal/video/reserve-batch-item",
                headers={"X-Core-Event-Token": settings.status_event_webhook_token},
                json={"tenant_id": row.tenant_id, "batch_id": row.id, "item_id": item.id})
        if response.status_code >= 500:
            raise ConnectionError("Video batch authorization is unavailable")
        if response.status_code != 200:
            import re
            try:
                code = response.json().get("code", "")
            except (ValueError, AttributeError):
                code = ""
            if not isinstance(code, str) or not re.fullmatch(r"[A-Z0-9_]{1,128}", code):
                code = "VIDEO_BUDGET_NOT_AUTHORIZED"
            raise video_error(code, "This video batch is no longer authorized")
        return response.json()
    paid = row.kind == "voices" or item.payload_json.get("mode") == "autopilot" or item.payload_json.get("narration_enabled") or any(
        not s.get("asset_id") or s.get("regenerate_image") or s.get("regenerate_video") for s in item.payload_json.get("scenes", []))
    if not paid:
        return {"reservation_id": None, "limit_units": 0}
    from postbridge.models.domain import TenantOrm, VideoAgentExecutionOrm
    db.scalar(select(TenantOrm).where(TenantOrm.id == row.tenant_id).with_for_update())
    root = _batch(db, row.tenant_id, row.policy_json.get("root_batch_id", row.id))
    # A root admits one original/voice at a time; include all descendant charges.
    batches = db.scalars(select(VideoBatchOrm).where(VideoBatchOrm.tenant_id == row.tenant_id)).all()
    ids = [b.id for b in batches if b.id == root.id or b.policy_json.get("root_batch_id") == root.id]
    jobs = db.scalars(select(VideoProductionJobOrm).join(VideoBatchItemOrm,
        VideoBatchItemOrm.revision_id == VideoProductionJobOrm.revision_id).where(VideoBatchItemOrm.batch_id.in_(ids))).all()
    used = 0
    for job in jobs:
        usage = job_usage(job)
        if usage["uncertain"]:
            raise video_error("VIDEO_RECONCILIATION_REQUIRED", "Reconcile earlier generation before continuing")
        used += max(usage["tokens_charged"], (job.progress_json or {}).get("billing", {}).get("limit_units", 0)) if job.status in ACTIVE else usage["tokens_charged"]
    remaining = (root.policy_json.get("max_units_total") or 100_000_000) - used
    run_id = root.policy_json.get("agent_run_id")
    if run_id:
        execution = db.get(VideoAgentExecutionOrm, run_id)
        monthly = execution.input_snapshot.get("max_units_per_month")
        if monthly:
            from postbridge.workers.video_schedule_tasks import selfhost_monthly_usage
            remaining = min(remaining, monthly - selfhost_monthly_usage(db, execution))
    if remaining <= 0:
        raise video_error("VIDEO_BATCH_BUDGET_EXHAUSTED", "This batch reached its total budget")
    return {"reservation_id": None, "limit_units": min(settings.video_ai_selfhost_budget_units, remaining)}


@celery_app.task(name="postbridge.video.advance_batch", acks_late=True, reject_on_worker_lost=True,
                 soft_time_limit=120, time_limit=150)
def advance_video_batch(batch_id):
    if not get_settings().video_studio_enabled:
        return
    token, now = str(uuid4()), datetime.now(UTC)
    with SESSION_LOCAL() as db:
        row = db.scalar(select(VideoBatchOrm).where(VideoBatchOrm.id == batch_id).with_for_update())
        if row is None or row.status not in ACTIVE or row.lease_until and utc(row.lease_until) > now:
            return
        row.lease_token, row.lease_until = token, now + timedelta(seconds=180)
        db.commit()
        try:
            row = db.scalar(select(VideoBatchOrm).where(VideoBatchOrm.id == batch_id).with_for_update()
                .execution_options(populate_existing=True))
            if row.status not in ACTIVE or row.lease_token != token:
                return
            items = _refresh(db, row)
            if row.status not in ACTIVE or any(item.status == "running" for item in items):
                row.lease_until = None
                db.commit()
                return
            item = next((item for item in items if item.status == "queued"), None)
            active = db.scalar(select(func.count()).select_from(VideoProductionJobOrm).where(
                VideoProductionJobOrm.tenant_id == row.tenant_id, VideoProductionJobOrm.status.in_(ACTIVE)))
            if item is None or active >= 2:
                row.lease_until = None
                db.commit()
                return
            if row.kind == "voices" and "expected_version" not in item.payload_json:
                item.payload_json = {**item.payload_json, "expected_version": row.expected_project_version,
                    "source_version": row.source_version}
            row.lease_token, row.lease_until = token, now + timedelta(seconds=180)
            row.status = "running"
            db.commit()
            grant = _grant(db, row, item)
            db.expire_all()
            row = db.scalar(select(VideoBatchOrm).where(VideoBatchOrm.id == batch_id).with_for_update()
                            .execution_options(populate_existing=True))
            if row.status not in ACTIVE or row.lease_token != token:
                return
            item = db.get(VideoBatchItemOrm, item.id)
            body = {**item.payload_json, "billing_reservation_id": grant.get("reservation_id"),
                "billing_budget_units": grant.get("limit_units", 0)}
            if row.kind == "voices":
                result = create_voice_variant(db, row.tenant_id, row.source_project_id,
                    VoiceVariantRequest.model_validate(body), commit=False)
                row.expected_project_version = result["current_version"]
            else:
                result = create_project(db, row.tenant_id, VideoCreate.model_validate(body), commit=False)
            revision = result.get("requested_revision", result["current"])
            item.project_id, item.revision_id, item.status = result["id"], revision["id"], "running"
            row.lease_until = None
            db.commit()
        except Exception as exc:
            db.rollback()
            row = db.scalar(select(VideoBatchOrm).where(VideoBatchOrm.id == batch_id).with_for_update()
                            .execution_options(populate_existing=True))
            if row is None or row.status not in ACTIVE or row.lease_token not in {None, token}:
                return
            if isinstance(exc, PostbridgeError) and exc.code != "VIDEO_QUEUE_FULL":
                row.status, row.error_code, row.completed_at = "failed", exc.code, datetime.now(UTC)
                for pending in db.scalars(select(VideoBatchItemOrm).where(VideoBatchItemOrm.batch_id == batch_id,
                    VideoBatchItemOrm.status == "queued")):
                    pending.status, pending.error_code = "failed", exc.code
                row.lease_until = None
            else:
                row.lease_until = datetime.now(UTC) + timedelta(seconds=30)
            db.commit()
            logger.warning("Video batch deferred or stopped: %s (%s)", batch_id, type(exc).__name__)
