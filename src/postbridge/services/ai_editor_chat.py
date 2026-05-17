"""AI editor chat persistence, with messages attached to a content_item."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from postbridge.ai.json_generate_reply import last_user_message_text
from postbridge.ai.schemas import GatewayTextResponse
from postbridge.models.domain import ContentItemAiChatMessageOrm, ContentItemOrm

MAX_CONTENT_CHARS = 100_000
MAX_MESSAGES_PER_POST = 500


def _truncate(text: str) -> str:
    if len(text) <= MAX_CONTENT_CHARS:
        return text
    return text[: MAX_CONTENT_CHARS - 20] + "\n…(truncated)"


def _ordered_chat_stmt(*, tenant_id: str, content_item_id: str):
    return (
        select(ContentItemAiChatMessageOrm)
        .where(
            ContentItemAiChatMessageOrm.tenant_id == tenant_id,
            ContentItemAiChatMessageOrm.content_item_id == content_item_id,
        )
        .order_by(ContentItemAiChatMessageOrm.created_at.asc(), ContentItemAiChatMessageOrm.id.asc())
    )


def list_ai_chat_messages(
    session: Session, *, tenant_id: str, content_item_id: str
) -> list[dict[str, str]]:
    rows = list(session.scalars(_ordered_chat_stmt(tenant_id=tenant_id, content_item_id=content_item_id)).all())
    return [
        {
            "role": row.role,
            "content": row.content,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
        if row.role in {"user", "assistant"} and row.kind in {"message", "result"}
    ]


def list_ai_chat_events(
    session: Session, *, tenant_id: str, content_item_id: str
) -> list[dict[str, str | dict | None]]:
    rows = list(session.scalars(_ordered_chat_stmt(tenant_id=tenant_id, content_item_id=content_item_id)).all())
    events: list[dict[str, str | dict | None]] = []
    for row in rows:
        payload: dict | None = None
        if row.payload_json:
            try:
                loaded = json.loads(row.payload_json)
                if isinstance(loaded, dict):
                    payload = loaded
            except json.JSONDecodeError:
                payload = None
        events.append(
            {
                "id": row.id,
                "agent_run_id": row.agent_run_id,
                "role": row.role,
                "kind": row.kind,
                "status": row.status,
                "content": row.content,
                "payload": payload,
                "created_at": row.created_at.isoformat(),
            }
        )
    return events


def delete_ai_chat_messages(session: Session, *, tenant_id: str, content_item_id: str) -> int:
    res = session.execute(
        delete(ContentItemAiChatMessageOrm).where(
            ContentItemAiChatMessageOrm.tenant_id == tenant_id,
            ContentItemAiChatMessageOrm.content_item_id == content_item_id,
        )
    )
    return res.rowcount or 0


def _trim_excess_messages(session: Session, *, tenant_id: str, content_item_id: str) -> None:
    total = session.scalar(
        select(func.count())
        .select_from(ContentItemAiChatMessageOrm)
        .where(
            ContentItemAiChatMessageOrm.tenant_id == tenant_id,
            ContentItemAiChatMessageOrm.content_item_id == content_item_id,
        )
    )
    if total is None or total <= MAX_MESSAGES_PER_POST:
        return
    excess = int(total) - MAX_MESSAGES_PER_POST
    if excess <= 0:
        return
    oldest_ids = list(
        session.scalars(
            select(ContentItemAiChatMessageOrm.id)
            .where(
                ContentItemAiChatMessageOrm.tenant_id == tenant_id,
                ContentItemAiChatMessageOrm.content_item_id == content_item_id,
            )
            .order_by(ContentItemAiChatMessageOrm.created_at.asc(), ContentItemAiChatMessageOrm.id.asc())
            .limit(excess)
        ).all()
    )
    if not oldest_ids:
        return
    session.execute(
        delete(ContentItemAiChatMessageOrm).where(ContentItemAiChatMessageOrm.id.in_(oldest_ids))
    )


def append_ai_chat_event(
    session: Session,
    *,
    tenant_id: str,
    content_item_id: str,
    role: str,
    content: str,
    kind: str = "message",
    status: str | None = "done",
    agent_run_id: str | None = None,
    payload: dict | None = None,
) -> None:
    session.add(
        ContentItemAiChatMessageOrm(
            id=str(uuid4()),
            tenant_id=tenant_id,
            content_item_id=content_item_id,
            agent_run_id=agent_run_id,
            role=role,
            kind=kind,
            status=status,
            content=_truncate(content.strip()),
            payload_json=json.dumps(payload, ensure_ascii=True) if payload else None,
            created_at=datetime.now(UTC),
        )
    )
    _trim_excess_messages(session, tenant_id=tenant_id, content_item_id=content_item_id)


def append_ai_chat_user_message(
    session: Session,
    *,
    tenant_id: str,
    content_item_id: str,
    content: str,
    agent_run_id: str | None = None,
) -> None:
    append_ai_chat_event(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
        role="user",
        content=content,
        kind="message",
        status="done",
        agent_run_id=agent_run_id,
    )


def append_ai_chat_assistant_message(
    session: Session,
    *,
    tenant_id: str,
    content_item_id: str,
    content: str,
    agent_run_id: str | None = None,
) -> None:
    append_ai_chat_event(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
        role="assistant",
        content=content,
        kind="message",
        status="done",
        agent_run_id=agent_run_id,
    )


def append_ai_chat_action(
    session: Session,
    *,
    tenant_id: str,
    content_item_id: str,
    content: str,
    status: str = "done",
    agent_run_id: str | None = None,
    payload: dict | None = None,
) -> None:
    append_ai_chat_event(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
        role="system",
        content=content,
        kind="action",
        status=status,
        agent_run_id=agent_run_id,
        payload=payload,
    )


def append_ai_chat_result(
    session: Session,
    *,
    tenant_id: str,
    content_item_id: str,
    content: str,
    agent_run_id: str | None = None,
    payload: dict | None = None,
) -> None:
    append_ai_chat_event(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
        role="assistant",
        content=content,
        kind="result",
        status="done",
        agent_run_id=agent_run_id,
        payload=payload,
    )


def append_ai_chat_error(
    session: Session,
    *,
    tenant_id: str,
    content_item_id: str,
    content: str,
    agent_run_id: str | None = None,
    payload: dict | None = None,
) -> None:
    append_ai_chat_event(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
        role="system",
        content=content,
        kind="error",
        status="failed",
        agent_run_id=agent_run_id,
        payload=payload,
    )


def append_ai_chat_turn(
    session: Session,
    *,
    tenant_id: str,
    content_item_id: str,
    user_content: str,
    assistant_content: str,
) -> None:
    append_ai_chat_user_message(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
        content=user_content,
    )
    append_ai_chat_assistant_message(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
        content=assistant_content,
    )


def append_agent_editor_run_started(
    session: Session,
    *,
    tenant_id: str,
    content_item_id: str,
    agent_run_id: str,
    user_request: str | None,
) -> None:
    if user_request and user_request.strip():
        append_ai_chat_user_message(
            session,
            tenant_id=tenant_id,
            content_item_id=content_item_id,
            content=user_request,
            agent_run_id=agent_run_id,
        )
    append_ai_chat_action(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
        content="The agent started processing the request",
        status="running",
        agent_run_id=agent_run_id,
    )


def append_agent_editor_context_loaded(
    session: Session,
    *,
    tenant_id: str,
    content_item_id: str,
    agent_run_id: str,
) -> None:
    append_ai_chat_action(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
        content="The agent reviewed the current draft and editorial context",
        status="done",
        agent_run_id=agent_run_id,
    )


def append_agent_editor_candidate_ready(
    session: Session,
    *,
    tenant_id: str,
    content_item_id: str,
    agent_run_id: str,
    headline: str | None,
    topic: str | None,
) -> None:
    label = headline or topic or "untitled"
    append_ai_chat_action(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
        content=f"Prepared a new draft variant: {label}",
        status="done",
        agent_run_id=agent_run_id,
        payload={"headline": headline, "topic": topic},
    )


def append_agent_editor_source_package_ready(
    session: Session,
    *,
    tenant_id: str,
    content_item_id: str,
    agent_run_id: str,
    source_count: int,
    image_candidate_count: int,
) -> None:
    content = f"Prepared a source package: {source_count} sources."
    if image_candidate_count > 0:
        content = f"{content}, {image_candidate_count} images."
    append_ai_chat_action(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
        content=content,
        status="done",
        agent_run_id=agent_run_id,
        payload={
            "source_count": source_count,
            "image_candidate_count": image_candidate_count,
        },
    )


def append_agent_editor_run_completed(
    session: Session,
    *,
    tenant_id: str,
    content_item_id: str,
    agent_run_id: str,
    result: dict,
) -> None:
    review_items = result.get("review_items") or []
    source_package_review_items = result.get("source_package_review_items") or []
    auto_materialized = result.get("auto_materialized") or []
    guardrail_blocks = result.get("guardrail_blocks") or []
    candidate_label = None
    candidates = result.get("candidates") or []
    if candidates and isinstance(candidates[0], dict):
        candidate_label = candidates[0].get("headline") or candidates[0].get("topic")
    append_ai_chat_action(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
        content="The agent finished processing",
        status="done",
        agent_run_id=agent_run_id,
        payload={
            "candidate_count": len(result.get("candidates") or []),
            "review_count": len(review_items),
            "source_package_review_count": len(source_package_review_items),
            "auto_materialized_count": len(auto_materialized),
        },
    )
    if source_package_review_items:
        append_ai_chat_result(
            session,
            tenant_id=tenant_id,
            content_item_id=content_item_id,
            content="The agent prepared a source package and is waiting for approval before drafting.",
            agent_run_id=agent_run_id,
            payload={"review_items": source_package_review_items},
        )
        return
    if auto_materialized:
        materialization = auto_materialized[0]
        materialization_kind = (
            materialization.get("materialization") if isinstance(materialization, dict) else None
        )
        result_text = "The agent updated the draft and applied changes to the post."
        if materialization_kind == "updated_existing_content_item":
            result_text = "The agent updated the current post draft."
        elif materialization_kind == "created_editorial_draft_content_item":
            result_text = "The agent created a new editorial draft."
        elif materialization_kind == "created_draft_content_item":
            result_text = "The agent created a new draft."
        elif materialization_kind in {
            "created_content_plan_and_targets",
            "created_and_dispatched_content_plan_and_targets",
        }:
            result_text = "The agent created a new draft and prepared a publication plan."
        if guardrail_blocks:
            reasons: list[str] = []
            for block in guardrail_blocks:
                for reason in block.get("reasons") or []:
                    value = str(reason).strip()
                    if value and value not in reasons:
                        reasons.append(value)
            if reasons:
                result_text = f"{result_text} Notes: {'; '.join(reasons)}."
        if candidate_label:
            result_text = f"{result_text} Variant: {candidate_label}."
        append_ai_chat_result(
            session,
            tenant_id=tenant_id,
            content_item_id=content_item_id,
            content=result_text,
            agent_run_id=agent_run_id,
            payload=materialization if isinstance(materialization, dict) else None,
        )
        return
    if review_items:
        result_text = "The agent prepared a new draft variant and sent it for review."
        if candidate_label:
            result_text = f"{result_text} Variant: {candidate_label}."
        append_ai_chat_result(
            session,
            tenant_id=tenant_id,
            content_item_id=content_item_id,
            content=result_text,
            agent_run_id=agent_run_id,
            payload={"review_items": review_items},
        )
        return
    if result.get("candidates"):
        result_text = "The agent prepared a new draft variant."
        if candidate_label:
            result_text = f"{result_text} Variant: {candidate_label}."
        append_ai_chat_result(
            session,
            tenant_id=tenant_id,
            content_item_id=content_item_id,
            content=result_text,
            agent_run_id=agent_run_id,
            payload={"candidates": result.get("candidates") or []},
        )


def append_agent_editor_source_package_resolved(
    session: Session,
    *,
    tenant_id: str,
    content_item_id: str,
    agent_run_id: str | None,
    decision: str,
    reviewer_id: str | None,
    note: str | None,
    follow_up_run_id: str | None = None,
) -> None:
    actor = reviewer_id or "reviewer"
    action_text = (
        "The editor approved the source package"
        if decision == "approved"
        else "The editor rejected the source package"
    )
    payload = {
        "decision": decision,
        "reviewer_id": reviewer_id,
        "note": note,
        "follow_up_run_id": follow_up_run_id,
    }
    append_ai_chat_action(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
        content=action_text,
        status="done",
        agent_run_id=agent_run_id,
        payload=payload,
    )
    result_text = f"Source package decision: {decision}. Actor: {actor}."
    if note:
        result_text = f"{result_text} Comment: {note}."
    if decision == "approved" and follow_up_run_id:
        result_text = f"{result_text} The agent continued with the approved sources."
    append_ai_chat_result(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
        content=result_text,
        agent_run_id=agent_run_id,
        payload=payload,
    )


def append_agent_editor_review_resolved(
    session: Session,
    *,
    tenant_id: str,
    content_item_id: str,
    agent_run_id: str | None,
    decision: str,
    reviewer_id: str | None,
    review_action: str | None,
    note: str | None,
    materialization: dict | None = None,
) -> None:
    actor = reviewer_id or "reviewer"
    if decision == "approved":
        action_text = "The editor approved the agent variant"
    else:
        action_text = "The editor rejected the agent variant"
    append_ai_chat_action(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
        content=action_text,
        status="done",
        agent_run_id=agent_run_id,
        payload={
            "decision": decision,
            "reviewer_id": reviewer_id,
            "review_action": review_action,
            "note": note,
        },
    )
    result_text = (
        f"Variant decision: {decision}. Actor: {actor}."
    )
    if review_action:
        result_text = f"{result_text} Action: {review_action}."
    if note:
        result_text = f"{result_text} Comment: {note}."
    append_ai_chat_result(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
        content=result_text,
        agent_run_id=agent_run_id,
        payload={
            "decision": decision,
            "reviewer_id": reviewer_id,
            "review_action": review_action,
            "note": note,
        },
    )
    if decision != "approved" or not materialization:
        return
    materialization_kind = materialization.get("materialization") if isinstance(materialization, dict) else None
    materialization_text = "The approved variant was applied to the draft."
    if materialization_kind == "updated_existing_content_item":
        materialization_text = "The approved variant was applied to the current draft."
    elif materialization_kind == "created_editorial_draft_content_item":
        materialization_text = "A new editorial draft was created from the approved variant."
    elif materialization_kind == "created_draft_content_item":
        materialization_text = "A new draft was created from the approved variant."
    elif materialization_kind in {
        "created_content_plan_and_targets",
        "created_and_dispatched_content_plan_and_targets",
    }:
        materialization_text = "The approved variant was materialized into a draft and publication plan."
    append_ai_chat_result(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
        content=materialization_text,
        agent_run_id=agent_run_id,
        payload=materialization,
    )


def maybe_append_generate_chat_turn(
    session: Session,
    *,
    tenant_id: str,
    content_item_id: str | None,
    flat_messages: list[dict[str, str]] | None,
    gw: GatewayTextResponse,
) -> None:
    if not content_item_id or not flat_messages:
        return
    user_t = last_user_message_text(flat_messages)
    if not user_t:
        return
    assistant = (gw.source_assistant_text or gw.body_text or "").strip()
    if not assistant:
        assistant = "(empty assistant reply)"
    append_ai_chat_turn(
        session,
        tenant_id=tenant_id,
        content_item_id=content_item_id,
        user_content=user_t,
        assistant_content=assistant,
    )


def require_content_item_for_tenant(
    session: Session, *, tenant_id: str, content_item_id: str
) -> ContentItemOrm:
    row = session.get(ContentItemOrm, content_item_id)
    if row is None or row.tenant_id != tenant_id:
        from postbridge.domain.errors import ValidationError

        raise ValidationError(
            code="VALIDATION_CONTENT_ITEM_NOT_FOUND",
            message="content item not found",
            details={"content_item_id": content_item_id},
        )
    return row
