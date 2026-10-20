"""Prepare agent video occurrences, then hand off to the existing production queue."""
from datetime import UTC, datetime, timedelta
import logging
import re
from uuid import uuid4

import httpx
from sqlalchemy import or_, select

from postbridge.config import get_settings
from postbridge.db import SESSION_LOCAL
from postbridge.domain.errors import PostbridgeError
from postbridge.models.domain import AgentRunOrm, VideoAgentExecutionOrm, VideoProjectOrm
from postbridge.services.video_schedules import synchronize_video_agent_runs, utc
from postbridge.services.video_studio import VideoCreate, create_project
from postbridge.services.video_media import video_error
from postbridge.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def dispatch_video_agent_executions(factory=None):
    settings = get_settings()
    with (factory or SESSION_LOCAL)() as db:
        synchronize_video_agent_runs(db)
        db.commit()
        pending = db.scalars(select(VideoAgentExecutionOrm.id).join(AgentRunOrm,
            AgentRunOrm.id == VideoAgentExecutionOrm.id).where(
            VideoAgentExecutionOrm.project_id.is_(None), AgentRunOrm.status.in_(["queued", "running"]),
            or_(VideoAgentExecutionOrm.lease_until.is_(None), VideoAgentExecutionOrm.lease_until < datetime.now(UTC)))
            .order_by(VideoAgentExecutionOrm.created_at).limit(20)).all() if settings.video_studio_enabled else []
        completed = db.scalars(select(VideoAgentExecutionOrm.id).join(AgentRunOrm,
            AgentRunOrm.id == VideoAgentExecutionOrm.id).where(VideoAgentExecutionOrm.notification_acknowledged == 0,
            AgentRunOrm.status.in_(["awaiting_review", "failed", "cancelled"])).limit(20)).all()
    for run_id in pending:
        celery_app.send_task("postbridge.video.prepare_agent_run", args=[run_id], queue="video")
    for run_id in completed:
        celery_app.send_task("postbridge.video.report_agent_run", args=[run_id], queue="video")


def callback(path, execution):
    settings = get_settings()
    with httpx.Client(timeout=30) as client:
        response = client.post(f"{settings.saas_base_url.rstrip('/')}/internal/video/{path}",
            headers={"X-Core-Event-Token": settings.status_event_webhook_token},
            json={"run_id": execution.id, "tenant_id": execution.tenant_id})
    if response.status_code >= 500:
        raise ConnectionError("Video scheduling callback unavailable")
    if response.status_code != 200:
        try:
            code = response.json().get("code", "")
        except (ValueError, AttributeError):
            code = ""
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z0-9_]{1,128}", code):
            code = "VIDEO_BUDGET_NOT_AUTHORIZED"
        raise video_error(code, "Scheduled video production is no longer authorized")
    return response.json()


def selfhost_budget(db, execution, body):
    settings = get_settings()
    snapshot = execution.input_snapshot
    if body.mode == "manual" and not body.narration_enabled and not any(not s.asset_id or s.regenerate_image for s in body.scenes):
        return 0
    limit = min(settings.video_ai_selfhost_budget_units, snapshot.get("max_units_per_run") or 100_000_000)
    monthly = snapshot.get("max_units_per_month")
    if monthly:
        from postbridge.models.domain import VideoProductionJobOrm, VideoRevisionOrm
        # The task lock serializes admission against other occurrences of this template.
        from postbridge.models.domain import AgentTaskOrm
        db.scalar(select(AgentTaskOrm).where(AgentTaskOrm.id == execution.task_id).with_for_update())
        admitted = utc(db.get(AgentRunOrm, execution.id).started_at or datetime.now(UTC))
        start = admitted.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        rows = db.execute(select(VideoProductionJobOrm).join(VideoRevisionOrm,
            VideoRevisionOrm.id == VideoProductionJobOrm.revision_id).join(VideoAgentExecutionOrm,
            VideoAgentExecutionOrm.project_id == VideoRevisionOrm.project_id).where(
            VideoAgentExecutionOrm.task_id == execution.task_id, VideoAgentExecutionOrm.id != execution.id,
            VideoRevisionOrm.version == 1,
            VideoAgentExecutionOrm.id.in_(select(AgentRunOrm.id).where(
                AgentRunOrm.started_at >= start, AgentRunOrm.started_at < end)))).scalars().all()
        total = 0
        for job in rows:
            progress = job.progress_json or {}
            steps = progress.get("steps", {}).values()
            spent = sum(step.get("units", 0) for step in steps)
            uncertain = any(step.get("state") in {"started", "unknown"} for step in steps)
            total += max(spent, progress.get("billing", {}).get("limit_units", 0)) if uncertain or job.status in {"queued", "running"} else spent
        if total + limit > monthly:
            raise video_error("VIDEO_SCHEDULE_MONTHLY_BUDGET", "The schedule reached its monthly video budget")
    return limit


@celery_app.task(name="postbridge.video.prepare_agent_run", acks_late=True, reject_on_worker_lost=True,
                 soft_time_limit=120, time_limit=150)
def prepare_video_agent_run(run_id):
    settings = get_settings()
    if not settings.video_studio_enabled:
        return
    token, now = str(uuid4()), datetime.now(UTC)
    with SESSION_LOCAL() as db:
        execution = db.scalar(select(VideoAgentExecutionOrm).where(VideoAgentExecutionOrm.id == run_id).with_for_update())
        run = db.get(AgentRunOrm, run_id)
        if execution is None or run.status not in {"queued", "running"} or execution.project_id:
            return
        if execution.lease_until and utc(execution.lease_until) > now:
            return
        existing = db.scalar(select(VideoProjectOrm).where(VideoProjectOrm.tenant_id == execution.tenant_id,
            VideoProjectOrm.request_key == execution.input_snapshot["video"]["request_key"]))
        if existing:
            # Recover a committed project even if its acknowledgement or run linkage was lost.
            execution.project_id, execution.lease_until = existing.id, None
            run.status, run.started_at = "running", run.started_at or now
            db.commit()
            return
        if execution.attempts >= 3:
            run.status, run.error_code, run.completed_at = "failed", "VIDEO_SCHEDULE_PREPARATION_FAILED", now
            db.commit()
            return
        execution.attempts += 1
        execution.lease_token, execution.lease_until = token, now + timedelta(seconds=180)
        run.status, run.started_at = "running", run.started_at or now
        db.commit()
        try:
            body = VideoCreate.model_validate(execution.input_snapshot["video"])
            if settings.postbridge_app_mode == "saas":
                grant = callback("reserve-agent-run", execution)
                body = body.model_copy(update={"billing_reservation_id": grant.get("reservation_id"),
                    "billing_budget_units": grant.get("limit_units", 0)})
            else:
                body = body.model_copy(update={"billing_budget_units": selfhost_budget(db, execution, body)})
            # Check lease again after HTTP. A crash/replay uses the same production key.
            db.expire_all()
            execution = db.scalar(select(VideoAgentExecutionOrm).where(VideoAgentExecutionOrm.id == run_id).with_for_update())
            if execution.lease_token != token:
                return
            project = create_project(db, execution.tenant_id, body, agent_run_id=run_id)
            execution.project_id = project["id"]
            execution.lease_until = None
            db.commit()
        except Exception as exc:
            db.rollback()
            execution = db.scalar(select(VideoAgentExecutionOrm).where(VideoAgentExecutionOrm.id == run_id).with_for_update())
            if execution.lease_token != token:
                return
            run = db.get(AgentRunOrm, run_id)
            if not isinstance(exc, PostbridgeError):
                # Reservation is idempotent; retry preparation, never a paid generation call.
                execution.lease_until = datetime.now(UTC) + timedelta(seconds=30)
                run.status = "queued"
            else:
                run.status, run.completed_at = "failed", datetime.now(UTC)
                run.error_code = exc.code if isinstance(exc, PostbridgeError) else "VIDEO_SCHEDULE_PREPARATION_FAILED"
                if run.error_code == "VIDEO_SCHEDULE_ACTOR_REVOKED" and execution.task_id:
                    from postbridge.models.domain import AgentTaskOrm
                    task = db.get(AgentTaskOrm, execution.task_id)
                    if task and task.status == "active":
                        task.status, task.next_run_at = "paused", None
            db.commit()
            logger.warning("Video agent run preparation deferred or stopped: %s (%s)", run_id, type(exc).__name__)


@celery_app.task(name="postbridge.video.report_agent_run", soft_time_limit=45, time_limit=60)
def report_video_agent_run(run_id):
    with SESSION_LOCAL() as db:
        execution = db.get(VideoAgentExecutionOrm, run_id)
        run = db.get(AgentRunOrm, run_id)
        if execution is None or execution.notification_acknowledged or run.status not in {"awaiting_review", "failed", "cancelled"}:
            return
        if get_settings().postbridge_app_mode == "saas":
            try:
                callback("agent-run-finished", execution)
            except Exception:
                logger.warning("Video completion notification deferred: %s", run_id)
                return
        execution.notification_acknowledged = 1
        db.commit()
