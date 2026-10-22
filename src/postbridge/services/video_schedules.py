"""Video templates and durable occurrences on the existing agent scheduler."""
from datetime import UTC, datetime
import json
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from postbridge.agent.scheduling import next_run_at_from_cron
from postbridge.config import get_settings
from postbridge.models.domain import (AgentRunOrm, AgentTaskOrm, ChannelOrm, TenantOrm,
    VideoAgentExecutionOrm, VideoProductionJobOrm, VideoRevisionOrm)
from postbridge.services.video_media import video_error
from postbridge.services.video_studio import VideoCreate, _check_assets, capabilities, require_enabled
from postbridge.services.video_batches import ProjectBatchCreate, VoiceChoice


from postbridge.services.video_auto_publication import AutoPublicationPolicy


class VideoTaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9:_-]+$")
    video: VideoCreate
    count: int = Field(default=1, ge=1, le=5)
    variants: list[VoiceChoice] = Field(default_factory=list, max_length=6)
    max_units_per_run: int | None = Field(default=None, ge=1, le=100_000_000)
    max_units_per_month: int | None = Field(default=None, ge=1, le=100_000_000)
    notify: bool = True
    auto_publication: AutoPublicationPolicy | None = None


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def validate_template(config, goal_text):
    try:
        value = VideoTaskConfig.model_validate(config)
        if value.video.billing_reservation_id or value.video.billing_budget_units or value.video.brief != goal_text:
            raise ValueError("Template contains internal billing data or a different task")
        if value.video.mode == "manual" and not value.video.scenes:
            raise ValueError("Manual templates need materials")
        ProjectBatchCreate(request_key=value.request_key, video=value.video, count=value.count, variants=value.variants)
        return value
    except (ValidationError, ValueError) as exc:
        raise video_error("VIDEO_SCHEDULE_INVALID", "Check the video task, materials and budget settings") from exc


def create_video_agent_task(session, *, tenant_id, channel_id, goal_text, schedule_cron, timezone,
                            task_config, created_by, autonomy_mode, max_candidates_per_run):
    require_enabled()
    config = validate_template(task_config, goal_text)
    if autonomy_mode != "draft_approval" or max_candidates_per_run != 1:
        raise video_error("VIDEO_SCHEDULE_INVALID", "Video tasks create one bounded draft batch per run")
    if not capabilities()["supports_schedules"]:
        raise video_error("VIDEO_SCHEDULE_UNAVAILABLE", "Video scheduling is not configured")
    if config.video.mode == "autopilot" and not capabilities()["autopilot_enabled"]:
        raise video_error("VIDEO_AUTOPILOT_UNAVAILABLE", "Video autopilot is not configured")
    if (config.variants or config.video.narration_enabled) and not capabilities()["speech_enabled"]:
        raise video_error("VIDEO_SPEECH_UNAVAILABLE", "Google speech generation is not configured")
    if (config.video.generated_visuals == "video" or any(s.regenerate_video or not s.asset_id and s.generation_kind == "video" for s in config.video.scenes)) and not capabilities()["video_generation_enabled"]:
        raise video_error("VIDEO_GENERATION_UNAVAILABLE", "AI video generation is not configured")
    try:
        from croniter import croniter
        ZoneInfo(timezone or "UTC")
        if schedule_cron and (len(schedule_cron.split()) != 5 or not croniter.is_valid(schedule_cron)):
            raise ValueError("Invalid cron")
        next_run = next_run_at_from_cron(schedule_cron, timezone=timezone)
    except (ImportError, ValueError, KeyError, ZoneInfoNotFoundError) as exc:
        raise video_error("VIDEO_SCHEDULE_INVALID", "Choose a valid schedule and timezone") from exc
    channel = session.get(ChannelOrm, channel_id)
    if channel is None or channel.tenant_id != tenant_id or channel.platform != "postbridge":
        raise video_error("VIDEO_SCHEDULE_CONTEXT_INVALID", "Choose a Postbridge editorial context")
    session.scalar(select(TenantOrm).where(TenantOrm.id == tenant_id).with_for_update())
    _check_assets(session, tenant_id, [scene.model_dump() for scene in config.video.scenes])
    task_id = str(uuid5(NAMESPACE_URL, f"postbridge-video-task:{tenant_id}:{config.request_key}"))
    existing = session.get(AgentTaskOrm, task_id)
    canonical = config.model_dump()
    if existing:
        if (existing.goal_text != goal_text or VideoTaskConfig.model_validate_json(existing.task_config_json).model_dump() != canonical
                or existing.channel_id != channel_id or existing.schedule_cron != schedule_cron
                or existing.timezone != (timezone or "UTC") or existing.created_by != created_by):
            raise video_error("VIDEO_REQUEST_CONFLICT", "This schedule request has different settings")
        return existing
    row = AgentTaskOrm(id=task_id, tenant_id=tenant_id, channel_id=channel_id, mode="video_autopilot",
        goal_text=goal_text, schedule_cron=schedule_cron, timezone=timezone or "UTC", max_candidates_per_run=1,
        autonomy_mode="draft_approval", task_config_json=json.dumps(canonical), created_by=created_by,
        status="active", next_run_at=next_run)
    session.add(row)
    session.flush()
    if config.auto_publication:
        from types import SimpleNamespace
        from postbridge.services.video_auto_publication import assert_policy
        assert_policy(session, tenant_id, SimpleNamespace(task_id=row.id, input_snapshot={
            "auto_publication": config.auto_publication.model_dump(), "created_by": created_by}))
    return row


def enqueue_video_agent_run(session, *, tenant_id, task_id, occurrence_key=None, scheduled=False, now=None):
    if not get_settings().video_studio_enabled and scheduled:
        return None
    require_enabled()
    now = now or datetime.now(UTC)
    task = session.scalar(select(AgentTaskOrm).where(AgentTaskOrm.id == task_id,
        AgentTaskOrm.tenant_id == tenant_id).with_for_update().execution_options(populate_existing=True))
    if task is None or task.mode != "video_autopilot" or task.status == "archived":
        raise video_error("VIDEO_SCHEDULE_NOT_FOUND", "Video task not found")
    if scheduled and (task.status != "active" or task.next_run_at is None or utc(task.next_run_at) > now):
        return None
    key = f"slot:{utc(task.next_run_at).isoformat()}" if scheduled else f"manual:{occurrence_key or uuid4()}"
    if len(key) > 80:
        raise video_error("VIDEO_SCHEDULE_INVALID", "Run request key is too long")
    run_id = str(uuid5(NAMESPACE_URL, f"postbridge-video-run:{task.id}:{key}"))
    previous = session.get(AgentRunOrm, run_id)
    if previous:
        return previous
    config = validate_template(json.loads(task.task_config_json), task.goal_text)
    active = session.scalar(select(AgentRunOrm.id).where(AgentRunOrm.agent_task_id == task.id,
        AgentRunOrm.status.in_(["queued", "running"])).limit(1))
    run = AgentRunOrm(id=run_id, tenant_id=tenant_id, channel_id=task.channel_id, agent_task_id=task.id,
        graph_name="video_autopilot", trigger_type="scheduled" if scheduled else "manual",
        user_request=task.goal_text, status="cancelled" if active else "queued",
        completed_at=now if active else None, error_code="VIDEO_SCHEDULE_OVERLAP" if active else None)
    session.add(run)
    session.flush()
    production = config.video.model_dump(exclude={"billing_reservation_id", "billing_budget_units"})
    production["request_key"] = f"agent-video:{run_id}"
    session.add(VideoAgentExecutionOrm(id=run_id, tenant_id=tenant_id, task_id=task.id, occurrence_key=key,
        input_snapshot={"video": production, "created_by": task.created_by,
            "count": config.count, "variants": [v.model_dump() for v in config.variants],
            "max_units_per_run": config.max_units_per_run, "max_units_per_month": config.max_units_per_month,
            "notify": config.notify, "timezone": task.timezone,
            "auto_publication": config.auto_publication.model_dump() if config.auto_publication else None}, notification_acknowledged=1 if active else 0))
    task.last_run_at = now
    if scheduled:
        # Advance from now: missed occurrences never produce a catch-up spending burst.
        task.next_run_at = next_run_at_from_cron(task.schedule_cron, base=now, timezone=task.timezone)
    session.flush()
    return run


def video_execution_payload(session, run):
    execution = session.get(VideoAgentExecutionOrm, run.id)
    if execution is None:
        return None
    result = {"project_id": execution.project_id, "batch_id": execution.batch_id, "task_id": execution.task_id,
            "occurrence_key": execution.occurrence_key, "input_snapshot": execution.input_snapshot,
            "auto_publication": execution.publication_json or {}}
    if execution.batch_id:
        from postbridge.services.video_batches import get_batch
        result["batch"] = get_batch(session, execution.tenant_id, execution.batch_id)
    return result


def synchronize_video_agent_runs(session):
    from sqlalchemy import or_
    from postbridge.models.domain import VideoBatchOrm, VideoBatchItemOrm
    rows = session.execute(select(VideoAgentExecutionOrm, AgentRunOrm).join(AgentRunOrm,
        VideoAgentExecutionOrm.id == AgentRunOrm.id).where(or_(VideoAgentExecutionOrm.project_id.is_not(None), VideoAgentExecutionOrm.batch_id.is_not(None)),
        AgentRunOrm.status.in_(["queued", "running"])).with_for_update()).all()
    for execution, run in rows:
        if execution.batch_id:
            batch = session.get(VideoBatchOrm, execution.batch_id)
            first = session.scalar(select(VideoBatchItemOrm).where(VideoBatchItemOrm.batch_id == execution.batch_id)
                .order_by(VideoBatchItemOrm.position).limit(1))
            execution.project_id = first.project_id if first else None
            if batch.status in {"queued", "running"}:
                continue
            run.status = "awaiting_review" if batch.status == "succeeded" else batch.status
            run.completed_at, run.error_code = datetime.now(UTC), batch.error_code
            run.trace_json = json.dumps({"result": {"video_batch_id": batch.id,
                "video_project_id": execution.project_id, "status": batch.status}, "trace": [], "tool_summary": {}})
            run.token_usage_json = "{}"
            continue
        revision = session.scalar(select(VideoRevisionOrm).where(VideoRevisionOrm.project_id == execution.project_id,
            VideoRevisionOrm.request_key == execution.input_snapshot["video"]["request_key"]))
        job = session.scalar(select(VideoProductionJobOrm).where(VideoProductionJobOrm.revision_id == revision.id)) if revision else None
        if job is None or job.status in {"queued", "running"}:
            continue
        run.status = "awaiting_review" if job.status == "succeeded" else job.status
        run.completed_at = datetime.now(UTC)
        run.error_code = job.error_code
        run.trace_json = json.dumps({"result": {"video_project_id": execution.project_id,
            "video_version": revision.version, "status": job.status}, "trace": [], "tool_summary": {}})
        # Generation charges belong only to the video ledger, never the generic agent usage event.
        run.token_usage_json = "{}"
    session.flush()
