from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from postbridge.agent.scheduling import next_run_at_from_cron
from postbridge.agent.policies import AutonomyPolicy, apply_policy_overrides, get_autonomy_policy, policy_to_dict
from postbridge.agent.workspace_policy import (
    AgentWorkspacePolicy,
    apply_workspace_policy_overrides,
    extract_workspace_policy_payload,
    workspace_policy_to_dict,
)
from postbridge.config import get_settings
from postbridge.agent.tools import (
    canonical_angle_family,
    canonical_source_hash,
    classify_source_type,
    review_action_from_hints,
    source_conflict_explanations,
    source_detail_items,
    source_disagreement_details,
    theme_labels_from_texts,
    upsert_content_embedding,
    upsert_content_fingerprint,
)
from postbridge.domain.errors import ValidationError
from postbridge.models.domain import (
    ChannelOrm,
    AgentRunOrm,
    AgentRunStepOrm,
    AgentTaskOrm,
    AgentPolicyOrm,
    ContentCandidateOrm,
    ContentEmbeddingOrm,
    ContentItemOrm,
    ContentSourceFingerprintOrm,
    MediaGenerationJobOrm,
    ReviewQueueItemOrm,
)
from postbridge.services.publication_planning import create_content_with_plan_and_targets
from postbridge.services.postbridge_workspace_content import SOURCE_TYPE as POSTBRIDGE_CONTENT_SOURCE_TYPE
from postbridge.services.ai_editor_chat import (
    append_agent_editor_review_resolved,
    append_agent_editor_source_package_resolved,
)

logger = logging.getLogger(__name__)


def create_agent_task(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    mode: str,
    goal_text: str,
    editorial_instructions: str | None,
    schedule_cron: str | None,
    timezone: str | None,
    max_candidates_per_run: int,
    autonomy_mode: str,
    provider_config_id: str | None,
    model_name: str | None,
    content_item_id: str | None,
    task_config: dict,
    created_by: str | None,
) -> AgentTaskOrm:
    row = AgentTaskOrm(
        id=str(uuid4()),
        tenant_id=tenant_id,
        channel_id=channel_id,
        mode=mode,
        goal_text=goal_text,
        editorial_instructions=editorial_instructions,
        schedule_cron=schedule_cron,
        timezone=timezone,
        max_candidates_per_run=max_candidates_per_run,
        autonomy_mode=autonomy_mode,
        provider_config_id=provider_config_id,
        model_name=model_name,
        content_item_id=content_item_id,
        task_config_json=json.dumps(task_config, ensure_ascii=True),
        created_by=created_by,
        status="active",
        next_run_at=next_run_at_from_cron(schedule_cron),
    )
    session.add(row)
    session.flush()
    return row


def list_agent_tasks(session: Session, *, tenant_id: str) -> list[AgentTaskOrm]:
    return list(
        session.scalars(
            select(AgentTaskOrm)
            .where(AgentTaskOrm.tenant_id == tenant_id)
            .where(AgentTaskOrm.status != "archived")
            .order_by(AgentTaskOrm.created_at.desc())
        ).all()
    )


def get_agent_policy(session: Session, *, tenant_id: str, channel_id: str | None = None) -> AgentPolicyOrm | None:
    stmt = select(AgentPolicyOrm).where(AgentPolicyOrm.tenant_id == tenant_id)
    stmt = stmt.where(AgentPolicyOrm.channel_id.is_(None) if channel_id is None else AgentPolicyOrm.channel_id == channel_id)
    return session.scalar(stmt.limit(1))


def pause_agent_task(session: Session, *, tenant_id: str, task_id: str) -> AgentTaskOrm:
    row = get_agent_task(session, tenant_id=tenant_id, task_id=task_id)
    row.status = "paused"
    row.next_run_at = None
    session.flush()
    return row


def resume_agent_task(session: Session, *, tenant_id: str, task_id: str) -> AgentTaskOrm:
    row = get_agent_task(session, tenant_id=tenant_id, task_id=task_id)
    row.status = "active"
    row.next_run_at = next_run_at_from_cron(row.schedule_cron, base=datetime.now(UTC))
    session.flush()
    return row


def archive_agent_task(session: Session, *, tenant_id: str, task_id: str) -> AgentTaskOrm:
    row = get_agent_task(session, tenant_id=tenant_id, task_id=task_id)
    row.status = "archived"
    row.next_run_at = None
    session.flush()
    return row


def list_agent_policies(session: Session, *, tenant_id: str) -> list[AgentPolicyOrm]:
    return list(
        session.scalars(
            select(AgentPolicyOrm)
            .where(AgentPolicyOrm.tenant_id == tenant_id)
            .order_by(AgentPolicyOrm.channel_id.asc().nullsfirst(), AgentPolicyOrm.created_at.desc())
        ).all()
    )


def _iter_agent_policy_sources(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
) -> list[tuple[str, AgentPolicyOrm, dict]]:
    sources: list[tuple[str, AgentPolicyOrm, dict]] = []
    for scope, row in (
        ("tenant", get_agent_policy(session, tenant_id=tenant_id, channel_id=None)),
        ("channel", get_agent_policy(session, tenant_id=tenant_id, channel_id=channel_id)),
    ):
        if row is None:
            continue
        try:
            payload = json.loads(row.policy_json)
        except json.JSONDecodeError:
            payload = {}
        sources.append((scope, row, payload if isinstance(payload, dict) else {}))
    return sources


def upsert_agent_policy(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str | None,
    policy_payload: dict,
) -> AgentPolicyOrm:
    row = get_agent_policy(session, tenant_id=tenant_id, channel_id=channel_id)
    if row is None:
        row = AgentPolicyOrm(
            id=str(uuid4()),
            tenant_id=tenant_id,
            channel_id=channel_id,
            policy_json=json.dumps(policy_payload, ensure_ascii=True),
            version=1,
        )
        session.add(row)
    else:
        row.policy_json = json.dumps(policy_payload, ensure_ascii=True)
        row.version += 1
    session.flush()
    return row


def resolve_agent_policy(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    mode: str | None,
) -> tuple[AutonomyPolicy, dict]:
    base_policy = get_autonomy_policy(mode)
    effective_policy = base_policy
    sources: list[dict] = []
    for scope, row, payload in _iter_agent_policy_sources(
        session,
        tenant_id=tenant_id,
        channel_id=channel_id,
    ):
        effective_policy = apply_policy_overrides(effective_policy, payload)
        sources.append(
            {
                "scope": scope,
                "policy_id": row.id,
                "channel_id": row.channel_id,
                "version": row.version,
                "payload": payload,
            }
        )
    return effective_policy, {
        "base_policy": policy_to_dict(base_policy),
        "effective_policy": policy_to_dict(effective_policy),
        "sources": sources,
    }


def resolve_agent_workspace_policy(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
) -> tuple[AgentWorkspacePolicy, dict]:
    effective_policy = AgentWorkspacePolicy()
    sources: list[dict[str, object]] = []
    for scope, row, payload in _iter_agent_policy_sources(
        session,
        tenant_id=tenant_id,
        channel_id=channel_id,
    ):
        effective_policy = apply_workspace_policy_overrides(effective_policy, payload)
        sources.append(
            {
                "scope": scope,
                "policy_id": row.id,
                "channel_id": row.channel_id,
                "version": row.version,
                "workspace_policy": extract_workspace_policy_payload(payload),
            }
        )
    return effective_policy, {
        "effective_workspace_policy": workspace_policy_to_dict(effective_policy),
        "sources": sources,
    }


def list_due_agent_tasks(session: Session, *, now: datetime | None = None) -> list[AgentTaskOrm]:
    current = now or datetime.now(UTC)
    return list(
        session.scalars(
            select(AgentTaskOrm)
            .where(
                AgentTaskOrm.status == "active",
                AgentTaskOrm.next_run_at.is_not(None),
                AgentTaskOrm.next_run_at <= current,
            )
            .order_by(AgentTaskOrm.next_run_at.asc())
        ).all()
    )


def get_agent_task(session: Session, *, tenant_id: str, task_id: str) -> AgentTaskOrm:
    row = session.get(AgentTaskOrm, task_id)
    if row is None or row.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_AGENT_TASK_NOT_FOUND",
            message="agent task not found",
            details={"task_id": task_id},
        )
    return row


def list_agent_runs(session: Session, *, tenant_id: str) -> list[AgentRunOrm]:
    return list(
        session.scalars(
            select(AgentRunOrm)
            .where(AgentRunOrm.tenant_id == tenant_id)
            .order_by(AgentRunOrm.created_at.desc())
        ).all()
    )


def list_agent_run_steps(session: Session, *, tenant_id: str, run_id: str) -> list[AgentRunStepOrm]:
    get_agent_run(session, tenant_id=tenant_id, run_id=run_id)
    return list(
        session.scalars(
            select(AgentRunStepOrm)
            .where(
                AgentRunStepOrm.tenant_id == tenant_id,
                AgentRunStepOrm.agent_run_id == run_id,
            )
            .order_by(AgentRunStepOrm.created_at.asc())
        ).all()
    )


def get_agent_run(session: Session, *, tenant_id: str, run_id: str) -> AgentRunOrm:
    row = session.get(AgentRunOrm, run_id)
    if row is None or row.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_AGENT_RUN_NOT_FOUND",
            message="agent run not found",
            details={"run_id": run_id},
        )
    return row


def list_content_candidates(session: Session, *, tenant_id: str, run_id: str | None = None) -> list[ContentCandidateOrm]:
    stmt = select(ContentCandidateOrm).where(ContentCandidateOrm.tenant_id == tenant_id)
    if run_id:
        stmt = stmt.where(ContentCandidateOrm.agent_run_id == run_id)
    stmt = stmt.order_by(ContentCandidateOrm.created_at.desc())
    return list(session.scalars(stmt).all())


def get_content_candidate(session: Session, *, tenant_id: str, candidate_id: str) -> ContentCandidateOrm:
    row = session.get(ContentCandidateOrm, candidate_id)
    if row is None or row.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_CONTENT_CANDIDATE_NOT_FOUND",
            message="content candidate not found",
            details={"candidate_id": candidate_id},
        )
    return row


def create_agent_run(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    graph_name: str,
    trigger_type: str,
    user_request: str | None,
    topic_definition: str | None,
    agent_task_id: str | None,
    content_item_id: str | None,
    model: str | None,
    provider_type: str | None,
) -> AgentRunOrm:
    row = AgentRunOrm(
        id=str(uuid4()),
        tenant_id=tenant_id,
        channel_id=channel_id,
        graph_name=graph_name,
        trigger_type=trigger_type,
        status="queued",
        user_request=user_request,
        topic_definition=topic_definition,
        agent_task_id=agent_task_id,
        content_item_id=content_item_id,
        model=model,
        provider_type=provider_type,
    )
    session.add(row)
    session.flush()
    return row


def mark_run_started(session: Session, run: AgentRunOrm) -> None:
    run.status = "running"
    run.started_at = datetime.now(UTC)
    session.flush()


def mark_run_completed(
    session: Session,
    run: AgentRunOrm,
    *,
    trace: list[dict],
    result: dict,
    review_created: bool,
    token_usage: dict | None = None,
) -> None:
    run.status = "awaiting_review" if review_created else "completed"
    run.completed_at = datetime.now(UTC)
    trace_policy = get_settings().agent_trace_policy
    run.trace_json = json.dumps(
        {
            "trace_policy": trace_policy,
            "trace": _serialize_run_trace(trace, policy=trace_policy),
            "tool_summary": summarize_tool_trace(trace),
            "result": _summarize_run_result(result),
        },
        ensure_ascii=True,
    )
    run.token_usage_json = json.dumps(token_usage or {}, ensure_ascii=True)
    if run.agent_task_id:
        task = session.get(AgentTaskOrm, run.agent_task_id)
        if task is not None:
            task.last_run_at = run.completed_at
            task.next_run_at = next_run_at_from_cron(task.schedule_cron, base=run.completed_at)
    session.flush()


def mark_run_failed(session: Session, run: AgentRunOrm, *, error_code: str, error_message: str) -> None:
    run.status = "failed"
    run.completed_at = datetime.now(UTC)
    run.error_code = error_code
    run.error_message = error_message
    session.flush()


def append_run_step(
    session: Session,
    *,
    agent_run_id: str,
    tenant_id: str,
    step_name: str,
    status: str,
    input_payload: dict | None = None,
    output_payload: dict | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> AgentRunStepOrm:
    now = datetime.now(UTC)
    started = started_at or now
    completed = completed_at or now
    row = AgentRunStepOrm(
        id=str(uuid4()),
        agent_run_id=agent_run_id,
        tenant_id=tenant_id,
        step_name=step_name,
        status=status,
        input_json=json.dumps(input_payload or {}, ensure_ascii=True),
        output_json=json.dumps(output_payload or {}, ensure_ascii=True),
        started_at=started,
        completed_at=completed,
    )
    session.add(row)
    session.flush()
    return row


def summarize_tool_trace(trace: list[dict] | None) -> dict[str, Any]:
    items = [item for item in (trace or []) if isinstance(item, dict)]
    tool_names = [str(item.get("tool")) for item in items if isinstance(item.get("tool"), str)]
    usage_totals = {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}
    for item in items:
        usage = item.get("usage")
        if not isinstance(usage, dict):
            continue
        for key in usage_totals:
            raw = usage.get(key)
            if isinstance(raw, int):
                usage_totals[key] += raw
    top_tools = [
        {"tool": tool, "count": count}
        for tool, count in Counter(tool_names).most_common(10)
    ]
    return {
        "tool_call_count": len(tool_names),
        "unique_tool_count": len(set(tool_names)),
        "top_tools": top_tools,
        "usage_totals": usage_totals,
    }


def _serialize_run_trace(trace: list[dict] | None, *, policy: str) -> list[dict]:
    if policy == "none":
        return []
    items = [item for item in (trace or []) if isinstance(item, dict)]
    max_entries = get_settings().agent_trace_max_entries
    sliced = items[:max_entries]
    if policy == "full":
        return sliced
    summarized: list[dict] = []
    for item in sliced:
        summary = {
            "tool": item.get("tool"),
            "usage": item.get("usage") if isinstance(item.get("usage"), dict) else {},
        }
        if "summary" in item and isinstance(item.get("summary"), dict):
            summary["summary"] = item.get("summary")
        for key in ("skipped", "empty", "error"):
            if key in item:
                summary[key] = item.get(key)
        summarized.append(summary)
    return summarized


def _summarize_run_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "agent_run_id": result.get("agent_run_id"),
        "mode": result.get("mode"),
        "status": result.get("status"),
        "autonomy_mode": result.get("autonomy_mode"),
        "candidate_count": len(result.get("candidates") or []),
        "review_count": len(result.get("review_items") or []),
        "source_package_review_count": len(result.get("source_package_review_items") or []),
        "auto_materialized_count": len(result.get("auto_materialized") or []),
        "guardrail_block_count": len(result.get("guardrail_blocks") or []),
    }


def save_candidate(
    session: Session,
    *,
    agent_run_id: str,
    tenant_id: str,
    channel_id: str,
    content_item_id: str | None,
    candidate: dict,
) -> ContentCandidateOrm:
    sanitized_body_markdown = _sanitize_topic_scout_body_markdown(candidate.get("body_markdown"))
    sanitized_candidate = dict(candidate)
    sanitized_candidate["body_markdown"] = sanitized_body_markdown
    row = ContentCandidateOrm(
        id=str(uuid4()),
        agent_run_id=agent_run_id,
        tenant_id=tenant_id,
        channel_id=channel_id,
        content_item_id=content_item_id,
        status="proposed",
        topic=sanitized_candidate.get("topic"),
        summary=sanitized_candidate.get("summary"),
        headline=sanitized_candidate.get("headline"),
        body_markdown=sanitized_body_markdown,
        why_now=sanitized_candidate.get("why_now"),
        source_bundle_json=json.dumps(sanitized_candidate.get("source_bundle") or {}, ensure_ascii=True),
        scores_json=json.dumps(sanitized_candidate.get("scores") or {}, ensure_ascii=True),
        risk_flags_json=json.dumps(sanitized_candidate.get("risk_flags") or [], ensure_ascii=True),
        dedup_summary=sanitized_candidate.get("dedup_summary"),
        style_fit_summary=sanitized_candidate.get("style_fit_summary"),
        draft_json=json.dumps(sanitized_candidate, ensure_ascii=True),
    )
    session.add(row)
    session.flush()
    return row


def create_review_queue_item(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    agent_run_id: str,
    candidate_id: str,
    review_payload: dict,
) -> ReviewQueueItemOrm:
    row = ReviewQueueItemOrm(
        id=str(uuid4()),
        tenant_id=tenant_id,
        channel_id=channel_id,
        agent_run_id=agent_run_id,
        candidate_id=candidate_id,
        status="pending",
        review_payload_json=json.dumps(review_payload, ensure_ascii=True),
    )
    session.add(row)
    session.flush()
    return row


def list_review_items(session: Session, *, tenant_id: str, status: str | None = None) -> list[ReviewQueueItemOrm]:
    stmt = select(ReviewQueueItemOrm).where(ReviewQueueItemOrm.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(ReviewQueueItemOrm.status == status)
    stmt = stmt.order_by(ReviewQueueItemOrm.created_at.desc())
    return list(session.scalars(stmt).all())


def get_review_item(session: Session, *, tenant_id: str, review_item_id: str) -> ReviewQueueItemOrm:
    row = session.get(ReviewQueueItemOrm, review_item_id)
    if row is None or row.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_REVIEW_ITEM_NOT_FOUND",
            message="review queue item not found",
            details={"review_item_id": review_item_id},
        )
    return row


def resolve_review_item(
    session: Session,
    *,
    tenant_id: str,
    review_item_id: str,
    decision: str,
    decision_payload: dict,
) -> tuple[ReviewQueueItemOrm, dict]:
    row = session.get(ReviewQueueItemOrm, review_item_id)
    if row is None or row.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_REVIEW_ITEM_NOT_FOUND",
            message="review queue item not found",
            details={"review_item_id": review_item_id},
        )
    if decision not in {"approved", "rejected"}:
        raise ValidationError(
            code="VALIDATION_REVIEW_DECISION_INVALID",
            message="decision must be approved or rejected",
            details={"decision": decision},
        )
    materialization: dict = {}
    row.status = decision
    row.decision_json = json.dumps(decision_payload, ensure_ascii=True)
    row.resolved_at = datetime.now(UTC)
    review_payload = _load_json(row.review_payload_json)
    review_hints = review_payload.get("review_hints") if isinstance(review_payload, dict) else []
    if not isinstance(decision_payload, dict):
        decision_payload = {}
    if not decision_payload.get("review_action"):
        decision_payload["review_action"] = review_action_from_hints(
            decision=decision,
            review_hints=review_hints if isinstance(review_hints, list) else [],
        )
    policy = get_autonomy_policy(review_payload.get("autonomy_mode"))
    candidate = session.get(ContentCandidateOrm, row.candidate_id)
    review_kind = review_payload.get("kind") if isinstance(review_payload, dict) else None
    if candidate is not None:
        candidate.status = "approved" if decision == "approved" else "rejected"
        if review_kind == "source_package":
            append_run_step(
                session,
                agent_run_id=row.agent_run_id,
                tenant_id=tenant_id,
                step_name="source_package_review_resolved",
                status="ok",
                input_payload={"review_item_id": row.id, "candidate_id": row.candidate_id},
                output_payload={
                    "decision": decision,
                    "candidate_status": candidate.status,
                },
            )
            if candidate.content_item_id:
                append_agent_editor_source_package_resolved(
                    session,
                    tenant_id=tenant_id,
                    content_item_id=candidate.content_item_id,
                    agent_run_id=row.agent_run_id,
                    decision=decision,
                    reviewer_id=decision_payload.get("reviewer_id") if isinstance(decision_payload, dict) else None,
                    note=decision_payload.get("note") if isinstance(decision_payload, dict) else None,
                    follow_up_run_id=(
                        decision_payload.get("follow_up_run_id") if isinstance(decision_payload, dict) else None
                    ),
                )
            session.flush()
            return row, materialization
        if decision == "approved":
            _apply_approved_images_to_candidate(candidate, decision_payload.get("approved_image_urls"))
        effective_materialization_policy = policy
        if (
            decision == "approved"
            and policy.mode == "guarded_auto_publish"
            and isinstance(review_payload, dict)
            and bool(review_payload.get("guardrail_blocked"))
        ):
            effective_materialization_policy = apply_policy_overrides(
                policy,
                {
                    "materialize_on_approval": True,
                    "materialization_level": "draft_only",
                    "auto_dispatch": False,
                },
            )
        if decision == "approved" and effective_materialization_policy.materialize_on_approval:
            materialization = materialize_candidate_on_approval(
                session,
                tenant_id=tenant_id,
                candidate=candidate,
                policy=effective_materialization_policy,
                requester_user_id=(
                    decision_payload.get("reviewer_id")
                    if isinstance(decision_payload, dict) and isinstance(decision_payload.get("reviewer_id"), str)
                    else None
                ),
            )
        append_run_step(
            session,
            agent_run_id=row.agent_run_id,
            tenant_id=tenant_id,
            step_name="review_resolved",
            status="ok",
            input_payload={"review_item_id": row.id, "candidate_id": row.candidate_id},
            output_payload={
                "decision": decision,
                "candidate_status": candidate.status,
                "materialization": materialization.get("materialization"),
            },
        )
        if candidate.content_item_id:
            append_agent_editor_review_resolved(
                session,
                tenant_id=tenant_id,
                content_item_id=candidate.content_item_id,
                agent_run_id=row.agent_run_id,
                decision=decision,
                reviewer_id=decision_payload.get("reviewer_id") if isinstance(decision_payload, dict) else None,
                review_action=decision_payload.get("review_action") if isinstance(decision_payload, dict) else None,
                note=decision_payload.get("note") if isinstance(decision_payload, dict) else None,
                materialization=materialization if isinstance(materialization, dict) else None,
            )
    session.flush()
    return row, materialization


def _apply_approved_images_to_candidate(candidate: ContentCandidateOrm, approved_image_urls: Any) -> None:
    if not isinstance(approved_image_urls, list):
        return
    selected_urls: list[str] = []
    seen: set[str] = set()
    for item in approved_image_urls:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        selected_urls.append(value)
    if not selected_urls:
        return
    source_bundle = _load_json(candidate.source_bundle_json)
    image_candidates = source_bundle.get("image_candidates") if isinstance(source_bundle, dict) else []
    allowed_urls = {
        str(item.get("url")).strip()
        for item in image_candidates
        if isinstance(item, dict) and isinstance(item.get("url"), str) and str(item.get("url")).strip()
    }
    filtered_urls = [item for item in selected_urls if item in allowed_urls]
    if not filtered_urls:
        return
    draft_payload = _load_json(candidate.draft_json)
    draft_payload["media_url"] = filtered_urls[0]
    draft_payload["media_urls"] = filtered_urls
    draft_payload["cover_image_url"] = filtered_urls[0]
    candidate.draft_json = json.dumps(draft_payload, ensure_ascii=True)


def materialize_candidate_on_approval(
    session: Session,
    *,
    tenant_id: str,
    candidate: ContentCandidateOrm,
    policy: AutonomyPolicy | None = None,
    requester_user_id: str | None = None,
) -> dict:
    resolved_policy = policy or get_autonomy_policy("plan_approval")
    editorial_channel = session.get(ChannelOrm, candidate.channel_id)
    draft_only_editorial_context = bool(
        editorial_channel is not None and editorial_channel.platform == "postbridge"
    )
    draft_payload = _load_json(candidate.draft_json)
    media_url = str(draft_payload.get("media_url") or "").strip() or None
    raw_media_urls = draft_payload.get("media_urls")
    media_urls = (
        [str(item).strip() for item in raw_media_urls if isinstance(item, str) and str(item).strip()]
        if isinstance(raw_media_urls, list)
        else None
    )
    if media_url and (not media_urls or media_url not in media_urls):
        media_urls = [media_url] + [item for item in (media_urls or []) if item != media_url]
    cover_image_url = str(draft_payload.get("cover_image_url") or "").strip() or media_url
    if candidate.content_item_id:
        existing = session.get(ContentItemOrm, candidate.content_item_id)
        if existing is not None and existing.tenant_id == tenant_id:
            existing.title = candidate.headline
            existing.body_markdown = candidate.body_markdown
            if media_url:
                existing.media_url = media_url
            if media_urls:
                existing.media_urls = media_urls
            if cover_image_url:
                structured = _load_json(existing.body_structured_json)
                structured["cover_image_url"] = cover_image_url
                existing.body_structured_json = json.dumps(structured, ensure_ascii=True)
            existing.updated_at = datetime.now(UTC)
            session.flush()
            candidate.status = "converted"
            result = {
                "content_item_id": existing.id,
                "publication_plan_id": None,
                "publication_target_ids": [],
                "materialization": "updated_existing_content_item",
            }
            result.update(
                _maybe_queue_generated_media_job(
                    session,
                    tenant_id=tenant_id,
                    content_item_id=existing.id,
                    candidate=candidate,
                    draft_payload=draft_payload,
                    requester_user_id=requester_user_id,
                )
            )
            return result

    if resolved_policy.materialization_level == "draft_only" or draft_only_editorial_context:
        source_type = (
            POSTBRIDGE_CONTENT_SOURCE_TYPE
            if draft_only_editorial_context
            else "agent_candidate"
        )
        content = ContentItemOrm(
            id=str(uuid4()),
            tenant_id=tenant_id,
            author_user_id=None,
            source_type=source_type,
            title=candidate.headline,
            body_markdown=candidate.body_markdown,
            body_structured_json=candidate.draft_json,
            media_url=media_url,
            media_urls=media_urls,
            language=None,
            status="draft",
        )
        session.add(content)
        session.flush()
        candidate.content_item_id = content.id
        candidate.status = "converted"
        _persist_candidate_fingerprints(
            session,
            tenant_id=tenant_id,
            channel_id=candidate.channel_id,
            content_item_id=content.id,
            candidate=candidate,
        )
        result = {
            "content_item_id": content.id,
            "publication_plan_id": None,
            "publication_target_ids": [],
            "materialization": "created_editorial_draft_content_item"
            if draft_only_editorial_context
            else "created_draft_content_item",
        }
        result.update(
            _maybe_queue_generated_media_job(
                session,
                tenant_id=tenant_id,
                content_item_id=content.id,
                candidate=candidate,
                draft_payload=draft_payload,
                requester_user_id=requester_user_id,
            )
        )
        return result

    chain = create_content_with_plan_and_targets(
        session,
        tenant_id=tenant_id,
        channel_ids=[candidate.channel_id],
        source_type="agent_candidate",
        title=candidate.headline,
        body_markdown=candidate.body_markdown,
        body_structured_json=candidate.draft_json,
        content_status="draft",
        media_url=media_url,
        media_urls=media_urls,
        plan_strategy="immediate",
        plan_status="draft",
        target_status="pending",
    )
    candidate.content_item_id = chain.content_item_id
    candidate.status = "converted"
    _persist_candidate_fingerprints(
        session,
        tenant_id=tenant_id,
        channel_id=candidate.channel_id,
        content_item_id=chain.content_item_id,
        candidate=candidate,
    )
    if resolved_policy.auto_dispatch:
        from postbridge.workers.tasks import process_publication_target_task

        for target_id in chain.publication_target_ids:
            process_publication_target_task.delay(target_id, None)
    session.flush()
    result = {
        "content_item_id": chain.content_item_id,
        "publication_plan_id": chain.publication_plan_id,
        "publication_target_ids": chain.publication_target_ids,
        "materialization": "created_content_plan_and_targets"
        if not resolved_policy.auto_dispatch
        else "created_and_dispatched_content_plan_and_targets",
    }
    result.update(
        _maybe_queue_generated_media_job(
            session,
            tenant_id=tenant_id,
            content_item_id=chain.content_item_id,
            candidate=candidate,
            draft_payload=draft_payload,
            requester_user_id=requester_user_id,
        )
    )
    return result


def _maybe_queue_generated_media_job(
    session: Session,
    *,
    tenant_id: str,
    content_item_id: str,
    candidate: ContentCandidateOrm,
    draft_payload: dict,
    requester_user_id: str | None,
) -> dict[str, str]:
    request = draft_payload.get("image_generation_request")
    risk_flags = _load_json_list(candidate.risk_flags_json)
    if not isinstance(request, dict) and "image_generation_requested" not in risk_flags:
        return {}
    payload = {
        "target": "media",
        "title": candidate.headline,
        "summary": candidate.summary,
        "content_md": candidate.body_markdown,
        "content_item_id": content_item_id,
    }
    if requester_user_id:
        payload["requester_user_id"] = requester_user_id
    job = MediaGenerationJobOrm(
        id=str(uuid4()),
        tenant_id=tenant_id,
        requester_user_id=requester_user_id,
        content_item_id=content_item_id,
        target="media",
        status="pending",
        request_payload=payload,
        correlation_id=f"agent-candidate:{candidate.id}",
    )
    session.add(job)
    session.flush()
    try:
        from postbridge.workers.media_generation_tasks import process_media_generation_job_task

        process_media_generation_job_task.delay(job.id, job.correlation_id)
    except Exception as exc:
        job.status = "failed"
        job.error_code = "MEDIA_GENERATION_QUEUE_FAILED"
        job.error_message = "media generation queue is unavailable"
        job.error_payload = {"exception": str(exc)}
        job.completed_at = datetime.now(UTC)
        session.add(job)
        logger.warning("Media generation job queue failed for candidate %s: %s", candidate.id, exc)
    return {"media_generation_job_id": job.id}


def _load_json(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


_TOPIC_SCOUT_META_HEADINGS = {
    "illustration",
    "image",
    "image guidance",
    "visual guidance",
    "post format",
    "format",
    "notes to editor",
    "editor note",
    "editor notes",
    "иллюстрация",
    "картинка",
    "изображение",
    "формат поста",
    "формат",
    "заметка редактору",
    "заметки редактору",
    "примечание редактору",
    "примечания редактору",
}


def _normalize_topic_scout_heading(value: str) -> str:
    lowered = value.strip().lower().rstrip(":")
    return re.sub(r"\s+", " ", lowered)


def _sanitize_topic_scout_body_markdown(body_markdown: str | None) -> str | None:
    if not isinstance(body_markdown, str) or not body_markdown.strip():
        return body_markdown
    lines = body_markdown.splitlines()
    sanitized: list[str] = []
    skip_section = False
    for line in lines:
        heading_match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if heading_match:
            heading = _normalize_topic_scout_heading(heading_match.group(1))
            skip_section = heading in _TOPIC_SCOUT_META_HEADINGS
            if skip_section:
                continue
        if skip_section:
            continue
        sanitized.append(line)
    cleaned = "\n".join(sanitized).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned or body_markdown.strip()


def _load_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _persist_candidate_fingerprints(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    content_item_id: str,
    candidate: ContentCandidateOrm,
) -> None:
    source_bundle = _load_json(candidate.source_bundle_json)
    unique_sources: dict[str, dict[str, str | None]] = {}
    seed_sources = source_bundle.get("seed_sources") if isinstance(source_bundle, dict) else None
    if isinstance(seed_sources, list):
        for item in seed_sources:
            if isinstance(item, dict):
                source_hash = canonical_source_hash(
                    source_url=item.get("url"),
                    title=item.get("title") or candidate.headline,
                    body_markdown=item.get("text_excerpt") or candidate.body_markdown,
                )
                if not source_hash or source_hash in unique_sources:
                    continue
                unique_sources[source_hash] = {
                    "source_url": item.get("url"),
                    "title": item.get("title") or candidate.headline,
                    "body_markdown": item.get("text_excerpt") or candidate.body_markdown,
                }
    primary_sources = source_bundle.get("primary_sources") if isinstance(source_bundle, dict) else None
    if isinstance(primary_sources, list):
        for item in primary_sources:
            if isinstance(item, str):
                source_hash = canonical_source_hash(
                    source_url=item,
                    title=candidate.headline,
                    body_markdown=candidate.body_markdown,
                )
                if not source_hash or source_hash in unique_sources:
                    continue
                unique_sources[source_hash] = {
                    "source_url": item,
                    "title": candidate.headline,
                    "body_markdown": candidate.body_markdown,
                }
    for payload in unique_sources.values():
        upsert_content_fingerprint(
            session,
            tenant_id=tenant_id,
            channel_id=channel_id,
            source_url=payload["source_url"],
            title=payload["title"],
            body_markdown=payload["body_markdown"],
            published_content_item_id=content_item_id,
            candidate_id=candidate.id,
        )
    candidate_embedding = session.scalar(
        select(ContentEmbeddingOrm).where(
            ContentEmbeddingOrm.tenant_id == tenant_id,
            ContentEmbeddingOrm.entity_type == "candidate",
            ContentEmbeddingOrm.entity_id == candidate.id,
        )
    )
    if candidate_embedding is not None:
        try:
            vector = json.loads(candidate_embedding.vector_json)
        except json.JSONDecodeError:
            vector = []
        if isinstance(vector, list):
            upsert_content_embedding(
                session,
                tenant_id=tenant_id,
                channel_id=channel_id,
                entity_type="content_item",
                entity_id=content_item_id,
                model_name=candidate_embedding.model_name,
                vector=[float(x) for x in vector if isinstance(x, (int, float))],
                text_hash=candidate_embedding.text_hash,
            )


def get_agent_overview_analytics(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str | None = None,
) -> dict:
    runs_stmt = select(AgentRunOrm).where(AgentRunOrm.tenant_id == tenant_id)
    candidates_stmt = select(ContentCandidateOrm).where(ContentCandidateOrm.tenant_id == tenant_id)
    reviews_stmt = select(ReviewQueueItemOrm).where(ReviewQueueItemOrm.tenant_id == tenant_id)
    if channel_id:
        runs_stmt = runs_stmt.where(AgentRunOrm.channel_id == channel_id)
        candidates_stmt = candidates_stmt.where(ContentCandidateOrm.channel_id == channel_id)
        reviews_stmt = reviews_stmt.where(ReviewQueueItemOrm.channel_id == channel_id)

    runs = list(session.scalars(runs_stmt).all())
    candidates = list(session.scalars(candidates_stmt).all())
    reviews = list(session.scalars(reviews_stmt).all())

    run_statuses = Counter(row.status for row in runs)
    run_modes = Counter(row.graph_name for row in runs)
    candidate_statuses = Counter(row.status for row in candidates)
    review_statuses = Counter(row.status for row in reviews)

    resolved_reviews = [row for row in reviews if row.status in {"approved", "rejected"} and row.resolved_at and row.created_at]
    resolution_seconds = [
        max((row.resolved_at - row.created_at).total_seconds(), 0.0)
        for row in resolved_reviews
    ]

    return {
        "tenant_id": tenant_id,
        "channel_id": channel_id,
        "runs_total": len(runs),
        "runs_by_status": dict(run_statuses),
        "runs_by_mode": dict(run_modes),
        "candidates_total": len(candidates),
        "candidates_by_status": dict(candidate_statuses),
        "converted_candidates": candidate_statuses.get("converted", 0),
        "candidate_conversion_rate": (
            round(candidate_statuses.get("converted", 0) / len(candidates), 4) if candidates else 0.0
        ),
        "review_items_total": len(reviews),
        "review_items_by_status": dict(review_statuses),
        "review_items_pending": review_statuses.get("pending", 0),
        "review_approved_total": review_statuses.get("approved", 0),
        "review_rejected_total": review_statuses.get("rejected", 0),
        "avg_review_resolution_seconds": (
            round(sum(resolution_seconds) / len(resolution_seconds), 3) if resolution_seconds else None
        ),
    }


def get_agent_timeseries_analytics(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str | None = None,
    days: int = 7,
) -> dict:
    window_days = max(1, min(days, 90))
    now = datetime.now(UTC)
    start = now - timedelta(days=window_days - 1)
    start_floor = start.replace(hour=0, minute=0, second=0, microsecond=0)

    runs_stmt = select(AgentRunOrm).where(
        AgentRunOrm.tenant_id == tenant_id,
        AgentRunOrm.created_at >= start_floor,
    )
    reviews_stmt = select(ReviewQueueItemOrm).where(
        ReviewQueueItemOrm.tenant_id == tenant_id,
        ReviewQueueItemOrm.created_at >= start_floor,
    )
    if channel_id:
        runs_stmt = runs_stmt.where(AgentRunOrm.channel_id == channel_id)
        reviews_stmt = reviews_stmt.where(ReviewQueueItemOrm.channel_id == channel_id)

    runs = list(session.scalars(runs_stmt).all())
    reviews = list(session.scalars(reviews_stmt).all())
    runs_by_day: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "completed": 0, "failed": 0, "awaiting_review": 0})
    reviews_by_day: dict[str, dict[str, int]] = defaultdict(lambda: {"created": 0, "approved": 0, "rejected": 0, "pending": 0})

    for row in runs:
        day_key = row.created_at.astimezone(UTC).date().isoformat()
        bucket = runs_by_day[day_key]
        bucket["total"] += 1
        bucket[row.status] = bucket.get(row.status, 0) + 1

    for row in reviews:
        day_key = row.created_at.astimezone(UTC).date().isoformat()
        created_bucket = reviews_by_day[day_key]
        created_bucket["created"] += 1
        if row.status in {"approved", "rejected", "pending"}:
            created_bucket[row.status] += 1

    series: list[dict] = []
    for offset in range(window_days):
        day = (start_floor + timedelta(days=offset)).date().isoformat()
        series.append(
            {
                "date": day,
                "runs": runs_by_day.get(day, {"total": 0, "completed": 0, "failed": 0, "awaiting_review": 0}),
                "reviews": reviews_by_day.get(day, {"created": 0, "approved": 0, "rejected": 0, "pending": 0}),
            }
        )
    return {
        "tenant_id": tenant_id,
        "channel_id": channel_id,
        "days": window_days,
        "series": series,
    }


def get_agent_quality_analytics(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str | None = None,
    days: int | None = None,
) -> dict:
    cutoff = None
    if days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=max(days, 1))
    runs_stmt = select(AgentRunOrm).where(AgentRunOrm.tenant_id == tenant_id)
    candidates_stmt = select(ContentCandidateOrm).where(ContentCandidateOrm.tenant_id == tenant_id)
    if channel_id:
        runs_stmt = runs_stmt.where(AgentRunOrm.channel_id == channel_id)
        candidates_stmt = candidates_stmt.where(ContentCandidateOrm.channel_id == channel_id)
    if cutoff is not None:
        runs_stmt = runs_stmt.where(AgentRunOrm.created_at >= cutoff)
        candidates_stmt = candidates_stmt.where(ContentCandidateOrm.created_at >= cutoff)

    runs = list(session.scalars(runs_stmt).all())
    candidates = list(session.scalars(candidates_stmt).all())
    runs_by_id = {row.id: row for row in runs}
    candidate_domains: list[set[str]] = []
    candidate_source_details: list[list[dict[str, Any]]] = []

    source_stats: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {
            "candidate_count": 0,
            "approved_count": 0,
            "converted_count": 0,
            "rejected_count": 0,
            "duplicate_count": 0,
            "risky_count": 0,
        }
    )
    domain_occurrences: Counter[str] = Counter()
    model_stats: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {
            "run_count": 0,
            "candidate_count": 0,
            "awaiting_review_runs": 0,
            "completed_runs": 0,
            "failed_runs": 0,
            "approved_count": 0,
            "converted_count": 0,
            "rejected_count": 0,
        }
    )
    policy_stats: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {
            "run_count": 0,
            "candidate_count": 0,
            "awaiting_review_runs": 0,
            "completed_runs": 0,
            "failed_runs": 0,
            "approved_count": 0,
            "converted_count": 0,
            "rejected_count": 0,
        }
    )

    for run in runs:
        label = run.model or "unknown"
        bucket = model_stats[label]
        bucket["run_count"] += 1
        if run.status == "awaiting_review":
            bucket["awaiting_review_runs"] += 1
        elif run.status == "completed":
            bucket["completed_runs"] += 1
        elif run.status == "failed":
            bucket["failed_runs"] += 1
        trace = _load_json(run.trace_json)
        policy_label = "unknown"
        if trace:
            result = trace.get("result")
            if isinstance(result, dict) and isinstance(result.get("autonomy_mode"), str):
                policy_label = result["autonomy_mode"]
        policy_bucket = policy_stats[policy_label]
        policy_bucket["run_count"] += 1
        if run.status == "awaiting_review":
            policy_bucket["awaiting_review_runs"] += 1
        elif run.status == "completed":
            policy_bucket["completed_runs"] += 1
        elif run.status == "failed":
            policy_bucket["failed_runs"] += 1

    for candidate in candidates:
        source_bundle = _load_json(candidate.source_bundle_json)
        source_urls: set[str] = set()
        if isinstance(source_bundle, dict):
            primary_sources = source_bundle.get("primary_sources")
            if isinstance(primary_sources, list):
                source_urls.update(item for item in primary_sources if isinstance(item, str))
            seed_sources = source_bundle.get("seed_sources")
            if isinstance(seed_sources, list):
                source_urls.update(
                    item.get("url") for item in seed_sources if isinstance(item, dict) and isinstance(item.get("url"), str)
                )
            primary_details = source_bundle.get("primary_sources_details")
            if isinstance(primary_details, list):
                candidate_source_details.append([item for item in primary_details if isinstance(item, dict)])
            else:
                candidate_source_details.append([])
        else:
            candidate_source_details.append([])

        domains = {
            urlparse(url).netloc.lower() or "unknown"
            for url in source_urls
            if isinstance(url, str) and url.strip()
        } or {"unknown"}
        candidate_domains.append(domains)

        for domain in domains:
            bucket = source_stats[domain]
            bucket["candidate_count"] += 1
            domain_occurrences[domain] += 1
            if candidate.status == "approved":
                bucket["approved_count"] += 1
            elif candidate.status == "converted":
                bucket["converted_count"] += 1
            elif candidate.status == "rejected":
                bucket["rejected_count"] += 1
            risk_flags = _load_json_list(candidate.risk_flags_json)
            if "possible_duplicate" in risk_flags or "embedding_duplicate" in risk_flags:
                bucket["duplicate_count"] += 1
            if risk_flags:
                bucket["risky_count"] += 1

        model_label = (runs_by_id.get(candidate.agent_run_id).model if runs_by_id.get(candidate.agent_run_id) else None) or "unknown"
        model_bucket = model_stats[model_label]
        model_bucket["candidate_count"] += 1
        if candidate.status == "approved":
            model_bucket["approved_count"] += 1
        elif candidate.status == "converted":
            model_bucket["converted_count"] += 1
        elif candidate.status == "rejected":
            model_bucket["rejected_count"] += 1
        policy_label = "unknown"
        run = runs_by_id.get(candidate.agent_run_id)
        if run is not None:
            trace = _load_json(run.trace_json)
            if trace:
                result = trace.get("result")
                if isinstance(result, dict) and isinstance(result.get("autonomy_mode"), str):
                    policy_label = result["autonomy_mode"]
        policy_bucket = policy_stats[policy_label]
        policy_bucket["candidate_count"] += 1
        if candidate.status == "approved":
            policy_bucket["approved_count"] += 1
        elif candidate.status == "converted":
            policy_bucket["converted_count"] += 1
        elif candidate.status == "rejected":
            policy_bucket["rejected_count"] += 1

    source_rows = [
        {
            "domain": domain,
            **stats,
            "conversion_rate": round(stats["converted_count"] / stats["candidate_count"], 4)
            if stats["candidate_count"]
            else 0.0,
            "approval_rate": round((stats["approved_count"] + stats["converted_count"]) / stats["candidate_count"], 4)
            if stats["candidate_count"]
            else 0.0,
            "duplicate_rate": round(stats["duplicate_count"] / stats["candidate_count"], 4)
            if stats["candidate_count"]
            else 0.0,
            "risk_rate": round(stats["risky_count"] / stats["candidate_count"], 4)
            if stats["candidate_count"]
            else 0.0,
            "trust_label": _source_trust_label(stats),
            "repeated_domain_pressure": round(domain_occurrences[domain] / len(candidates), 4) if candidates else 0.0,
        }
        for domain, stats in sorted(source_stats.items(), key=lambda item: (-int(item[1]["candidate_count"]), item[0]))
    ]
    model_rows = [
        {
            "model": model,
            **stats,
            "conversion_rate": round(stats["converted_count"] / stats["candidate_count"], 4)
            if stats["candidate_count"]
            else 0.0,
            "approval_rate": round((stats["approved_count"] + stats["converted_count"]) / stats["candidate_count"], 4)
            if stats["candidate_count"]
            else 0.0,
        }
        for model, stats in sorted(model_stats.items(), key=lambda item: (-int(item[1]["candidate_count"]), item[0]))
    ]
    policy_rows = [
        {
            "policy": policy,
            **stats,
            "conversion_rate": round(stats["converted_count"] / stats["candidate_count"], 4)
            if stats["candidate_count"]
            else 0.0,
            "approval_rate": round((stats["approved_count"] + stats["converted_count"]) / stats["candidate_count"], 4)
            if stats["candidate_count"]
            else 0.0,
        }
        for policy, stats in sorted(policy_stats.items(), key=lambda item: (-int(item[1]["candidate_count"]), item[0]))
    ]
    channel_policy_stats: dict[tuple[str, str], dict[str, float | int]] = defaultdict(
        lambda: {
            "run_count": 0,
            "candidate_count": 0,
            "converted_count": 0,
            "rejected_count": 0,
            "approval_rate": 0.0,
            "conversion_rate": 0.0,
            }
        )
    channel_source_quality_stats: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {
            "candidate_count": 0,
            "avg_source_quality": 0.0,
            "avg_source_conflict": 0.0,
            "avg_source_freshness": 0.0,
            "avg_source_corroboration": 0.0,
            "avg_source_type_trust": 0.0,
            "high_conflict_count": 0,
            "single_source_count": 0,
        }
    )
    source_type_stats: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"candidate_count": 0, "converted_count": 0, "rejected_count": 0, "duplicate_count": 0, "risky_count": 0}
    )
    angle_stats: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"candidate_count": 0, "converted_count": 0, "rejected_count": 0, "avg_alignment": 0.0, "avg_pressure": 0.0}
    )
    theme_stats: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"candidate_count": 0, "converted_count": 0, "rejected_count": 0}
    )
    review_action_stats: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"review_count": 0, "approved_count": 0, "rejected_count": 0}
    )
    workflow_preset_stats: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"review_count": 0, "auto_resolved_count": 0, "approved_count": 0, "rejected_count": 0}
    )
    for run in runs:
        trace = _load_json(run.trace_json)
        policy_label = "unknown"
        if trace:
            result = trace.get("result")
            if isinstance(result, dict) and isinstance(result.get("autonomy_mode"), str):
                policy_label = result["autonomy_mode"]
        channel_policy_stats[(run.channel_id, policy_label)]["run_count"] += 1
    for candidate in candidates:
        run = runs_by_id.get(candidate.agent_run_id)
        if run is None:
            continue
        trace = _load_json(run.trace_json)
        policy_label = "unknown"
        if trace:
            result = trace.get("result")
            if isinstance(result, dict) and isinstance(result.get("autonomy_mode"), str):
                policy_label = result["autonomy_mode"]
        bucket = channel_policy_stats[(candidate.channel_id, policy_label)]
        bucket["candidate_count"] += 1
        if candidate.status == "converted":
            bucket["converted_count"] += 1
        elif candidate.status == "rejected":
            bucket["rejected_count"] += 1
        scores = _load_json(candidate.scores_json)
        source_bundle = _load_json(candidate.source_bundle_json)
        if isinstance(scores, dict):
            quality_bucket = channel_source_quality_stats[candidate.channel_id]
            quality_bucket["candidate_count"] += 1
            quality_bucket["avg_source_quality"] += float(scores.get("source_quality") or 0.0)
            quality_bucket["avg_source_conflict"] += float(scores.get("source_conflict") or 0.0)
            quality_bucket["avg_source_freshness"] += float(scores.get("source_freshness") or 0.0)
            quality_bucket["avg_source_corroboration"] += float(scores.get("source_corroboration") or 0.0)
            quality_bucket["avg_source_type_trust"] += float(scores.get("source_type_trust") or 0.0)
            if float(scores.get("source_conflict") or 0.0) >= 0.65:
                quality_bucket["high_conflict_count"] += 1
        risk_flags = _load_json_list(candidate.risk_flags_json)
        if "single_source" in risk_flags:
            channel_source_quality_stats[candidate.channel_id]["single_source_count"] += 1
        source_types = {classify_source_type(item) for item in source_detail_items(source_bundle)}
        if not source_types:
            source_types = {"unknown"}
        for source_type in source_types:
            bucket = source_type_stats[source_type]
            bucket["candidate_count"] += 1
            if candidate.status == "converted":
                bucket["converted_count"] += 1
            elif candidate.status == "rejected":
                bucket["rejected_count"] += 1
            if "possible_duplicate" in risk_flags or "embedding_duplicate" in risk_flags:
                bucket["duplicate_count"] += 1
            if risk_flags:
                bucket["risky_count"] += 1
        matched_angle = None
        matched_angle_family = None
        if isinstance(source_bundle, dict):
            raw_angle = source_bundle.get("matched_angle")
            if isinstance(raw_angle, str) and raw_angle.strip():
                matched_angle = raw_angle.strip()
            raw_family = source_bundle.get("matched_angle_family")
            if isinstance(raw_family, str) and raw_family.strip():
                matched_angle_family = raw_family.strip()
        if not matched_angle and candidate.headline:
            matched_angle = candidate.headline
        if not matched_angle_family:
            matched_angle_family = canonical_angle_family(matched_angle)
        if matched_angle:
            angle_key = matched_angle_family or matched_angle
            bucket = angle_stats[angle_key]
            bucket["candidate_count"] += 1
            if candidate.status == "converted":
                bucket["converted_count"] += 1
            elif candidate.status == "rejected":
                bucket["rejected_count"] += 1
            if isinstance(scores, dict):
                bucket["avg_alignment"] += float(scores.get("angle_alignment") or 0.0)
                bucket["avg_pressure"] += float(scores.get("angle_pressure") or 0.0)
        for theme in theme_labels_from_texts([matched_angle_family, matched_angle, candidate.topic, candidate.headline]):
            bucket = theme_stats[theme]
            bucket["candidate_count"] += 1
            if candidate.status == "converted":
                bucket["converted_count"] += 1
            elif candidate.status == "rejected":
                bucket["rejected_count"] += 1
    review_rows = list_review_items(session, tenant_id=tenant_id)
    if channel_id is not None:
        review_rows = [row for row in review_rows if row.channel_id == channel_id]
    if cutoff is not None:
        review_rows = [
            row
            for row in review_rows
            if (
                row.created_at.replace(tzinfo=UTC)
                if row.created_at.tzinfo is None
                else row.created_at.astimezone(UTC)
            ) >= cutoff
        ]
    for row in review_rows:
        review_payload = _load_json(row.review_payload_json)
        decision_payload = _load_json(row.decision_json)
        action = None
        if isinstance(decision_payload, dict):
            raw_action = decision_payload.get("review_action")
            if isinstance(raw_action, str) and raw_action.strip():
                action = raw_action.strip()
        preset = None
        if isinstance(review_payload, dict):
            raw_preset = review_payload.get("workflow_preset")
            if isinstance(raw_preset, str) and raw_preset.strip():
                preset = raw_preset.strip()
        if preset:
            bucket = workflow_preset_stats[preset]
            bucket["review_count"] += 1
            if row.status == "approved":
                bucket["approved_count"] += 1
            elif row.status == "rejected":
                bucket["rejected_count"] += 1
            if isinstance(decision_payload, dict) and decision_payload.get("auto_resolved") is True:
                bucket["auto_resolved_count"] += 1
        if not action:
            continue
        bucket = review_action_stats[action]
        bucket["review_count"] += 1
        if row.status == "approved":
            bucket["approved_count"] += 1
        elif row.status == "rejected":
            bucket["rejected_count"] += 1
    channel_titles = {
        row.id: row.title
        for row in session.scalars(
            select(ChannelOrm).where(
                ChannelOrm.id.in_({row.channel_id for row in runs} | {row.channel_id for row in candidates})
            )
        ).all()
    }
    channel_policy_rows = []
    for (row_channel_id, policy), stats in sorted(channel_policy_stats.items(), key=lambda item: (-int(item[1]["candidate_count"]), item[0][0], item[0][1])):
        stats["conversion_rate"] = (
            round(stats["converted_count"] / stats["candidate_count"], 4) if stats["candidate_count"] else 0.0
        )
        stats["approval_rate"] = (
            round(stats["converted_count"] / stats["candidate_count"], 4) if stats["candidate_count"] else 0.0
        )
        channel_policy_rows.append(
            {
                "channel_id": row_channel_id,
                "channel_title": channel_titles.get(row_channel_id),
                "policy": policy,
                **stats,
            }
        )
    channel_source_quality_rows = []
    for row_channel_id, stats in sorted(channel_source_quality_stats.items()):
        candidate_count = int(stats["candidate_count"])
        channel_source_quality_rows.append(
            {
                "channel_id": row_channel_id,
                "channel_title": channel_titles.get(row_channel_id),
                "candidate_count": candidate_count,
                "avg_source_quality": round(float(stats["avg_source_quality"]) / candidate_count, 4) if candidate_count else 0.0,
                "avg_source_conflict": round(float(stats["avg_source_conflict"]) / candidate_count, 4) if candidate_count else 0.0,
                "avg_source_freshness": round(float(stats["avg_source_freshness"]) / candidate_count, 4) if candidate_count else 0.0,
                "avg_source_corroboration": round(float(stats["avg_source_corroboration"]) / candidate_count, 4) if candidate_count else 0.0,
                "avg_source_type_trust": round(float(stats["avg_source_type_trust"]) / candidate_count, 4) if candidate_count else 0.0,
                "high_conflict_share": round(int(stats["high_conflict_count"]) / candidate_count, 4) if candidate_count else 0.0,
                "single_source_share": round(int(stats["single_source_count"]) / candidate_count, 4) if candidate_count else 0.0,
            }
        )
    source_type_rows = [
        {
            "source_type": source_type,
            **stats,
            "conversion_rate": round(stats["converted_count"] / stats["candidate_count"], 4)
            if stats["candidate_count"]
            else 0.0,
            "duplicate_rate": round(stats["duplicate_count"] / stats["candidate_count"], 4)
            if stats["candidate_count"]
            else 0.0,
            "risk_rate": round(stats["risky_count"] / stats["candidate_count"], 4)
            if stats["candidate_count"]
            else 0.0,
            "trust_label": _source_trust_label(stats),
        }
        for source_type, stats in sorted(source_type_stats.items(), key=lambda item: (-int(item[1]["candidate_count"]), item[0]))
    ]
    angle_rows = [
        {
            "angle_family": angle,
            **stats,
            "conversion_rate": round(stats["converted_count"] / stats["candidate_count"], 4)
            if stats["candidate_count"]
            else 0.0,
            "avg_alignment": round(float(stats["avg_alignment"]) / stats["candidate_count"], 4)
            if stats["candidate_count"]
            else 0.0,
            "avg_pressure": round(float(stats["avg_pressure"]) / stats["candidate_count"], 4)
            if stats["candidate_count"]
            else 0.0,
        }
        for angle, stats in sorted(angle_stats.items(), key=lambda item: (-int(item[1]["candidate_count"]), item[0]))
    ]
    theme_rows = [
        {
            "theme": theme,
            **stats,
            "conversion_rate": round(stats["converted_count"] / stats["candidate_count"], 4)
            if stats["candidate_count"]
            else 0.0,
        }
        for theme, stats in sorted(theme_stats.items(), key=lambda item: (-int(item[1]["candidate_count"]), item[0]))
    ]
    review_action_rows = [
        {
            "review_action": action,
            **stats,
            "approval_rate": round(stats["approved_count"] / stats["review_count"], 4)
            if stats["review_count"]
            else 0.0,
            "rejection_rate": round(stats["rejected_count"] / stats["review_count"], 4)
            if stats["review_count"]
            else 0.0,
        }
        for action, stats in sorted(review_action_stats.items(), key=lambda item: (-int(item[1]["review_count"]), item[0]))
    ]
    workflow_preset_rows = [
        {
            "workflow_preset": preset,
            **stats,
            "approval_rate": round(stats["approved_count"] / stats["review_count"], 4)
            if stats["review_count"]
            else 0.0,
            "auto_resolve_rate": round(stats["auto_resolved_count"] / stats["review_count"], 4)
            if stats["review_count"]
            else 0.0,
        }
        for preset, stats in sorted(workflow_preset_stats.items(), key=lambda item: (-int(item[1]["review_count"]), item[0]))
    ]
    freshness_buckets: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"candidate_count": 0, "converted_count": 0, "rejected_count": 0}
    )
    for candidate in candidates:
        source_bundle = _load_json(candidate.source_bundle_json)
        freshness_hours = _candidate_source_age_hours(candidate.created_at, source_bundle)
        bucket_name = _freshness_bucket_name(freshness_hours)
        bucket = freshness_buckets[bucket_name]
        bucket["candidate_count"] += 1
        if candidate.status == "converted":
            bucket["converted_count"] += 1
        elif candidate.status == "rejected":
            bucket["rejected_count"] += 1
    freshness_rows = [
        {
            "bucket": bucket,
            **stats,
            "conversion_rate": round(stats["converted_count"] / stats["candidate_count"], 4)
            if stats["candidate_count"]
            else 0.0,
        }
        for bucket, stats in freshness_buckets.items()
    ]
    diversity = _build_source_diversity_analytics(source_rows, candidate_domains, len(candidates))
    agreement = _build_source_agreement_analytics(candidate_domains, candidate_source_details)
    return {
        "tenant_id": tenant_id,
        "channel_id": channel_id,
        "days": days,
        "cutoff": cutoff.isoformat() if cutoff else None,
        "sources": source_rows,
        "source_diversity": diversity,
        "source_agreement": agreement,
        "source_types": source_type_rows,
        "angles": angle_rows,
        "themes": theme_rows,
        "review_actions": review_action_rows,
        "workflow_presets": workflow_preset_rows,
        "models": model_rows,
        "policies": policy_rows,
        "channel_policies": channel_policy_rows,
        "channel_source_quality": channel_source_quality_rows,
        "policy_overrides": [
            {
                "id": row.id,
                "channel_id": row.channel_id,
                "version": row.version,
                "policy": _load_json(row.policy_json),
            }
            for row in list_agent_policies(session, tenant_id=tenant_id)
            if channel_id is None or row.channel_id in {None, channel_id}
        ],
        "freshness": sorted(
            freshness_rows,
            key=lambda row: ["0-6h", "6-24h", "1-3d", "3d+", "unknown"].index(row["bucket"]),
        ),
        "policy_recommendations": _build_policy_recommendations(runs, channel_policy_rows, channel_source_quality_rows),
    }


def cleanup_agent_runtime(
    session: Session,
    *,
    tenant_id: str | None = None,
    retention_days: int,
    trace_retention_days: int | None = None,
    review_retention_days: int | None = None,
    review_body_retention_days: int | None = None,
    fingerprint_retention_days: int | None = None,
) -> dict:
    now = datetime.now(UTC)
    settings = get_settings()
    trace_days = max(trace_retention_days or retention_days, 1)
    review_days = max(review_retention_days or retention_days, 1)
    review_body_days = max(review_body_retention_days or review_days, 1)
    fingerprint_days = max(fingerprint_retention_days or review_days, 1)
    trace_cutoff = now - timedelta(days=trace_days)
    review_cutoff = now - timedelta(days=review_days)
    review_body_cutoff = now - timedelta(days=review_body_days)
    fingerprint_cutoff = now - timedelta(days=fingerprint_days)

    trace_runs_stmt = select(AgentRunOrm).where(AgentRunOrm.created_at < trace_cutoff)
    runs_stmt = select(AgentRunOrm).where(AgentRunOrm.created_at < review_cutoff)
    reviews_stmt = select(ReviewQueueItemOrm).where(ReviewQueueItemOrm.created_at < review_cutoff)
    if tenant_id:
        trace_runs_stmt = trace_runs_stmt.where(AgentRunOrm.tenant_id == tenant_id)
        runs_stmt = runs_stmt.where(AgentRunOrm.tenant_id == tenant_id)
        reviews_stmt = reviews_stmt.where(ReviewQueueItemOrm.tenant_id == tenant_id)

    trace_runs = list(session.scalars(trace_runs_stmt).all())
    stripped_traces = 0
    compacted_traces = 0
    deleted_steps = 0
    for row in trace_runs:
        changed = False
        if row.trace_json:
            if settings.agent_trace_compaction_mode == "summary":
                compacted = _compact_run_trace_payload(row.trace_json)
                if compacted is not None:
                    row.trace_json = json.dumps(compacted, ensure_ascii=True)
                    changed = True
                    compacted_traces += 1
            else:
                row.trace_json = None
                changed = True
        if settings.agent_trace_compaction_mode == "drop" and row.token_usage_json:
            row.token_usage_json = None
            changed = True
        if changed and settings.agent_trace_compaction_mode == "drop":
            stripped_traces += 1
    trace_run_ids = [row.id for row in trace_runs]
    if trace_run_ids:
        deleted_steps = (
            session.execute(delete(AgentRunStepOrm).where(AgentRunStepOrm.agent_run_id.in_(trace_run_ids))).rowcount or 0
        )

    old_reviews = list(session.scalars(reviews_stmt).all())
    review_body_stmt = select(ReviewQueueItemOrm).where(ReviewQueueItemOrm.created_at < review_body_cutoff)
    if tenant_id:
        review_body_stmt = review_body_stmt.where(ReviewQueueItemOrm.tenant_id == tenant_id)
    compacted_review_payloads = 0
    for row in session.scalars(review_body_stmt).all():
        compacted_payload = _compact_review_payload(row.review_payload_json)
        if compacted_payload is None:
            continue
        row.review_payload_json = json.dumps(compacted_payload, ensure_ascii=True)
        compacted_review_payloads += 1
    pending_by_run: dict[str, int] = defaultdict(int)
    if old_reviews:
        for row in old_reviews:
            if row.status == "pending":
                pending_by_run[row.agent_run_id] += 1

    old_runs = list(session.scalars(runs_stmt).all())
    deletable_run_ids = [
        row.id
        for row in old_runs
        if row.status in {"completed", "failed"} or pending_by_run.get(row.id, 0) == 0
    ]

    deleted_review_items = 0
    deleted_runs = 0
    deleted_embeddings = 0
    deleted_fingerprints = 0
    sanitized_fingerprints = 0

    if deletable_run_ids:
        candidate_ids = list(
            session.scalars(
                select(ContentCandidateOrm.id).where(ContentCandidateOrm.agent_run_id.in_(deletable_run_ids))
            ).all()
        )
        review_rows = list(
            session.scalars(
                select(ReviewQueueItemOrm).where(ReviewQueueItemOrm.agent_run_id.in_(deletable_run_ids))
            ).all()
        )
        for row in review_rows:
            session.delete(row)
        deleted_review_items = len(review_rows)

        if candidate_ids:
            embedding_delete_stmt = delete(ContentEmbeddingOrm).where(
                ContentEmbeddingOrm.entity_type == "candidate",
                ContentEmbeddingOrm.entity_id.in_(candidate_ids),
            )
            if tenant_id:
                embedding_delete_stmt = embedding_delete_stmt.where(ContentEmbeddingOrm.tenant_id == tenant_id)
            deleted_embeddings = session.execute(embedding_delete_stmt).rowcount or 0
            session.execute(delete(ContentCandidateOrm).where(ContentCandidateOrm.id.in_(candidate_ids)))

        session.execute(delete(AgentRunStepOrm).where(AgentRunStepOrm.agent_run_id.in_(deletable_run_ids)))

        run_rows = list(session.scalars(select(AgentRunOrm).where(AgentRunOrm.id.in_(deletable_run_ids))).all())
        for row in run_rows:
            session.delete(row)
        deleted_runs = len(run_rows)

    fingerprint_stmt = select(ContentSourceFingerprintOrm).where(ContentSourceFingerprintOrm.created_at < fingerprint_cutoff)
    if tenant_id:
        fingerprint_stmt = fingerprint_stmt.where(ContentSourceFingerprintOrm.tenant_id == tenant_id)
    fingerprints = list(session.scalars(fingerprint_stmt).all())
    if fingerprints:
        existing_candidate_ids = {
            row[0]
            for row in session.execute(select(ContentCandidateOrm.id).where(ContentCandidateOrm.id.in_([
                fp.candidate_id for fp in fingerprints if fp.candidate_id
            ]))).all()
        }
        existing_content_ids = {
            row[0]
            for row in session.execute(select(ContentItemOrm.id).where(ContentItemOrm.id.in_([
                fp.published_content_item_id for fp in fingerprints if fp.published_content_item_id
            ]))).all()
        }
        for row in fingerprints:
            candidate_exists = (row.candidate_id in existing_candidate_ids) if row.candidate_id else False
            content_exists = (row.published_content_item_id in existing_content_ids) if row.published_content_item_id else False
            changed = False
            if row.candidate_id and not candidate_exists:
                row.candidate_id = None
                changed = True
            if row.published_content_item_id and not content_exists:
                row.published_content_item_id = None
                changed = True
            if changed:
                sanitized_fingerprints += 1
            if not row.candidate_id and not row.published_content_item_id:
                session.delete(row)
                deleted_fingerprints += 1

    return {
        "tenant_id": tenant_id,
        "retention_days": retention_days,
        "trace_retention_days": trace_days,
        "review_retention_days": review_days,
        "review_body_retention_days": review_body_days,
        "fingerprint_retention_days": fingerprint_days,
        "trace_cutoff": trace_cutoff,
        "review_cutoff": review_cutoff,
        "review_body_cutoff": review_body_cutoff,
        "fingerprint_cutoff": fingerprint_cutoff,
        "stripped_run_traces": stripped_traces,
        "compacted_run_traces": compacted_traces,
        "deleted_run_steps": deleted_steps,
        "deleted_runs": deleted_runs,
        "deleted_review_items": deleted_review_items,
        "compacted_review_payloads": compacted_review_payloads,
        "deleted_candidate_embeddings": deleted_embeddings,
        "sanitized_fingerprints": sanitized_fingerprints,
        "deleted_fingerprints": deleted_fingerprints,
    }


def _compact_run_trace_payload(raw: str | None) -> dict | None:
    payload = _load_json(raw)
    if not isinstance(payload, dict):
        return None
    return {
        "trace_policy": "compacted",
        "trace": [],
        "tool_summary": payload.get("tool_summary") if isinstance(payload.get("tool_summary"), dict) else {},
        "result": payload.get("result") if isinstance(payload.get("result"), dict) else {},
    }


def _compact_review_payload(raw: str | None) -> dict | None:
    payload = _load_json(raw)
    if not isinstance(payload, dict):
        return None
    source_bundle = payload.get("source_bundle") if isinstance(payload.get("source_bundle"), dict) else {}
    summary_bundle = {
        "primary_sources": source_bundle.get("primary_sources") if isinstance(source_bundle.get("primary_sources"), list) else [],
        "topic_angles": source_bundle.get("topic_angles") if isinstance(source_bundle.get("topic_angles"), list) else [],
        "selection_context": source_bundle.get("selection_context") if isinstance(source_bundle.get("selection_context"), dict) else {},
        "matched_angle": source_bundle.get("matched_angle"),
        "matched_angle_family": source_bundle.get("matched_angle_family"),
    }
    return {
        "compacted": True,
        "topic": payload.get("topic"),
        "headline": payload.get("headline"),
        "summary": payload.get("summary"),
        "why_now": payload.get("why_now"),
        "dedup_summary": payload.get("dedup_summary"),
        "style_fit_summary": payload.get("style_fit_summary"),
        "workflow_preset": payload.get("workflow_preset"),
        "suggested_decision": payload.get("suggested_decision"),
        "suggested_review_action": payload.get("suggested_review_action"),
        "review_hints": payload.get("review_hints") if isinstance(payload.get("review_hints"), list) else [],
        "source_quality_summary": payload.get("source_quality_summary") if isinstance(payload.get("source_quality_summary"), dict) else {},
        "scores": payload.get("scores") if isinstance(payload.get("scores"), dict) else {},
        "risk_flags": payload.get("risk_flags") if isinstance(payload.get("risk_flags"), list) else [],
        "source_bundle": summary_bundle,
        "policy_resolution": payload.get("policy_resolution") if isinstance(payload.get("policy_resolution"), dict) else {},
    }


def _candidate_source_age_hours(candidate_created_at: datetime, source_bundle: dict) -> float | None:
    timestamps: list[datetime] = []
    if not isinstance(source_bundle, dict):
        return None
    detailed_sources: list[dict] = []
    for key in ("primary_sources_details", "seed_sources"):
        value = source_bundle.get(key)
        if isinstance(value, list):
            detailed_sources.extend(item for item in value if isinstance(item, dict))
    for item in detailed_sources:
        for key in ("published_at", "updated_at"):
            parsed = _parse_datetime(item.get(key))
            if parsed is not None:
                timestamps.append(parsed)
                break
    if not timestamps:
        return None
    freshest = max(timestamps)
    delta = candidate_created_at.astimezone(UTC) - freshest
    return max(delta.total_seconds() / 3600, 0.0)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _freshness_bucket_name(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    if hours <= 6:
        return "0-6h"
    if hours <= 24:
        return "6-24h"
    if hours <= 72:
        return "1-3d"
    return "3d+"


def _source_trust_label(stats: dict[str, float | int]) -> str:
    candidate_count = int(stats["candidate_count"])
    if candidate_count < 3:
        return "insufficient_data"
    conversion_rate = float(stats["converted_count"]) / candidate_count if candidate_count else 0.0
    duplicate_rate = float(stats["duplicate_count"]) / candidate_count if candidate_count else 0.0
    reject_rate = float(stats["rejected_count"]) / candidate_count if candidate_count else 0.0
    if conversion_rate >= 0.6 and duplicate_rate <= 0.15 and reject_rate <= 0.2:
        return "trusted"
    if duplicate_rate >= 0.4 or reject_rate >= 0.5:
        return "risky"
    return "mixed"


def _build_policy_recommendations(
    runs: list[AgentRunOrm],
    channel_policy_rows: list[dict[str, Any]],
    channel_source_quality_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    latest_policy_by_channel: dict[str, tuple[datetime, str]] = {}
    for run in runs:
        trace = _load_json(run.trace_json)
        policy_label = "unknown"
        if trace:
            result = trace.get("result")
            if isinstance(result, dict) and isinstance(result.get("autonomy_mode"), str):
                policy_label = result["autonomy_mode"]
        current = latest_policy_by_channel.get(run.channel_id)
        if current is None or run.created_at > current[0]:
            latest_policy_by_channel[run.channel_id] = (run.created_at, policy_label)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in channel_policy_rows:
        grouped[row["channel_id"]].append(row)
    quality_by_channel = {row["channel_id"]: row for row in channel_source_quality_rows}

    recommendations: list[dict[str, Any]] = []
    for channel_id, rows in sorted(grouped.items()):
        current_policy = latest_policy_by_channel.get(channel_id, (datetime.min.replace(tzinfo=UTC), "unknown"))[1]
        latest_run_at = latest_policy_by_channel.get(channel_id, (datetime.min.replace(tzinfo=UTC), "unknown"))[0]
        current_row = next((row for row in rows if row["policy"] == current_policy), None)
        basis = current_row or max(rows, key=lambda row: row["candidate_count"])
        candidate_count = int(basis["candidate_count"])
        conversion_rate = float(basis["conversion_rate"])
        rejection_rate = round(
            (float(basis["rejected_count"]) / candidate_count) if candidate_count else 0.0,
            4,
        )
        source_quality = quality_by_channel.get(channel_id, {})
        avg_source_quality = float(source_quality.get("avg_source_quality") or 0.0)
        avg_source_conflict = float(source_quality.get("avg_source_conflict") or 0.0)
        single_source_share = float(source_quality.get("single_source_share") or 0.0)
        if candidate_count < 3:
            recommendation = "needs_more_history"
            recommended_policy = current_policy
            reason = "not enough reviewed candidates yet"
            confidence = "low"
        elif avg_source_conflict >= 0.7 or avg_source_quality <= 0.3:
            recommendation = "keep_manual"
            recommended_policy = "draft_approval"
            reason = "source evidence is too conflicted or low-quality for higher autonomy"
            confidence = _policy_recommendation_confidence(
                candidate_count, conversion_rate, rejection_rate, avg_source_quality, avg_source_conflict
            )
        elif single_source_share >= 0.8 and current_policy == "draft_approval":
            recommendation = "keep_current"
            recommended_policy = current_policy
            reason = "channel still relies too heavily on single-source candidates"
            confidence = _policy_recommendation_confidence(
                candidate_count, conversion_rate, rejection_rate, avg_source_quality, avg_source_conflict
            )
        elif rejection_rate >= 0.4:
            recommendation = "keep_manual"
            recommended_policy = "draft_approval"
            reason = "rejection rate is too high for raising autonomy"
            confidence = _policy_recommendation_confidence(
                candidate_count, conversion_rate, rejection_rate, avg_source_quality, avg_source_conflict
            )
        elif conversion_rate >= 0.8 and current_policy != "guarded_auto_publish":
            recommendation = "safe_to_raise"
            recommended_policy = "guarded_auto_publish"
            reason = "high conversion rate with low rejection risk"
            confidence = _policy_recommendation_confidence(
                candidate_count, conversion_rate, rejection_rate, avg_source_quality, avg_source_conflict
            )
        elif conversion_rate >= 0.5 and current_policy == "draft_approval":
            recommendation = "safe_to_raise"
            recommended_policy = "plan_approval"
            reason = "channel shows stable conversion under review"
            confidence = _policy_recommendation_confidence(
                candidate_count, conversion_rate, rejection_rate, avg_source_quality, avg_source_conflict
            )
        else:
            recommendation = "keep_current"
            recommended_policy = current_policy
            reason = "current policy matches observed channel quality"
            confidence = _policy_recommendation_confidence(
                candidate_count, conversion_rate, rejection_rate, avg_source_quality, avg_source_conflict
            )
        decayed_confidence = _apply_confidence_decay(confidence, latest_run_at)
        rationale_weights = _build_policy_rationale_weights(
            candidate_count=candidate_count,
            conversion_rate=conversion_rate,
            rejection_rate=rejection_rate,
            last_run_at=latest_run_at,
            source_quality=avg_source_quality,
            source_conflict=avg_source_conflict,
        )
        recommendations.append(
            {
                "channel_id": channel_id,
                "channel_title": basis.get("channel_title"),
                "current_policy": current_policy,
                "recommended_policy": recommended_policy,
                "recommendation": recommendation,
                "confidence": decayed_confidence,
                "base_confidence": confidence,
                "confidence_explanation": _build_confidence_explanation(
                    base_confidence=confidence,
                    final_confidence=decayed_confidence,
                    candidate_count=candidate_count,
                    last_run_at=latest_run_at,
                ),
                "rationale_weights": rationale_weights,
                "candidate_count": candidate_count,
                "conversion_rate": conversion_rate,
                "rejection_rate": rejection_rate,
                "avg_source_quality": avg_source_quality,
                "avg_source_conflict": avg_source_conflict,
                "single_source_share": single_source_share,
                "last_run_at": latest_run_at,
                "reason": reason,
            }
        )
    return recommendations


def _policy_recommendation_confidence(
    candidate_count: int,
    conversion_rate: float,
    rejection_rate: float,
    source_quality: float,
    source_conflict: float,
) -> str:
    if candidate_count < 3:
        return "low"
    if candidate_count >= 8 and conversion_rate >= 0.75 and rejection_rate <= 0.2 and source_quality >= 0.6 and source_conflict <= 0.35:
        return "high"
    if candidate_count >= 5 and rejection_rate <= 0.35 and source_quality >= 0.4 and source_conflict <= 0.55:
        return "medium"
    return "low"


def _build_source_diversity_analytics(
    source_rows: list[dict[str, Any]],
    candidate_domains: list[set[str]],
    total_candidates: int,
) -> dict[str, Any]:
    top_sources = source_rows[:3]
    top_share = (
        round(sum(int(row["candidate_count"]) for row in top_sources) / total_candidates, 4)
        if total_candidates
        else 0.0
    )
    avg_domains_per_candidate = (
        round(sum(len(domains) for domains in candidate_domains) / len(candidate_domains), 4)
        if candidate_domains
        else 0.0
    )
    if top_share >= 0.8:
        concentration_label = "high"
    elif top_share >= 0.5:
        concentration_label = "medium"
    else:
        concentration_label = "low"
    repeated_domain_pressure = round(
        sum(max(len(domains) - len(set(domains)), 0) for domains in candidate_domains) / total_candidates,
        4,
    ) if total_candidates else 0.0
    novelty_score = round(max(0.0, 1.0 - top_share), 4)
    return {
        "unique_domains": len(source_rows),
        "avg_domains_per_candidate": avg_domains_per_candidate,
        "top_3_share": top_share,
        "concentration_label": concentration_label,
        "repeated_domain_pressure": repeated_domain_pressure,
        "novelty_score": novelty_score,
        "top_domains": [
            {
                "domain": row["domain"],
                "candidate_count": row["candidate_count"],
                "trust_label": row["trust_label"],
            }
            for row in top_sources
        ],
    }


def _build_source_agreement_analytics(
    candidate_domains: list[set[str]],
    candidate_source_details: list[list[dict[str, Any]]],
) -> dict[str, Any]:
    total = len(candidate_domains)
    single_source = sum(1 for domains in candidate_domains if len(domains) <= 1)
    two_source = sum(1 for domains in candidate_domains if len(domains) == 2)
    multi_source = sum(1 for domains in candidate_domains if len(domains) >= 3)
    corroborated = sum(1 for domains in candidate_domains if len(domains) >= 2)
    disagreement = 0
    conflict = 0
    disagreement_scores: list[float] = []
    conflict_scores: list[float] = []
    top_conflicts: list[dict[str, Any]] = []
    for items in candidate_source_details:
        score, conflict_score = source_disagreement_details(items)
        disagreement_scores.append(score)
        conflict_scores.append(conflict_score)
        top_conflicts.extend(source_conflict_explanations(items, max_examples=2))
        if score >= 0.5:
            disagreement += 1
        if conflict_score >= 0.65:
            conflict += 1
    top_conflicts.sort(key=lambda item: float(item.get("conflict_score") or 0.0), reverse=True)
    return {
        "single_source_candidates": single_source,
        "two_source_candidates": two_source,
        "multi_source_candidates": multi_source,
        "corroborated_candidates": corroborated,
        "corroborated_share": round(corroborated / total, 4) if total else 0.0,
        "disagreement_candidates": disagreement,
        "disagreement_share": round(disagreement / total, 4) if total else 0.0,
        "avg_disagreement_score": round(sum(disagreement_scores) / len(disagreement_scores), 4) if disagreement_scores else 0.0,
        "conflict_candidates": conflict,
        "conflict_share": round(conflict / total, 4) if total else 0.0,
        "avg_conflict_score": round(sum(conflict_scores) / len(conflict_scores), 4) if conflict_scores else 0.0,
        "top_conflict_examples": top_conflicts[:5],
    }


def _build_confidence_explanation(
    *,
    base_confidence: str,
    final_confidence: str,
    candidate_count: int,
    last_run_at: datetime,
) -> str:
    if last_run_at == datetime.min.replace(tzinfo=UTC):
        recency = "no recent run history"
    else:
        age_days = int(max((datetime.now(UTC) - last_run_at.astimezone(UTC)).total_seconds() / 86400, 0.0))
        recency = f"last evidence is {age_days} day(s) old"
    if base_confidence != final_confidence:
        return (
            f"Base confidence was {base_confidence} from {candidate_count} reviewed candidate(s), "
            f"but it decayed to {final_confidence} because {recency}."
        )
    return f"Confidence is {final_confidence} based on {candidate_count} reviewed candidate(s); {recency}."


def _build_policy_rationale_weights(
    *,
    candidate_count: int,
    conversion_rate: float,
    rejection_rate: float,
    last_run_at: datetime,
    source_quality: float,
    source_conflict: float,
) -> dict[str, float]:
    age_days = (
        max((datetime.now(UTC) - last_run_at.astimezone(UTC)).total_seconds() / 86400, 0.0)
        if last_run_at != datetime.min.replace(tzinfo=UTC)
        else 9999.0
    )
    history_weight = min(candidate_count / 10.0, 1.0)
    quality_weight = max(min(conversion_rate - rejection_rate + 0.5, 1.0), 0.0)
    recency_weight = 1.0 if age_days <= 14 else 0.6 if age_days <= 30 else 0.25
    return {
        "history": round(history_weight, 4),
        "quality": round(quality_weight, 4),
        "recency": round(recency_weight, 4),
        "source_quality": round(max(min(source_quality, 1.0), 0.0), 4),
        "conflict_penalty": round(max(min(source_conflict, 1.0), 0.0), 4),
    }


def _apply_confidence_decay(confidence: str, last_run_at: datetime) -> str:
    if last_run_at == datetime.min.replace(tzinfo=UTC):
        return "low"
    age_days = max((datetime.now(UTC) - last_run_at.astimezone(UTC)).total_seconds() / 86400, 0.0)
    if age_days <= 14:
        return confidence
    if age_days <= 30:
        return _downgrade_confidence(confidence)
    return _downgrade_confidence(_downgrade_confidence(confidence))


def _downgrade_confidence(confidence: str) -> str:
    if confidence == "high":
        return "medium"
    if confidence == "medium":
        return "low"
    return "low"
