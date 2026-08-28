from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from postbridge.agent.orchestrator import AgentOrchestrator
from postbridge.agent.embeddings import (
    compact_embeddings,
    get_embedding_lifecycle_overview,
    maintain_embeddings,
    reindex_embedding_drift,
    reindex_channel_content_embeddings,
    reindex_content_item_embedding,
    rotate_channel_content_embeddings,
    search_content_knowledge,
)
from postbridge.agent.storage import (
    archive_agent_task,
    cleanup_agent_runtime,
    create_agent_task,
    get_agent_overview_analytics,
    get_agent_policy,
    get_agent_quality_analytics,
    get_agent_timeseries_analytics,
    get_agent_run,
    get_agent_task,
    get_content_candidate,
    get_review_item,
    list_agent_run_steps,
    list_agent_runs,
    list_agent_policies,
    list_agent_tasks,
    list_content_candidates,
    list_review_items,
    pause_agent_task,
    resolve_review_item,
    resume_agent_task,
    upsert_agent_policy,
)
from postbridge.api.service_auth import require_service_tenant
from postbridge.db import get_db_session
from postbridge.models.domain import ReviewQueueItemOrm
from postbridge.observability.logging import log_review_item_resolved
from postbridge.observability.metrics import inc_agent_review_item_resolved
from postbridge.services.ai_editor_chat import list_ai_chat_events, require_content_item_for_tenant
from postbridge.services.postbridge_workspace_content import content_item_to_api_dict
from postbridge.workers.tasks import reindex_channel_embeddings_task, reindex_content_item_embedding_task
from postbridge.config import get_settings

router = APIRouter()

_IMAGE_INTENT_RE = re.compile(
    r"(картин|изображ|обложк|иллюстрац|фото|баннер|image|cover|illustration|photo)",
    re.IGNORECASE,
)


def _review_item_public_dict(row: ReviewQueueItemOrm, materialization: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        payload = json.loads(row.review_payload_json)
    except json.JSONDecodeError:
        payload = {}
    try:
        decision = json.loads(row.decision_json) if row.decision_json else None
    except json.JSONDecodeError:
        decision = None
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "channel_id": row.channel_id,
        "agent_run_id": row.agent_run_id,
        "candidate_id": row.candidate_id,
        "status": row.status,
        "review_payload": payload,
        "decision": decision,
        "materialization": materialization or {},
        "created_at": row.created_at,
        "resolved_at": row.resolved_at,
    }


def _load_json_object(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _agent_run_public_dict(row) -> dict[str, Any]:
    trace = _load_json_object(getattr(row, "trace_json", None), {})
    result_raw = getattr(row, "result_json", None)
    result = _load_json_object(result_raw, {})
    if not result and isinstance(trace, dict):
        nested_result = trace.get("result")
        if isinstance(nested_result, dict):
            result = nested_result
    duration_ms = None
    if row.started_at and row.completed_at:
        duration_ms = int((row.completed_at - row.started_at).total_seconds() * 1000)
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "channel_id": row.channel_id,
        "agent_task_id": row.agent_task_id,
        "content_item_id": row.content_item_id,
        "graph_name": row.graph_name,
        "trigger_type": row.trigger_type,
        "status": row.status,
        "user_request": row.user_request,
        "topic_definition": row.topic_definition,
        "model": row.model,
        "provider_type": row.provider_type,
        "token_usage": _load_json_object(row.token_usage_json, {}),
        "result": result,
        "trace": trace,
        "trace_policy": trace.get("trace_policy") if isinstance(trace, dict) else None,
        "tool_summary": trace.get("tool_summary") if isinstance(trace, dict) else {},
        "duration_ms": duration_ms,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "created_at": row.created_at,
    }


def _agent_run_response_from_result(
    session: Session,
    *,
    tenant_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    run_id = result.get("agent_run_id")
    if not isinstance(run_id, str) or not run_id:
        return result
    public_payload = _agent_run_public_dict(get_agent_run(session, tenant_id=tenant_id, run_id=run_id))
    payload = dict(result)
    payload.update(public_payload)
    payload.setdefault("agent_run_id", public_payload["id"])
    return payload


def _run_step_public_dict(row) -> dict[str, Any]:
    duration_ms = None
    if row.started_at and row.completed_at:
        duration_ms = int((row.completed_at - row.started_at).total_seconds() * 1000)
    return {
        "id": row.id,
        "agent_run_id": row.agent_run_id,
        "tenant_id": row.tenant_id,
        "step_name": row.step_name,
        "status": row.status,
        "input": _load_json_object(row.input_json, {}),
        "output": _load_json_object(row.output_json, {}),
        "duration_ms": duration_ms,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "created_at": row.created_at,
    }


def _task_requests_images(editorial_instructions: str | None) -> bool:
    return bool(isinstance(editorial_instructions, str) and _IMAGE_INTENT_RE.search(editorial_instructions))


def _candidate_public_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "channel_id": row.channel_id,
        "agent_run_id": row.agent_run_id,
        "content_item_id": row.content_item_id,
        "status": row.status,
        "topic": row.topic,
        "headline": row.headline,
        "summary": row.summary,
        "body_markdown": row.body_markdown,
        "why_now": row.why_now,
        "source_bundle": _load_json_object(row.source_bundle_json, {}),
        "scores": _load_json_object(row.scores_json, {}),
        "risk_flags": _load_json_object(row.risk_flags_json, []),
        "dedup_summary": row.dedup_summary,
        "style_fit_summary": row.style_fit_summary,
        "draft": _load_json_object(row.draft_json, {}),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _latest_editor_run_for_content_item(session: Session, *, tenant_id: str, content_item_id: str):
    for row in list_agent_runs(session, tenant_id=tenant_id):
        if row.graph_name == "post_copilot" and row.content_item_id == content_item_id:
            return row
    return None


def _editor_timeline_payload(session: Session, *, tenant_id: str, content_item_id: str) -> dict[str, Any]:
    content_item = require_content_item_for_tenant(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
    )
    latest_run = _latest_editor_run_for_content_item(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
    )
    latest_run_payload = _agent_run_public_dict(latest_run) if latest_run is not None else None
    return {
        "content_item_id": content_item_id,
        "content_item": content_item_to_api_dict(content_item),
        "events": list_ai_chat_events(session, tenant_id=tenant_id, content_item_id=content_item_id),
        "latest_run": latest_run_payload,
        "session_status": latest_run_payload["status"] if latest_run_payload is not None else "idle",
    }


class AgentTaskCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_id: str = Field(
        min_length=36,
        max_length=36,
        description="Редакционный контекст Postbridge, в котором агент хранит стиль, память и review flow.",
    )
    mode: str = Field(pattern="^(post_copilot|topic_scout)$")
    goal_text: str = Field(min_length=1, max_length=20_000)
    editorial_instructions: str | None = Field(default=None, max_length=20_000)
    schedule_cron: str | None = Field(default=None, max_length=128)
    timezone: str | None = Field(default=None, max_length=64)
    max_candidates_per_run: int = Field(default=5, ge=1, le=20)
    autonomy_mode: str = Field(default="draft_approval", max_length=32)
    provider_config_id: str | None = Field(default=None, min_length=36, max_length=36)
    model_name: str | None = Field(default=None, max_length=128)
    content_item_id: str | None = Field(default=None, min_length=36, max_length=36)
    task_config: dict[str, Any] = Field(default_factory=dict)
    search_image_mode: str | None = Field(default=None, pattern="^(none|web_search|generate)$")
    seed_urls: list[str] = Field(default_factory=list, max_length=20)
    require_source_approval: bool = False
    created_by: str = Field(min_length=1, max_length=64)


class AgentRunCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_id: str = Field(
        min_length=36,
        max_length=36,
        description="Редакционный контекст Postbridge, в котором агент хранит стиль, память и review flow.",
    )
    mode: str = Field(pattern="^(post_copilot|topic_scout)$")
    user_request: str | None = Field(default=None, max_length=20_000)
    topic_definition: str | None = Field(default=None, max_length=20_000)
    content_item_id: str | None = Field(default=None, min_length=36, max_length=36)
    max_candidates: int = Field(default=5, ge=1, le=20)
    autonomy_mode: str = Field(default="draft_approval", max_length=32)
    image_request: bool = False
    seed_urls: list[str] = Field(default_factory=list, max_length=20)
    approved_image_urls: list[str] = Field(default_factory=list, max_length=20)
    require_source_approval: bool = False


class AgentEditorMessageCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_id: str = Field(
        min_length=36,
        max_length=36,
        description="Редакционный контекст Postbridge, в котором агент хранит стиль, память и review flow.",
    )
    user_request: str = Field(min_length=1, max_length=20_000)
    autonomy_mode: str = Field(default="draft_approval", max_length=32)
    image_request: bool = False
    seed_urls: list[str] = Field(default_factory=list, max_length=20)
    approved_image_urls: list[str] = Field(default_factory=list, max_length=20)
    require_source_approval: bool = False


class ReviewResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern="^(approved|rejected)$")
    review_action: str | None = Field(
        default=None,
        pattern="^(approve_as_is|approve_after_fact_check|approve_after_new_angle|reject_duplicate|reject_low_quality|reject_conflict|reject_off_strategy)$",
    )
    note: str | None = Field(default=None, max_length=4000)
    reviewer_id: str | None = Field(default=None, max_length=64)
    approved_seed_urls: list[str] = Field(default_factory=list, max_length=20)
    approved_image_urls: list[str] = Field(default_factory=list, max_length=20)


class ReindexEmbeddingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    async_mode: bool = Field(default=False)
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0, le=1_000_000)


class ReindexContentEmbeddingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    async_mode: bool = Field(default=False)
    channel_id: str = Field(min_length=36, max_length=36)


class EmbeddingLifecycleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    async_mode: bool = Field(default=False)
    channel_id: str | None = Field(default=None, min_length=36, max_length=36)
    channel_limit: int = Field(default=20, ge=1, le=200)
    item_limit: int = Field(default=100, ge=1, le=1000)
    channel_offset: int = Field(default=0, ge=0, le=1_000_000)


class KnowledgeSearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4000)
    channel_ids: list[str] | None = Field(default=None, max_length=50)
    limit: int = Field(default=8, ge=1, le=20)
    semantic_enabled: bool = True


class EmbeddingMaintenanceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    async_mode: bool = Field(default=False)
    channel_id: str | None = Field(default=None, min_length=36, max_length=36)
    prune_orphans: bool = Field(default=True)
    prune_malformed: bool = Field(default=True)
    optimize_native: bool = Field(default=True)
    row_limit: int | None = Field(default=None, ge=1, le=10_000)
    offset: int = Field(default=0, ge=0, le=1_000_000)
    after_id: str | None = Field(default=None, min_length=36, max_length=36)


class EmbeddingCompactionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    async_mode: bool = Field(default=False)
    channel_id: str | None = Field(default=None, min_length=36, max_length=36)
    candidate_retention_days: int | None = Field(default=None, ge=1, le=3650)
    optimize_native: bool = Field(default=True)


class AgentCleanupBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    async_mode: bool = Field(default=False)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    trace_retention_days: int | None = Field(default=None, ge=1, le=3650)
    review_retention_days: int | None = Field(default=None, ge=1, le=3650)
    review_body_retention_days: int | None = Field(default=None, ge=1, le=3650)
    fingerprint_retention_days: int | None = Field(default=None, ge=1, le=3650)


class AgentPolicyUpsertBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_id: str | None = Field(default=None, min_length=36, max_length=36)
    policy: dict[str, Any] = Field(default_factory=dict)


def _agent_policy_public_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "channel_id": row.channel_id,
        "policy": _load_json_object(row.policy_json, {}),
        "version": row.version,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.post("/internal/service/agent/tasks", include_in_schema=False)
def create_service_agent_task(
    body: AgentTaskCreateBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    task_config = {
        **body.task_config,
        "seed_urls": body.seed_urls,
        "require_source_approval": body.require_source_approval,
    }
    if body.search_image_mode is not None:
        task_config["search_image_mode"] = body.search_image_mode
    row = create_agent_task(
        session,
        tenant_id=tenant_id,
        channel_id=body.channel_id,
        mode=body.mode,
        goal_text=body.goal_text,
        editorial_instructions=body.editorial_instructions,
        schedule_cron=body.schedule_cron,
        timezone=body.timezone,
        max_candidates_per_run=body.max_candidates_per_run,
        autonomy_mode=body.autonomy_mode,
        provider_config_id=body.provider_config_id,
        model_name=body.model_name,
        content_item_id=body.content_item_id,
        task_config=task_config,
        created_by=body.created_by,
    )
    session.commit()
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "mode": row.mode,
        "status": row.status,
        "goal_text": row.goal_text,
        "editorial_instructions": row.editorial_instructions,
        "task_config": task_config,
        "schedule_cron": row.schedule_cron,
        "timezone": row.timezone,
        "autonomy_mode": row.autonomy_mode,
        "created_at": row.created_at,
    }


@router.get("/internal/service/agent/tasks", include_in_schema=False)
def list_service_agent_tasks(
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    rows = list_agent_tasks(session, tenant_id=tenant_id)
    return [
        {
            "id": row.id,
            "channel_id": row.channel_id,
            "mode": row.mode,
            "status": row.status,
            "goal_text": row.goal_text,
            "editorial_instructions": row.editorial_instructions,
            "task_config": AgentOrchestrator.parse_task_config(row.task_config_json),
            "schedule_cron": row.schedule_cron,
            "timezone": row.timezone,
            "autonomy_mode": row.autonomy_mode,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.post("/internal/service/agent/tasks/{task_id}/pause", include_in_schema=False)
def pause_service_agent_task(
    task_id: str,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    row = pause_agent_task(session, tenant_id=tenant_id, task_id=task_id)
    session.commit()
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "mode": row.mode,
        "status": row.status,
        "goal_text": row.goal_text,
        "editorial_instructions": row.editorial_instructions,
        "task_config": AgentOrchestrator.parse_task_config(row.task_config_json),
        "schedule_cron": row.schedule_cron,
        "timezone": row.timezone,
        "autonomy_mode": row.autonomy_mode,
        "created_at": row.created_at,
    }


@router.post("/internal/service/agent/tasks/{task_id}/resume", include_in_schema=False)
def resume_service_agent_task(
    task_id: str,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    row = resume_agent_task(session, tenant_id=tenant_id, task_id=task_id)
    session.commit()
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "mode": row.mode,
        "status": row.status,
        "goal_text": row.goal_text,
        "editorial_instructions": row.editorial_instructions,
        "task_config": AgentOrchestrator.parse_task_config(row.task_config_json),
        "schedule_cron": row.schedule_cron,
        "timezone": row.timezone,
        "autonomy_mode": row.autonomy_mode,
        "created_at": row.created_at,
    }


@router.delete("/internal/service/agent/tasks/{task_id}", include_in_schema=False)
def delete_service_agent_task(
    task_id: str,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    row = archive_agent_task(session, tenant_id=tenant_id, task_id=task_id)
    session.commit()
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "mode": row.mode,
        "status": row.status,
        "goal_text": row.goal_text,
        "editorial_instructions": row.editorial_instructions,
        "task_config": AgentOrchestrator.parse_task_config(row.task_config_json),
        "schedule_cron": row.schedule_cron,
        "timezone": row.timezone,
        "autonomy_mode": row.autonomy_mode,
        "created_at": row.created_at,
    }


@router.get("/internal/service/agent/runs", include_in_schema=False)
def list_service_agent_runs(
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    return [_agent_run_public_dict(row) for row in list_agent_runs(session, tenant_id=tenant_id)]


@router.get("/internal/service/agent/runs/{run_id}", include_in_schema=False)
def get_service_agent_run(
    run_id: str,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _agent_run_public_dict(get_agent_run(session, tenant_id=tenant_id, run_id=run_id))


@router.get("/internal/service/agent/runs/{run_id}/steps", include_in_schema=False)
def list_service_agent_run_steps(
    run_id: str,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    return [_run_step_public_dict(row) for row in list_agent_run_steps(session, tenant_id=tenant_id, run_id=run_id)]


@router.get("/internal/service/agent/candidates", include_in_schema=False)
def list_service_agent_candidates(
    run_id: str | None = None,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    return [
        _candidate_public_dict(row)
        for row in list_content_candidates(session, tenant_id=tenant_id, run_id=run_id)
    ]


@router.get("/internal/service/agent/candidates/{candidate_id}", include_in_schema=False)
def get_service_agent_candidate(
    candidate_id: str,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _candidate_public_dict(
        get_content_candidate(session, tenant_id=tenant_id, candidate_id=candidate_id)
    )


@router.get("/internal/service/agent/analytics/overview", include_in_schema=False)
def get_service_agent_analytics_overview(
    channel_id: str | None = None,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return get_agent_overview_analytics(session, tenant_id=tenant_id, channel_id=channel_id)


@router.get("/internal/service/agent/analytics/timeseries", include_in_schema=False)
def get_service_agent_analytics_timeseries(
    channel_id: str | None = None,
    days: int = 7,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return get_agent_timeseries_analytics(session, tenant_id=tenant_id, channel_id=channel_id, days=days)


@router.get("/internal/service/agent/analytics/quality", include_in_schema=False)
def get_service_agent_analytics_quality(
    channel_id: str | None = None,
    days: int | None = None,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return get_agent_quality_analytics(session, tenant_id=tenant_id, channel_id=channel_id, days=days)


@router.get("/internal/service/agent/policies", include_in_schema=False)
def list_service_agent_policies(
    channel_id: str | None = None,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]] | dict[str, Any]:
    if channel_id is not None:
        row = get_agent_policy(session, tenant_id=tenant_id, channel_id=channel_id)
        return _agent_policy_public_dict(row) if row is not None else {}
    return [_agent_policy_public_dict(row) for row in list_agent_policies(session, tenant_id=tenant_id)]


@router.put("/internal/service/agent/policies", include_in_schema=False)
def upsert_service_agent_policy(
    body: AgentPolicyUpsertBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    row = upsert_agent_policy(
        session,
        tenant_id=tenant_id,
        channel_id=body.channel_id,
        policy_payload=body.policy,
    )
    session.commit()
    return _agent_policy_public_dict(row)


@router.post("/internal/service/agent/tasks/{task_id}/run", include_in_schema=False)
def run_service_agent_task(
    task_id: str,
    request: Request,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    _ = request
    task = get_agent_task(session, tenant_id=tenant_id, task_id=task_id)
    if task.mode == "post_copilot" and task.content_item_id:
        require_content_item_for_tenant(
            session,
            tenant_id=tenant_id,
            content_item_id=task.content_item_id,
        )
    orchestrator = AgentOrchestrator(session)
    task_config = orchestrator.parse_task_config(task.task_config_json)
    result = orchestrator.run_once(
        tenant_id=tenant_id,
        channel_id=task.channel_id,
        mode=task.mode,
        user_request=task.goal_text,
        topic_definition=task.goal_text if task.mode == "topic_scout" else None,
        editorial_instructions=task.editorial_instructions,
        content_item_id=task.content_item_id,
        max_candidates=task.max_candidates_per_run,
        agent_task=task,
        image_request=_task_requests_images(task.editorial_instructions),
        search_image_mode=(
            str(task_config.get("search_image_mode"))
            if task_config.get("search_image_mode") in {"none", "web_search", "generate"}
            else None
        ),
        seed_urls=task_config.get("seed_urls") if isinstance(task_config.get("seed_urls"), list) else None,
        require_source_approval=bool(task_config.get("require_source_approval")),
    )
    session.commit()
    run_payload = _agent_run_response_from_result(session, tenant_id=tenant_id, result=result)
    return {
        "task_id": task.id,
        "task_config": task_config,
        "run": run_payload,
    }


@router.post("/internal/service/agent/runs", include_in_schema=False)
def create_service_agent_run(
    body: AgentRunCreateBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    if body.mode == "post_copilot" and body.content_item_id:
        require_content_item_for_tenant(
            session,
            tenant_id=tenant_id,
            content_item_id=body.content_item_id,
        )
    orchestrator = AgentOrchestrator(session)
    result = orchestrator.run_once(
        tenant_id=tenant_id,
        channel_id=body.channel_id,
        mode=body.mode,
        user_request=body.user_request,
        topic_definition=body.topic_definition,
        editorial_instructions=None,
        content_item_id=body.content_item_id,
        max_candidates=body.max_candidates,
        agent_task=None,
        autonomy_mode=body.autonomy_mode,
        image_request=body.image_request,
        seed_urls=body.seed_urls,
        approved_image_urls=body.approved_image_urls,
        require_source_approval=body.require_source_approval,
    )
    session.commit()
    return _agent_run_response_from_result(session, tenant_id=tenant_id, result=result)


@router.get("/internal/service/agent/content-items/{content_item_id}/timeline", include_in_schema=False)
def get_service_agent_editor_timeline(
    content_item_id: str,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    require_content_item_for_tenant(session, tenant_id=tenant_id, content_item_id=content_item_id)
    return _editor_timeline_payload(session, tenant_id=tenant_id, content_item_id=content_item_id)


@router.post("/internal/service/agent/content-items/{content_item_id}/messages", include_in_schema=False)
def create_service_agent_editor_message(
    content_item_id: str,
    body: AgentEditorMessageCreateBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    require_content_item_for_tenant(session, tenant_id=tenant_id, content_item_id=content_item_id)
    orchestrator = AgentOrchestrator(session)
    result = orchestrator.run_once(
        tenant_id=tenant_id,
        channel_id=body.channel_id,
        mode="post_copilot",
        user_request=body.user_request,
        topic_definition=None,
        editorial_instructions=None,
        content_item_id=content_item_id,
        max_candidates=1,
        agent_task=None,
        autonomy_mode=body.autonomy_mode,
        image_request=body.image_request,
        seed_urls=body.seed_urls,
        approved_image_urls=body.approved_image_urls,
        require_source_approval=body.require_source_approval,
    )
    session.commit()
    run_payload = _agent_run_response_from_result(session, tenant_id=tenant_id, result=result)
    return {
        "run": run_payload,
        "timeline": _editor_timeline_payload(session, tenant_id=tenant_id, content_item_id=content_item_id),
    }


@router.get("/internal/service/review-queue", include_in_schema=False)
def list_service_review_queue(
    status: str | None = None,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    rows = list_review_items(session, tenant_id=tenant_id, status=status)
    return [_review_item_public_dict(row) for row in rows]


@router.get("/internal/service/review-queue/{review_item_id}", include_in_schema=False)
def get_service_review_queue_item(
    review_item_id: str,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    row = get_review_item(session, tenant_id=tenant_id, review_item_id=review_item_id)
    return _review_item_public_dict(row)


@router.post("/internal/service/review-queue/{review_item_id}/resolve", include_in_schema=False)
def resolve_service_review_queue_item(
    review_item_id: str,
    body: ReviewResolveBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    row, materialization = resolve_review_item(
        session,
        tenant_id=tenant_id,
        review_item_id=review_item_id,
        decision=body.decision,
        decision_payload={
            "note": body.note,
            "reviewer_id": body.reviewer_id,
            "review_action": body.review_action,
            "approved_image_urls": body.approved_image_urls,
        },
    )
    follow_up_run: dict[str, Any] | None = None
    review_payload = _load_json_object(row.review_payload_json, {})
    if (
        body.decision == "approved"
        and isinstance(review_payload, dict)
        and review_payload.get("kind") == "source_package"
    ):
        selected_seed_urls = [
            str(item).strip()
            for item in body.approved_seed_urls
            if isinstance(item, str) and str(item).strip()
        ]
        approved_seed_urls = selected_seed_urls or [
            str(item).strip()
            for item in (review_payload.get("approved_seed_urls") or [])
            if isinstance(item, str) and str(item).strip()
        ]
        selected_image_urls = [
            str(item).strip()
            for item in body.approved_image_urls
            if isinstance(item, str) and str(item).strip()
        ]
        if approved_seed_urls:
            body_payload = _load_json_object(row.decision_json, {})
            if isinstance(body_payload, dict):
                body_payload["follow_up_run_id"] = "pending"
                body_payload["approved_seed_urls"] = approved_seed_urls
                body_payload["approved_image_urls"] = selected_image_urls
                row.decision_json = json.dumps(body_payload, ensure_ascii=True)
            orchestrator = AgentOrchestrator(session)
            follow_up_run = orchestrator.run_once(
                tenant_id=tenant_id,
                channel_id=str(review_payload.get("channel_id") or row.channel_id),
                mode="post_copilot",
                user_request=review_payload.get("user_request"),
                topic_definition=review_payload.get("topic_definition"),
                editorial_instructions=None,
                content_item_id=review_payload.get("content_item_id"),
                max_candidates=1,
                agent_task=None,
                autonomy_mode=review_payload.get("autonomy_mode"),
                image_request=bool(review_payload.get("image_request")),
                seed_urls=approved_seed_urls,
                approved_image_urls=selected_image_urls,
            )
            body_payload = _load_json_object(row.decision_json, {})
            if isinstance(body_payload, dict):
                body_payload["follow_up_run_id"] = follow_up_run.get("agent_run_id")
                row.decision_json = json.dumps(body_payload, ensure_ascii=True)
    log_review_item_resolved(
        row.id,
        candidate_id=row.candidate_id,
        decision=body.decision,
        tenant_id=tenant_id,
    )
    inc_agent_review_item_resolved(body.decision)
    session.commit()
    out = _review_item_public_dict(row, materialization)
    if follow_up_run is not None:
        out["follow_up_run"] = follow_up_run
    return out


@router.post("/internal/service/agent/reindex/channel/{channel_id}", include_in_schema=False)
def reindex_service_channel_embeddings(
    channel_id: str,
    body: ReindexEmbeddingsBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    if body.async_mode:
        reindex_channel_embeddings_task.delay(tenant_id, channel_id, body.limit, body.offset)
        return {"status": "queued", "tenant_id": tenant_id, "channel_id": channel_id, "limit": body.limit, "offset": body.offset}
    result = reindex_channel_content_embeddings(
        session,
        tenant_id=tenant_id,
        channel_id=channel_id,
        limit=body.limit,
        offset=body.offset,
    )
    session.commit()
    return {"status": "completed", "tenant_id": tenant_id, "channel_id": channel_id, **result}


@router.post("/internal/service/agent/reindex/channel/{channel_id}/rotate", include_in_schema=False)
def rotate_service_channel_embeddings(
    channel_id: str,
    body: ReindexEmbeddingsBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    if body.async_mode:
        from postbridge.workers.tasks import rotate_channel_embeddings_task

        rotate_channel_embeddings_task.delay(tenant_id, channel_id, body.limit, body.offset)
        return {"status": "queued", "tenant_id": tenant_id, "channel_id": channel_id, "limit": body.limit, "offset": body.offset}
    result = rotate_channel_content_embeddings(
        session,
        tenant_id=tenant_id,
        channel_id=channel_id,
        limit=body.limit,
        offset=body.offset,
    )
    session.commit()
    return {"status": "completed", "tenant_id": tenant_id, "channel_id": channel_id, **result}


@router.post("/internal/service/agent/reindex/content-items/{content_item_id}", include_in_schema=False)
def reindex_service_content_item_embedding(
    content_item_id: str,
    body: ReindexContentEmbeddingBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    if body.async_mode:
        reindex_content_item_embedding_task.delay(tenant_id, body.channel_id, content_item_id)
        return {
            "status": "queued",
            "tenant_id": tenant_id,
            "channel_id": body.channel_id,
            "content_item_id": content_item_id,
        }
    result = reindex_content_item_embedding(
        session,
        tenant_id=tenant_id,
        channel_id=body.channel_id,
        content_item_id=content_item_id,
    )
    session.commit()
    return {"status": "completed", "tenant_id": tenant_id, "channel_id": body.channel_id, **result}


@router.get("/internal/service/agent/embeddings/lifecycle", include_in_schema=False)
def get_service_agent_embeddings_lifecycle(
    channel_id: str | None = Query(default=None, min_length=36, max_length=36),
    channel_limit: int = Query(default=20, ge=1, le=200),
    channel_offset: int = Query(default=0, ge=0, le=1_000_000),
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    result = get_embedding_lifecycle_overview(
        session,
        tenant_id=tenant_id,
        channel_id=channel_id,
        limit_channels=channel_limit,
        offset_channels=channel_offset,
    )
    return {"status": "completed", **result}


@router.post("/internal/service/agent/knowledge/search", include_in_schema=False)
def search_service_agent_knowledge(
    body: KnowledgeSearchBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    result = search_content_knowledge(
        session,
        tenant_id=tenant_id,
        query=body.query,
        channel_ids=body.channel_ids,
        limit=body.limit,
        semantic_enabled=body.semantic_enabled,
    )
    return {"status": "completed", "tenant_id": tenant_id, **result}


@router.post("/internal/service/agent/reindex/drift", include_in_schema=False)
def reindex_service_embedding_drift(
    body: EmbeddingLifecycleBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    if body.async_mode:
        from postbridge.workers.tasks import reindex_embedding_drift_task

        reindex_embedding_drift_task.delay(
            tenant_id,
            body.channel_id,
            body.channel_limit,
            body.item_limit,
            body.channel_offset,
        )
        return {
            "status": "queued",
            "tenant_id": tenant_id,
            "channel_id": body.channel_id,
            "channel_limit": body.channel_limit,
            "item_limit": body.item_limit,
            "channel_offset": body.channel_offset,
        }
    result = reindex_embedding_drift(
        session,
        tenant_id=tenant_id,
        channel_id=body.channel_id,
        channel_limit=body.channel_limit,
        item_limit=body.item_limit,
        channel_offset=body.channel_offset,
    )
    session.commit()
    return {"status": "completed", **result}


@router.post("/internal/service/agent/embeddings/maintenance", include_in_schema=False)
def maintain_service_embeddings(
    body: EmbeddingMaintenanceBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    if body.async_mode:
        from postbridge.workers.tasks import maintain_embeddings_task

        maintain_embeddings_task.delay(
            tenant_id,
            body.channel_id,
            body.prune_orphans,
            body.prune_malformed,
            body.optimize_native,
            body.row_limit,
            body.offset,
            body.after_id,
        )
        return {
            "status": "queued",
            "tenant_id": tenant_id,
            "channel_id": body.channel_id,
            "row_limit": body.row_limit,
            "offset": body.offset,
            "after_id": body.after_id,
        }
    result = maintain_embeddings(
        session,
        tenant_id=tenant_id,
        channel_id=body.channel_id,
        prune_orphans=body.prune_orphans,
        prune_malformed=body.prune_malformed,
        optimize_native=body.optimize_native,
        limit=body.row_limit,
        offset=body.offset,
        after_id=body.after_id,
    )
    session.commit()
    return {"status": "completed", **result}


@router.post("/internal/service/agent/embeddings/compact", include_in_schema=False)
def compact_service_embeddings(
    body: EmbeddingCompactionBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    settings = get_settings()
    retention_days = body.candidate_retention_days or settings.agent_embedding_candidate_retention_days
    if body.async_mode:
        from postbridge.workers.tasks import compact_embeddings_task

        compact_embeddings_task.delay(tenant_id, body.channel_id, retention_days, body.optimize_native)
        return {
            "status": "queued",
            "tenant_id": tenant_id,
            "channel_id": body.channel_id,
            "candidate_retention_days": retention_days,
        }
    result = compact_embeddings(
        session,
        tenant_id=tenant_id,
        channel_id=body.channel_id,
        candidate_retention_days=retention_days,
        optimize_native=body.optimize_native,
    )
    session.commit()
    return {"status": "completed", **result}


@router.post("/internal/service/agent/cleanup", include_in_schema=False)
def cleanup_service_agent_runtime(
    body: AgentCleanupBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    settings = get_settings()
    retention_days = body.retention_days or settings.agent_cleanup_retention_days
    trace_retention_days = body.trace_retention_days or settings.agent_trace_retention_days
    review_retention_days = body.review_retention_days or settings.agent_review_retention_days
    review_body_retention_days = body.review_body_retention_days or settings.agent_review_body_retention_days
    fingerprint_retention_days = body.fingerprint_retention_days or settings.agent_fingerprint_retention_days
    if body.async_mode:
        from postbridge.workers.tasks import cleanup_agent_runtime_task

        cleanup_agent_runtime_task.delay(
            tenant_id,
            retention_days,
            trace_retention_days,
            review_retention_days,
            review_body_retention_days,
            fingerprint_retention_days,
        )
        return {
            "status": "queued",
            "tenant_id": tenant_id,
            "retention_days": retention_days,
            "trace_retention_days": trace_retention_days,
            "review_retention_days": review_retention_days,
            "review_body_retention_days": review_body_retention_days,
            "fingerprint_retention_days": fingerprint_retention_days,
        }
    result = cleanup_agent_runtime(
        session,
        tenant_id=tenant_id,
        retention_days=retention_days,
        trace_retention_days=trace_retention_days,
        review_retention_days=review_retention_days,
        review_body_retention_days=review_body_retention_days,
        fingerprint_retention_days=fingerprint_retention_days,
    )
    session.commit()
    return {"status": "completed", **result}
