from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from postbridge.agent.graphs.post_copilot import build_post_copilot_graph
from postbridge.agent.policies import evaluate_policy_guardrails
from postbridge.agent.graphs.topic_scout import build_topic_scout_graph
from postbridge.agent.providers.openai_compatible import ensure_openai_compatible_provider
from postbridge.agent.storage import (
    append_run_step,
    create_agent_run,
    create_review_queue_item,
    materialize_candidate_on_approval,
    resolve_review_item,
    resolve_agent_policy,
    resolve_agent_workspace_policy,
    mark_run_completed,
    mark_run_failed,
    mark_run_started,
    save_candidate,
    summarize_tool_trace,
)
from postbridge.config import get_settings
from postbridge.agent.tools import (
    build_review_payload,
    canonical_angle_family,
    extract_candidate_angle,
    find_default_provider,
    find_similar_embeddings,
    fingerprint_text,
    historical_angle_pressure,
    upsert_content_embedding,
)
from postbridge.domain.errors import PostbridgeError
from postbridge.models.domain import AgentTaskOrm
from postbridge.observability.logging import (
    log_agent_run_completed,
    log_agent_run_failed,
    log_agent_run_started,
    log_agent_step_completed,
    log_review_item_created,
)
from postbridge.observability.metrics import (
    inc_agent_review_item_created,
    inc_agent_run_completed,
    inc_agent_run_failed,
    inc_agent_run_started,
    observe_agent_run_duration_seconds,
    observe_agent_step_duration_seconds,
    observe_agent_token_usage,
    observe_agent_tool_calls,
)
from postbridge.services.ai_editor_chat import (
    append_agent_editor_candidate_ready,
    append_agent_editor_context_loaded,
    append_agent_editor_run_completed,
    append_agent_editor_run_started,
    append_agent_editor_source_package_ready,
    append_ai_chat_error,
)

SEARCH_IMAGE_MODES = {"none", "web_search", "generate"}


def _normalize_search_image_mode(value: Any, *, fallback_image_request: bool) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in {"off", "false", "no", "without_images", "no_images"}:
        return "none"
    if normalized in {"search", "web", "network", "source", "source_images"}:
        return "web_search"
    if normalized in {"generation", "generated"}:
        return "generate"
    if normalized in SEARCH_IMAGE_MODES:
        return normalized
    return "web_search" if fallback_image_request else "none"


class AgentOrchestrator:
    def __init__(self, session: Session) -> None:
        self._session = session

    def run_once(
        self,
        *,
        tenant_id: str,
        channel_id: str,
        mode: str,
        user_request: str | None,
        topic_definition: str | None,
        editorial_instructions: str | None = None,
        content_item_id: str | None,
        max_candidates: int,
        agent_task: AgentTaskOrm | None = None,
        autonomy_mode: str | None = None,
        image_request: bool = False,
        search_image_mode: str | None = None,
        seed_urls: list[str] | None = None,
        approved_image_urls: list[str] | None = None,
        require_source_approval: bool = False,
    ) -> dict[str, Any]:
        record_editor_timeline = bool(mode == "post_copilot" and content_item_id)
        timeline_content_item_id = content_item_id
        policy, policy_resolution = resolve_agent_policy(
            self._session,
            tenant_id=tenant_id,
            channel_id=channel_id,
            mode=autonomy_mode or (agent_task.autonomy_mode if agent_task else None),
        )
        _, workspace_policy_resolution = resolve_agent_workspace_policy(
            self._session,
            tenant_id=tenant_id,
            channel_id=channel_id,
        )
        workspace_policy = workspace_policy_resolution.get("effective_workspace_policy") or {}
        normalized_search_image_mode = _normalize_search_image_mode(
            search_image_mode,
            fallback_image_request=image_request,
        )
        effective_image_request = (
            normalized_search_image_mode in {"web_search", "generate"}
            if mode == "topic_scout" and search_image_mode is not None
            else image_request
        )
        provider_row = find_default_provider(self._session, tenant_id=tenant_id)
        provider = ensure_openai_compatible_provider(provider_row)
        run = create_agent_run(
            self._session,
            tenant_id=tenant_id,
            channel_id=channel_id,
            graph_name=mode,
            trigger_type="scheduled" if agent_task is not None else "api",
            user_request=user_request,
            topic_definition=topic_definition,
            agent_task_id=agent_task.id if agent_task else None,
            content_item_id=content_item_id,
            model=agent_task.model_name if agent_task and agent_task.model_name else provider.model_name,
            provider_type=provider.provider_type,
        )
        mark_run_started(self._session, run)
        if record_editor_timeline:
            append_agent_editor_run_started(
                self._session,
                tenant_id=tenant_id,
                content_item_id=content_item_id,
                agent_run_id=run.id,
                user_request=user_request,
            )
        run_started_at = datetime.now(UTC)
        log_agent_run_started(run.id, tenant_id=tenant_id, channel_id=channel_id, mode=mode)
        inc_agent_run_started(mode)
        append_run_step(
            self._session,
            agent_run_id=run.id,
            tenant_id=tenant_id,
            step_name="run_started",
            status="ok",
            input_payload={
                "mode": mode,
                "user_request": user_request,
                "topic_definition": topic_definition,
                "content_item_id": content_item_id,
                "autonomy_mode": policy.mode,
                "search_image_mode": normalized_search_image_mode,
                "require_source_approval": require_source_approval,
                "policy_resolution": policy_resolution,
                "workspace_policy_resolution": workspace_policy_resolution,
            },
            started_at=run_started_at,
            completed_at=run_started_at,
        )
        try:
            graph_started_at = datetime.now(UTC)
            if mode == "post_copilot":
                compiled = build_post_copilot_graph(session=self._session, provider=provider)
            else:
                compiled = build_topic_scout_graph(
                    session=self._session,
                    provider=provider,
                    max_candidates=max_candidates,
                )
            state = compiled.invoke(
                {
                    "tenant_id": tenant_id,
                    "channel_id": channel_id,
                    "mode": mode,
                    "user_request": user_request,
                    "topic_definition": topic_definition,
                    "editorial_instructions": editorial_instructions,
                    "image_request": effective_image_request,
                    "search_image_mode": normalized_search_image_mode,
                    "search_image_mode_source": "task" if search_image_mode is not None else "legacy_image_request",
                    "content_item_id": content_item_id,
                    "agent_task_id": agent_task.id if agent_task else None,
                    "agent_run_id": run.id,
                    "seed_urls": seed_urls or [],
                    "approved_image_urls": approved_image_urls or [],
                    "require_source_approval": require_source_approval,
                    "workspace_policy": workspace_policy,
                    "tool_trace": [],
                    "errors": [],
                }
            )
            graph_completed_at = datetime.now(UTC)
            if record_editor_timeline:
                append_agent_editor_context_loaded(
                    self._session,
                    tenant_id=tenant_id,
                    content_item_id=content_item_id,
                    agent_run_id=run.id,
                )
            append_run_step(
                self._session,
                agent_run_id=run.id,
                tenant_id=tenant_id,
                step_name="graph_invoke",
                status="ok",
                output_payload={"selected_candidates": len(state.get("selected_candidates") or [])},
                started_at=graph_started_at,
                completed_at=graph_completed_at,
            )
            log_agent_step_completed(
                run.id,
                tenant_id=tenant_id,
                step_name="graph_invoke",
                status="ok",
                duration_ms=int((graph_completed_at - graph_started_at).total_seconds() * 1000),
            )
            observe_agent_step_duration_seconds(
                "graph_invoke",
                max((graph_completed_at - graph_started_at).total_seconds(), 0.0),
            )
            review_items: list[dict[str, Any]] = []
            source_package_review_items: list[dict[str, Any]] = []
            saved_candidates: list[dict[str, Any]] = []
            auto_materialized: list[dict[str, Any]] = []
            guardrail_blocks: list[dict[str, Any]] = []
            source_package = state.get("source_package") if isinstance(state.get("source_package"), dict) else {}
            source_package_summary = (
                state.get("source_package_summary") if isinstance(state.get("source_package_summary"), dict) else {}
            )
            source_package_sources = (
                source_package.get("primary_sources_details")
                if isinstance(source_package.get("primary_sources_details"), list)
                else []
            )
            explicit_seed_urls = [item for item in (seed_urls or []) if isinstance(item, str) and item.strip()]
            if (
                mode == "post_copilot"
                and require_source_approval
                and not explicit_seed_urls
                and bool(source_package_sources)
                and isinstance(user_request, str)
                and user_request.strip()
            ):
                candidate_started_at = datetime.now(UTC)
                preview_candidate = {
                    "topic": "Source package approval",
                    "headline": user_request.strip()[:512],
                    "body_markdown": "",
                    "summary": f"Selected sources: {len(source_package_sources)}",
                    "why_now": "Awaiting source approval before draft generation",
                    "style_fit_summary": "Source package preview",
                    "source_bundle": source_package,
                    "scores": {
                        "source_corroboration": source_package_summary.get("corroboration_score") or 0.0,
                        "source_freshness": source_package_summary.get("freshness_score") or 0.0,
                        "source_quality": source_package_summary.get("corroboration_score") or 0.0,
                    },
                    "risk_flags": source_package_summary.get("risk_flags") or [],
                    "draft_json": {
                        "source_package": source_package,
                        "source_package_summary": source_package_summary,
                    },
                }
                row = save_candidate(
                    self._session,
                    agent_run_id=run.id,
                    tenant_id=tenant_id,
                    channel_id=channel_id,
                    content_item_id=content_item_id,
                    candidate=preview_candidate,
                )
                append_run_step(
                    self._session,
                    agent_run_id=run.id,
                    tenant_id=tenant_id,
                    step_name="candidate_saved",
                    status="ok",
                    output_payload={
                        "candidate_id": row.id,
                        "headline": row.headline,
                        "topic": row.topic,
                    },
                    started_at=candidate_started_at,
                    completed_at=datetime.now(UTC),
                )
                approved_seed_urls = [
                    item.get("url")
                    for item in source_package_sources
                    if isinstance(item, dict) and isinstance(item.get("url"), str) and item.get("url")
                ]
                review_payload = {
                    "kind": "source_package",
                    "candidate_id": row.id,
                    "channel_id": channel_id,
                    "content_item_id": content_item_id,
                    "autonomy_mode": policy.mode,
                    "user_request": user_request,
                    "topic_definition": topic_definition,
                    "image_request": effective_image_request,
                    "search_image_mode": normalized_search_image_mode,
                    "source_package": source_package,
                    "source_package_summary": source_package_summary,
                    "approved_seed_urls": approved_seed_urls,
                    "review_hints": ["confirm_sources"],
                    "workflow_preset": "source_package_approval",
                    "suggested_decision": "approved",
                    "suggested_review_action": "approve_as_is",
                    "proposed_next_action": "review_source_package",
                    "created_at": row.created_at.isoformat(),
                }
                review_row = create_review_queue_item(
                    self._session,
                    tenant_id=tenant_id,
                    channel_id=channel_id,
                    agent_run_id=run.id,
                    candidate_id=row.id,
                    review_payload=review_payload,
                )
                source_package_review_items.append(
                    {
                        "review_item_id": review_row.id,
                        "candidate_id": row.id,
                        "status": review_row.status,
                        "kind": "source_package",
                    }
                )
                append_run_step(
                    self._session,
                    agent_run_id=run.id,
                    tenant_id=tenant_id,
                    step_name="source_package_review_item_created",
                    status="ok",
                    output_payload={
                        "review_item_id": review_row.id,
                        "candidate_id": row.id,
                        "source_count": len(source_package_sources),
                        "image_candidate_count": len(source_package.get("image_candidates") or []),
                    },
                )
                log_review_item_created(
                    review_row.id,
                    candidate_id=row.id,
                    tenant_id=tenant_id,
                    channel_id=channel_id,
                )
                inc_agent_review_item_created()
                if record_editor_timeline:
                    append_agent_editor_source_package_ready(
                        self._session,
                        tenant_id=tenant_id,
                        content_item_id=content_item_id,
                        agent_run_id=run.id,
                        source_count=len(source_package_sources),
                        image_candidate_count=len(source_package.get("image_candidates") or []),
                    )
                result = {
                    "agent_run_id": run.id,
                    "mode": mode,
                    "status": "awaiting_review",
                    "autonomy_mode": policy.mode,
                    "policy_resolution": policy_resolution,
                    "candidates": [],
                    "review_items": [],
                    "source_package_review_items": source_package_review_items,
                    "auto_materialized": [],
                    "guardrail_blocks": [],
                    "tool_trace": state.get("tool_trace") or [],
                }
                append_run_step(
                    self._session,
                    agent_run_id=run.id,
                    tenant_id=tenant_id,
                    step_name="run_completed",
                    status="ok",
                    output_payload={
                        "candidate_count": 0,
                        "review_count": 0,
                        "source_package_review_count": len(source_package_review_items),
                        "auto_materialized_count": 0,
                    },
                )
                tool_summary = summarize_tool_trace(state.get("tool_trace") or [])
                duration_seconds = max((datetime.now(UTC) - run_started_at).total_seconds(), 0.0)
                mark_run_completed(
                    self._session,
                    run,
                    trace=state.get("tool_trace") or [],
                    result=result,
                    review_created=True,
                    token_usage=tool_summary["usage_totals"],
                )
                if mode == "post_copilot" and timeline_content_item_id:
                    append_agent_editor_run_completed(
                        self._session,
                        tenant_id=tenant_id,
                        content_item_id=timeline_content_item_id,
                        agent_run_id=run.id,
                        result=result,
                    )
                log_agent_run_completed(
                    run.id,
                    tenant_id=tenant_id,
                    channel_id=channel_id,
                    mode=mode,
                    review_count=len(source_package_review_items),
                    duration_ms=int(duration_seconds * 1000),
                    token_usage_total=int(tool_summary["usage_totals"]["total_tokens"]),
                    tool_call_count=int(tool_summary["tool_call_count"]),
                    trace_policy=get_settings().agent_trace_policy,
                )
                inc_agent_run_completed(mode)
                observe_agent_run_duration_seconds(mode, duration_seconds)
                observe_agent_token_usage(mode, int(tool_summary["usage_totals"]["total_tokens"]))
                observe_agent_tool_calls(int(tool_summary["tool_call_count"]))
                self._session.flush()
                return result
            for candidate in state.get("selected_candidates") or []:
                candidate_started_at = datetime.now(UTC)
                row = save_candidate(
                    self._session,
                    agent_run_id=run.id,
                    tenant_id=tenant_id,
                    channel_id=channel_id,
                    content_item_id=content_item_id,
                    candidate=candidate,
                )
                append_run_step(
                    self._session,
                    agent_run_id=run.id,
                    tenant_id=tenant_id,
                    step_name="candidate_saved",
                    status="ok",
                    output_payload={
                        "candidate_id": row.id,
                        "headline": row.headline,
                        "topic": row.topic,
                    },
                    started_at=candidate_started_at,
                    completed_at=datetime.now(UTC),
                )
                embedding_text = "\n".join(
                    x for x in [row.headline or "", row.body_markdown or "", row.summary or ""] if x
                ).strip()
                if embedding_text:
                    vector, _ = provider.invoke_embedding(text=embedding_text)
                    upsert_content_embedding(
                        self._session,
                        tenant_id=tenant_id,
                        channel_id=channel_id,
                        entity_type="candidate",
                        entity_id=row.id,
                        model_name=provider.embedding_model_name or provider.model_name,
                        vector=vector,
                        text_hash=fingerprint_text(embedding_text),
                    )
                    similar = find_similar_embeddings(
                        self._session,
                        tenant_id=tenant_id,
                        channel_id=channel_id,
                        vector=vector,
                        entity_type="content_item",
                        top_k=3,
                    )
                    if similar and similar[0]["score"] >= 0.85:
                        row.dedup_summary = (
                            f"embedding near-duplicate with content_item {similar[0]['entity_id']} "
                            f"(score={similar[0]['score']:.2f})"
                        )
                        draft = json.loads(row.risk_flags_json) if row.risk_flags_json else []
                        if "embedding_duplicate" not in draft:
                            draft.append("embedding_duplicate")
                        row.risk_flags_json = json.dumps(draft, ensure_ascii=True)
                        append_run_step(
                            self._session,
                            agent_run_id=run.id,
                            tenant_id=tenant_id,
                            step_name="embedding_duplicate_detected",
                            status="warning",
                            output_payload={
                                "candidate_id": row.id,
                                "matched_entity_id": similar[0]["entity_id"],
                                "similarity_score": similar[0]["score"],
                            },
                        )
                scores_payload = json.loads(row.scores_json) if row.scores_json else {}
                source_bundle_payload = json.loads(row.source_bundle_json) if row.source_bundle_json else {}
                if not isinstance(source_bundle_payload, dict):
                    source_bundle_payload = {"items": source_bundle_payload}
                if isinstance(scores_payload, dict):
                    scores_payload.setdefault("source_count", int(candidate.get("source_quality_summary", {}).get("domains") and len(candidate.get("source_quality_summary", {}).get("domains")) or 0))
                    matched_angle = extract_candidate_angle(candidate)
                    angle_pressure = historical_angle_pressure(
                        self._session,
                        tenant_id=tenant_id,
                        channel_id=channel_id,
                        matched_angle=matched_angle,
                        exclude_candidate_id=row.id,
                    )
                    scores_payload["angle_pressure"] = float(angle_pressure["pressure"])
                    scores_payload["angle_recent_match_count"] = int(angle_pressure["recent_match_count"])
                    if matched_angle:
                        source_bundle_payload["matched_angle"] = matched_angle
                        source_bundle_payload["matched_angle_family"] = canonical_angle_family(matched_angle)
                    source_bundle_payload["angle_pressure"] = angle_pressure
                    if float(angle_pressure["pressure"]) >= 0.35:
                        risk_flags_payload = json.loads(row.risk_flags_json) if row.risk_flags_json else []
                        if isinstance(risk_flags_payload, list) and "repeated_angle" not in risk_flags_payload:
                            risk_flags_payload.append("repeated_angle")
                            row.risk_flags_json = json.dumps(risk_flags_payload, ensure_ascii=True)
                    row.scores_json = json.dumps(scores_payload, ensure_ascii=True)
                    row.source_bundle_json = json.dumps(source_bundle_payload, ensure_ascii=True)
                risk_flags_payload = json.loads(row.risk_flags_json) if row.risk_flags_json else []
                if mode == "topic_scout" and normalized_search_image_mode == "generate":
                    draft_payload = json.loads(row.draft_json) if row.draft_json else {}
                    if isinstance(draft_payload, dict):
                        draft_payload.setdefault(
                            "image_generation_request",
                            {"target": "media", "source": state.get("search_image_mode_source") or "task"},
                        )
                        row.draft_json = json.dumps(draft_payload, ensure_ascii=True)
                    if isinstance(risk_flags_payload, list) and "image_generation_requested" not in risk_flags_payload:
                        risk_flags_payload.append("image_generation_requested")
                        row.risk_flags_json = json.dumps(risk_flags_payload, ensure_ascii=True)
                saved_candidates.append(
                    {
                        "candidate_id": row.id,
                        "headline": row.headline,
                        "topic": row.topic,
                        "status": row.status,
                    }
                )
                if record_editor_timeline:
                    append_agent_editor_candidate_ready(
                        self._session,
                        tenant_id=tenant_id,
                        content_item_id=content_item_id,
                        agent_run_id=run.id,
                        headline=row.headline,
                        topic=row.topic,
                    )
                if policy.requires_review:
                    if mode == "post_copilot" and content_item_id and policy.mode == "draft_approval":
                        materialization = materialize_candidate_on_approval(
                            self._session,
                            tenant_id=tenant_id,
                            candidate=row,
                            policy=policy,
                            requester_user_id=agent_task.created_by if agent_task else None,
                        )
                        auto_materialized.append({"candidate_id": row.id, **materialization})
                        append_run_step(
                            self._session,
                            agent_run_id=run.id,
                            tenant_id=tenant_id,
                            step_name="candidate_auto_materialized",
                            status="ok",
                            output_payload={"candidate_id": row.id, **materialization},
                        )
                        continue
                    review_payload = build_review_payload(row, autonomy_mode=policy.mode)
                    review_payload["policy_resolution"] = policy_resolution
                    review_row = create_review_queue_item(
                        self._session,
                        tenant_id=tenant_id,
                        channel_id=channel_id,
                        agent_run_id=run.id,
                        candidate_id=row.id,
                        review_payload=review_payload,
                    )
                    review_items.append(
                        {
                            "review_item_id": review_row.id,
                            "candidate_id": row.id,
                            "status": review_row.status,
                        }
                    )
                    append_run_step(
                        self._session,
                        agent_run_id=run.id,
                        tenant_id=tenant_id,
                        step_name="review_item_created",
                        status="ok",
                        output_payload={
                            "review_item_id": review_row.id,
                            "candidate_id": row.id,
                        },
                    )
                    log_review_item_created(
                        review_row.id,
                        candidate_id=row.id,
                        tenant_id=tenant_id,
                        channel_id=channel_id,
                    )
                    inc_agent_review_item_created()
                    suggested_action = review_payload.get("suggested_review_action")
                    suggested_decision = review_payload.get("suggested_decision")
                    allowed_auto_actions = set(policy.auto_resolve_review_actions or ())
                    preset = review_payload.get("workflow_preset")
                    if isinstance(preset, str) and preset in policy.auto_resolve_review_actions_by_preset:
                        allowed_auto_actions.update(policy.auto_resolve_review_actions_by_preset.get(preset, ()))
                    if (
                        isinstance(suggested_action, str)
                        and suggested_action in allowed_auto_actions
                        and isinstance(suggested_decision, str)
                        and suggested_decision in {"approved", "rejected"}
                    ):
                        resolved_row, materialization = resolve_review_item(
                            self._session,
                            tenant_id=tenant_id,
                            review_item_id=review_row.id,
                            decision=suggested_decision,
                            decision_payload={
                                "reviewer_id": "system:auto",
                                "note": "auto-resolved by workflow preset policy",
                                "review_action": suggested_action,
                                "auto_resolved": True,
                                "workflow_preset": preset,
                            },
                        )
                        review_items[-1]["status"] = resolved_row.status
                        review_items[-1]["auto_resolved"] = True
                        if materialization:
                            auto_materialized.append({"candidate_id": row.id, **materialization})
                        append_run_step(
                            self._session,
                            agent_run_id=run.id,
                            tenant_id=tenant_id,
                            step_name="review_item_auto_resolved",
                            status="ok",
                            output_payload={
                                "review_item_id": review_row.id,
                                "candidate_id": row.id,
                                "decision": suggested_decision,
                                "review_action": suggested_action,
                            },
                        )
                else:
                    guardrail = evaluate_policy_guardrails(
                        policy,
                        scores=scores_payload,
                        risk_flags=risk_flags_payload if isinstance(risk_flags_payload, list) else [],
                    )
                    if guardrail["blocked"]:
                        guardrail_blocks.append(
                            {
                                "candidate_id": row.id,
                                "reasons": guardrail["reasons"],
                            }
                        )
                        if mode == "post_copilot":
                            append_run_step(
                                self._session,
                                agent_run_id=run.id,
                                tenant_id=tenant_id,
                                step_name="auto_publish_guardrail_noted",
                                status="warning",
                                output_payload={
                                    "candidate_id": row.id,
                                    "reasons": guardrail["reasons"],
                                },
                            )
                            materialization = materialize_candidate_on_approval(
                                self._session,
                                tenant_id=tenant_id,
                                candidate=row,
                                policy=policy,
                                requester_user_id=agent_task.created_by if agent_task else None,
                            )
                            auto_materialized.append(
                                {
                                    "candidate_id": row.id,
                                    "guardrail_reasons": guardrail["reasons"],
                                    **materialization,
                                }
                            )
                            append_run_step(
                                self._session,
                                agent_run_id=run.id,
                                tenant_id=tenant_id,
                                step_name="candidate_auto_materialized",
                                status="ok",
                                output_payload={
                                    "candidate_id": row.id,
                                    "guardrail_reasons": guardrail["reasons"],
                                    **materialization,
                                },
                            )
                        else:
                            review_payload = build_review_payload(row, autonomy_mode=policy.mode)
                            review_payload["guardrail_blocked"] = True
                            review_payload["guardrail_reasons"] = guardrail["reasons"]
                            review_payload["policy_resolution"] = policy_resolution
                            review_row = create_review_queue_item(
                                self._session,
                                tenant_id=tenant_id,
                                channel_id=channel_id,
                                agent_run_id=run.id,
                                candidate_id=row.id,
                                review_payload=review_payload,
                            )
                            review_items.append(
                                {
                                    "review_item_id": review_row.id,
                                    "candidate_id": row.id,
                                    "status": review_row.status,
                                }
                            )
                            append_run_step(
                                self._session,
                                agent_run_id=run.id,
                                tenant_id=tenant_id,
                                step_name="auto_publish_guardrail_blocked",
                                status="warning",
                                output_payload={
                                    "candidate_id": row.id,
                                    "reasons": guardrail["reasons"],
                                },
                            )
                            log_review_item_created(
                                review_row.id,
                                candidate_id=row.id,
                                tenant_id=tenant_id,
                                channel_id=channel_id,
                            )
                            inc_agent_review_item_created()
                    else:
                        materialization = materialize_candidate_on_approval(
                            self._session,
                            tenant_id=tenant_id,
                            candidate=row,
                            policy=policy,
                            requester_user_id=agent_task.created_by if agent_task else None,
                        )
                        auto_materialized.append({"candidate_id": row.id, **materialization})
                        append_run_step(
                            self._session,
                            agent_run_id=run.id,
                            tenant_id=tenant_id,
                            step_name="candidate_auto_materialized",
                            status="ok",
                            output_payload={"candidate_id": row.id, **materialization},
                        )
            result = {
                "agent_run_id": run.id,
                "mode": mode,
                "status": "awaiting_review" if review_items else "completed",
                "autonomy_mode": policy.mode,
                "policy_resolution": policy_resolution,
                "candidates": saved_candidates,
                "review_items": review_items,
                "source_package_review_items": source_package_review_items,
                "auto_materialized": auto_materialized,
                "guardrail_blocks": guardrail_blocks,
                "tool_trace": state.get("tool_trace") or [],
            }
            if mode == "post_copilot" and not timeline_content_item_id:
                for item in auto_materialized:
                    if not isinstance(item, dict):
                        continue
                    created_content_item_id = item.get("content_item_id")
                    if isinstance(created_content_item_id, str) and created_content_item_id.strip():
                        timeline_content_item_id = created_content_item_id.strip()
                        run.content_item_id = timeline_content_item_id
                        break
            append_run_step(
                self._session,
                agent_run_id=run.id,
                tenant_id=tenant_id,
                step_name="run_completed",
                status="ok",
                output_payload={
                    "candidate_count": len(saved_candidates),
                    "review_count": len(review_items),
                    "source_package_review_count": len(source_package_review_items),
                    "auto_materialized_count": len(auto_materialized),
                },
            )
            tool_summary = summarize_tool_trace(state.get("tool_trace") or [])
            duration_seconds = max((datetime.now(UTC) - run_started_at).total_seconds(), 0.0)
            mark_run_completed(
                self._session,
                run,
                trace=state.get("tool_trace") or [],
                result=result,
                review_created=bool(review_items),
                token_usage=tool_summary["usage_totals"],
            )
            if mode == "post_copilot" and timeline_content_item_id:
                if not record_editor_timeline:
                    append_agent_editor_run_started(
                        self._session,
                        tenant_id=tenant_id,
                        content_item_id=timeline_content_item_id,
                        agent_run_id=run.id,
                        user_request=user_request,
                    )
                    append_agent_editor_context_loaded(
                        self._session,
                        tenant_id=tenant_id,
                        content_item_id=timeline_content_item_id,
                        agent_run_id=run.id,
                    )
                    if saved_candidates and isinstance(saved_candidates[0], dict):
                        append_agent_editor_candidate_ready(
                            self._session,
                            tenant_id=tenant_id,
                            content_item_id=timeline_content_item_id,
                            agent_run_id=run.id,
                            headline=saved_candidates[0].get("headline"),
                            topic=saved_candidates[0].get("topic"),
                        )
                append_agent_editor_run_completed(
                    self._session,
                    tenant_id=tenant_id,
                    content_item_id=timeline_content_item_id,
                    agent_run_id=run.id,
                    result=result,
                )
            log_agent_run_completed(
                run.id,
                tenant_id=tenant_id,
                channel_id=channel_id,
                mode=mode,
                review_count=len(review_items),
                duration_ms=int(duration_seconds * 1000),
                token_usage_total=int(tool_summary["usage_totals"]["total_tokens"]),
                tool_call_count=int(tool_summary["tool_call_count"]),
                trace_policy=get_settings().agent_trace_policy,
            )
            inc_agent_run_completed(mode)
            observe_agent_run_duration_seconds(mode, duration_seconds)
            observe_agent_token_usage(mode, int(tool_summary["usage_totals"]["total_tokens"]))
            observe_agent_tool_calls(int(tool_summary["tool_call_count"]))
            self._session.flush()
            return result
        except PostbridgeError as exc:
            if record_editor_timeline:
                append_ai_chat_error(
                    self._session,
                    tenant_id=tenant_id,
                    content_item_id=content_item_id,
                    agent_run_id=run.id,
                    content="Агент не смог завершить обработку запроса.",
                    payload={"error_code": exc.code, "error_message": exc.message},
                )
            append_run_step(
                self._session,
                agent_run_id=run.id,
                tenant_id=tenant_id,
                step_name="run_failed",
                status="error",
                output_payload={"error_code": exc.code, "error_message": exc.message},
            )
            mark_run_failed(self._session, run, error_code=exc.code, error_message=exc.message)
            log_agent_run_failed(
                run.id,
                tenant_id=tenant_id,
                channel_id=channel_id,
                mode=mode,
                error_code=exc.code,
            )
            inc_agent_run_failed(mode)
            raise
        except Exception as exc:
            if record_editor_timeline:
                append_ai_chat_error(
                    self._session,
                    tenant_id=tenant_id,
                    content_item_id=content_item_id,
                    agent_run_id=run.id,
                    content="Агент завершился с внутренней ошибкой.",
                    payload={
                        "error_code": "INTERNAL_AGENT_RUN_FAILED",
                        "error_message": f"{type(exc).__name__}: {exc}",
                    },
                )
            append_run_step(
                self._session,
                agent_run_id=run.id,
                tenant_id=tenant_id,
                step_name="run_failed",
                status="error",
                output_payload={
                    "error_code": "INTERNAL_AGENT_RUN_FAILED",
                    "error_message": f"{type(exc).__name__}: {exc}",
                },
            )
            mark_run_failed(
                self._session,
                run,
                error_code="INTERNAL_AGENT_RUN_FAILED",
                error_message=f"{type(exc).__name__}: {exc}",
            )
            log_agent_run_failed(
                run.id,
                tenant_id=tenant_id,
                channel_id=channel_id,
                mode=mode,
                error_code="INTERNAL_AGENT_RUN_FAILED",
            )
            inc_agent_run_failed(mode)
            raise

    @staticmethod
    def parse_task_config(raw: str | None) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
