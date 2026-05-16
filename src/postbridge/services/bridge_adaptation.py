"""Bridge-level post adaptation before delivery to a target platform."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy.orm import Session

from postbridge.agent.providers.openai_compatible import OpenAICompatibleProvider
from postbridge.integrations.registry import (
    RULE_POST_TEXT_LIMITS,
    adapt_post_dict_for_platform,
)
from postbridge.integrations.text_rule.common import truncate_at_word
from postbridge.models.domain import AgentRunOrm

logger = logging.getLogger(__name__)

BRIDGE_ADAPT_GRAPH_NAME = "bridge_adapt"
BRIDGE_ADAPTATION_MODES = frozenset({"rule_only", "ai_auto", "ai_review"})


class BridgeAdaptationGenerator(Protocol):
    def __call__(
        self,
        *,
        post: dict[str, Any],
        platform: str,
        limit: int | None,
        instructions: str | None,
    ) -> tuple[str, dict[str, Any]]:
        ...


@dataclass(slots=True)
class BridgeAdaptationResult:
    text: str
    status: str
    mode: str
    platform: str
    limit: int | None
    fallback_used: bool = False
    reason: str | None = None
    run_id: str | None = None
    token_usage: dict[str, Any] | None = None


def _settings_dict(settings_json: Any) -> dict[str, Any]:
    if isinstance(settings_json, dict):
        return settings_json
    if isinstance(settings_json, str) and settings_json.strip():
        try:
            parsed = json.loads(settings_json)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def resolve_bridge_adaptation_mode(settings_json: Any) -> str:
    settings = _settings_dict(settings_json)
    raw = settings.get("adaptation_mode")
    nested = settings.get("adaptation")
    if isinstance(nested, dict) and nested.get("mode") is not None:
        raw = nested.get("mode")
    mode = str(raw or "rule_only").strip().lower()
    return mode if mode in BRIDGE_ADAPTATION_MODES else "rule_only"


def resolve_bridge_adaptation_instructions(settings_json: Any) -> str | None:
    settings = _settings_dict(settings_json)
    raw = settings.get("adaptation_instructions")
    nested = settings.get("adaptation")
    if isinstance(nested, dict) and nested.get("instructions") is not None:
        raw = nested.get("instructions")
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    return cleaned or None


def _default_generator(
    *,
    post: dict[str, Any],
    platform: str,
    limit: int | None,
    instructions: str | None,
) -> tuple[str, dict[str, Any]]:
    provider = OpenAICompatibleProvider.from_env()
    target_limit = None if limit is None else max(1, int(limit * 0.9))
    payload = {
        "target_platform": platform,
        "text_limit": limit,
        "target_text_length": target_limit,
        "post": post,
        "instructions": instructions,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You are Postbridge bridge_adapt, an editor that adapts a post for the target "
                "platform before delivery. Preserve facts, links, CTA, and formatting when possible. "
                "Respect text_limit as a hard maximum when it is provided: the returned text must fit "
                "within it. For tight limits, aim for target_text_length so minor counting differences "
                "do not exceed the hard maximum. Return JSON with keys: text, notes."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False),
        },
    ]
    data, usage = provider.invoke_json(messages=messages, temperature=0.2)
    text = data.get("text")
    return (text if isinstance(text, str) else "", usage)


def _normalize_agent_text(text: str, limit: int | None) -> tuple[str, str | None]:
    cleaned = "\n".join(line.rstrip() for line in text.strip().splitlines()).strip()
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")
    if limit is None or len(cleaned) <= limit:
        return cleaned, None
    return truncate_at_word(cleaned, limit).strip(), "agent_text_trimmed_to_limit"


def _record_agent_run(
    session: Session,
    *,
    tenant_id: str,
    target_channel_id: str | None,
    content_item_id: str | None,
    status: str,
    mode: str,
    platform: str,
    reason: str | None,
    token_usage: dict[str, Any] | None,
    error_message: str | None = None,
) -> str | None:
    if not target_channel_id:
        return None
    now = datetime.now(UTC)
    run_id = str(uuid4())
    trace = {
        "mode": mode,
        "platform": platform,
        "reason": reason,
    }
    session.add(
        AgentRunOrm(
            id=run_id,
            tenant_id=tenant_id,
            channel_id=target_channel_id,
            content_item_id=content_item_id,
            graph_name=BRIDGE_ADAPT_GRAPH_NAME,
            trigger_type="api",
            status=status,
            token_usage_json=json.dumps(token_usage or {}, ensure_ascii=False),
            trace_json=json.dumps(trace, ensure_ascii=False),
            error_message=error_message,
            started_at=now,
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    return run_id


def adapt_post_for_bridge(
    session: Session,
    *,
    tenant_id: str,
    post: dict[str, Any],
    platform: str,
    bridge_settings: dict[str, Any] | None = None,
    target_channel_id: str | None = None,
    content_item_id: str | None = None,
    generator: BridgeAdaptationGenerator | None = None,
) -> BridgeAdaptationResult:
    """Adapt a post according to bridge settings and target platform constraints."""
    raw = json.dumps(post, ensure_ascii=False)
    if len(raw) > 500_000:
        raise ValueError("post payload too large")

    mode = resolve_bridge_adaptation_mode(bridge_settings)
    instructions = resolve_bridge_adaptation_instructions(bridge_settings)
    limit = RULE_POST_TEXT_LIMITS.get(platform)
    rule_text = adapt_post_dict_for_platform(post, platform)

    if mode == "rule_only":
        return BridgeAdaptationResult(
            text=rule_text,
            status="ready",
            mode=mode,
            platform=platform,
            limit=limit,
        )

    effective_generator = generator or _default_generator
    try:
        ai_text, usage = effective_generator(
            post=post,
            platform=platform,
            limit=limit,
            instructions=instructions,
        )
    except Exception as exc:
        logger.warning(
            "bridge adaptation failed, falling back to rule text: tenant=%s platform=%s error=%s",
            tenant_id,
            platform,
            exc,
        )
        run_id = _record_agent_run(
            session,
            tenant_id=tenant_id,
            target_channel_id=target_channel_id,
            content_item_id=content_item_id,
            status="failed",
            mode=mode,
            platform=platform,
            reason="agent_error",
            token_usage=None,
            error_message=str(exc),
        )
        return BridgeAdaptationResult(
            text=rule_text,
            status="needs_review" if mode == "ai_review" else "ready",
            mode=mode,
            platform=platform,
            limit=limit,
            fallback_used=True,
            reason="agent_error"
            if mode != "ai_review"
            else "agent_error_requires_human_approval",
            run_id=run_id,
        )

    cleaned, normalization_reason = _normalize_agent_text(ai_text, limit)
    fallback_reason: str | None = None
    if not cleaned:
        fallback_reason = "agent_empty_text"
    elif limit is not None and len(cleaned) > limit:
        fallback_reason = "agent_text_over_limit"

    if fallback_reason:
        run_id = _record_agent_run(
            session,
            tenant_id=tenant_id,
            target_channel_id=target_channel_id,
            content_item_id=content_item_id,
            status="completed",
            mode=mode,
            platform=platform,
            reason=fallback_reason,
            token_usage=usage,
        )
        return BridgeAdaptationResult(
            text=rule_text,
            status="needs_review" if mode == "ai_review" else "ready",
            mode=mode,
            platform=platform,
            limit=limit,
            fallback_used=True,
            reason=fallback_reason
            if mode != "ai_review"
            else f"{fallback_reason}_requires_human_approval",
            run_id=run_id,
            token_usage=usage,
        )

    run_id = _record_agent_run(
        session,
        tenant_id=tenant_id,
        target_channel_id=target_channel_id,
        content_item_id=content_item_id,
        status="completed",
        mode=mode,
        platform=platform,
        reason=normalization_reason,
        token_usage=usage,
    )
    return BridgeAdaptationResult(
        text=cleaned,
        status="needs_review" if mode == "ai_review" else "ready",
        mode=mode,
        platform=platform,
        limit=limit,
        run_id=run_id,
        token_usage=usage,
        reason="ai_review_requires_human_approval"
        if mode == "ai_review"
        else normalization_reason,
    )
