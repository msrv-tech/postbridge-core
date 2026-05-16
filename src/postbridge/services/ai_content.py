"""Доменные операции AI: мутация content_item / render_variant (фаза 5)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from postbridge.ai import (
    AiGatewayClient,
    GatewayGenerateRequest,
    GatewayTextResponse,
    GatewayTranslateRequest,
    gateway_response_to_warnings_json,
)
from postbridge.ai.schemas import usage_tokens_charged_for_billing
from postbridge.domain.errors import ValidationError
from postbridge.models.domain import (
    ChannelOrm,
    ContentItemOrm,
    PublicationTargetOrm,
    RenderVariantOrm,
)
from postbridge.integrations.registry import get_ai_adapter, get_platform_capabilities
from postbridge.services.ai_editor_chat import maybe_append_generate_chat_turn
from postbridge.services.postbridge_workspace_content import SOURCE_TYPE as POSTBRIDGE_POST_SOURCE_TYPE
from postbridge.services.publication_planning import create_content_with_plan_and_targets


def parse_capabilities_json(raw: str | None) -> dict[str, Any]:
    if not raw or not raw.strip():
        return {}
    try:
        o = json.loads(raw)
        return o if isinstance(o, dict) else {}
    except json.JSONDecodeError:
        return {}


def _preview_body(title: str | None, body_markdown: str | None) -> str:
    if body_markdown and body_markdown.strip():
        return body_markdown.strip()[:100_000]
    if title:
        return title.strip()
    return ""


def _latest_render_variant(
    session: Session, *, tenant_id: str, content_item_id: str, channel_id: str
) -> RenderVariantOrm | None:
    stmt = (
        select(RenderVariantOrm)
        .where(
            RenderVariantOrm.tenant_id == tenant_id,
            RenderVariantOrm.content_item_id == content_item_id,
            RenderVariantOrm.channel_id == channel_id,
        )
        .order_by(RenderVariantOrm.version.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


@dataclass(slots=True)
class AdaptTranslateResult:
    content_item_id: str
    channel_id: str
    render_variant_id: str
    previous_render_variant_id: str | None
    usage_tokens_charged: int = 1


def adapt_content_for_channel(
    session: Session,
    *,
    tenant_id: str,
    content_item_id: str,
    channel_id: str,
    client: AiGatewayClient,
    target_language: str | None = None,
    model: str | None = None,
) -> AdaptTranslateResult:
    content = session.get(ContentItemOrm, content_item_id)
    if content is None or content.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_CONTENT_ITEM_NOT_FOUND",
            message="content item not found",
            details={"content_item_id": content_item_id},
        )
    channel = session.get(ChannelOrm, channel_id)
    if channel is None or channel.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_CHANNEL_NOT_FOUND",
            message="channel not found",
            details={"channel_id": channel_id},
        )

    source_text = _preview_body(content.title, content.body_markdown)
    if not source_text:
        raise ValidationError(
            code="VALIDATION_CONTENT_EMPTY",
            message="content has no text to adapt",
            details={"content_item_id": content_item_id},
        )

    caps = parse_capabilities_json(channel.capabilities_json)
    pcaps = get_platform_capabilities(channel.platform)
    if pcaps is not None and not pcaps.ai_adapt_supported:
        raise ValidationError(
            code="VALIDATION_AI_ADAPT_NOT_SUPPORTED",
            message="AI adapt is not supported for this channel platform",
            details={"channel_id": channel_id, "platform": channel.platform},
        )
    adapter = get_ai_adapter(channel.platform)
    req = adapter.build_adapt_request(
        source_text=source_text,
        title=content.title,
        platform=channel.platform,
        target_language=target_language,
        capabilities_hint=caps,
    )
    if model:
        req = req.model_copy(update={"model": model})
    gw = adapter.post_process_adapt_response(client.adapt_for_platform(req))

    prev = _latest_render_variant(
        session, tenant_id=tenant_id, content_item_id=content_item_id, channel_id=channel_id
    )
    next_version = (prev.version + 1) if prev else 1

    rv_id = str(uuid4())
    now = datetime.now(UTC)
    session.add(
        RenderVariantOrm(
            id=rv_id,
            tenant_id=tenant_id,
            content_item_id=content_item_id,
            channel_id=channel.id,
            platform=channel.platform,
            language=target_language or content.language,
            title=gw.title,
            body_text=gw.body_text,
            body_json=gw.body_json,
            hashtags=gw.hashtags,
            mentions=gw.mentions,
            link_url=gw.link_url,
            warnings_json=gateway_response_to_warnings_json(gw),
            created_by="ai",
            version=next_version,
            created_at=now,
        )
    )
    session.flush()

    if prev is not None:
        session.execute(
            update(PublicationTargetOrm)
            .where(
                PublicationTargetOrm.tenant_id == tenant_id,
                PublicationTargetOrm.render_variant_id == prev.id,
            )
            .values(render_variant_id=rv_id)
        )

    session.flush()
    return AdaptTranslateResult(
        content_item_id=content_item_id,
        channel_id=channel_id,
        render_variant_id=rv_id,
        previous_render_variant_id=prev.id if prev else None,
        usage_tokens_charged=usage_tokens_charged_for_billing(gw),
    )


def translate_content_for_channel(
    session: Session,
    *,
    tenant_id: str,
    content_item_id: str,
    channel_id: str,
    target_language: str,
    client: AiGatewayClient,
    model: str | None = None,
) -> AdaptTranslateResult:
    content = session.get(ContentItemOrm, content_item_id)
    if content is None or content.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_CONTENT_ITEM_NOT_FOUND",
            message="content item not found",
            details={"content_item_id": content_item_id},
        )
    channel = session.get(ChannelOrm, channel_id)
    if channel is None or channel.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_CHANNEL_NOT_FOUND",
            message="channel not found",
            details={"channel_id": channel_id},
        )

    source_text = _preview_body(content.title, content.body_markdown)
    if not source_text:
        raise ValidationError(
            code="VALIDATION_CONTENT_EMPTY",
            message="content has no text to translate",
            details={"content_item_id": content_item_id},
        )

    gw = client.translate(
        GatewayTranslateRequest(
            source_text=source_text,
            title=content.title,
            target_language=target_language,
            model=model,
        )
    )

    prev = _latest_render_variant(
        session, tenant_id=tenant_id, content_item_id=content_item_id, channel_id=channel_id
    )
    next_version = (prev.version + 1) if prev else 1

    rv_id = str(uuid4())
    now = datetime.now(UTC)
    session.add(
        RenderVariantOrm(
            id=rv_id,
            tenant_id=tenant_id,
            content_item_id=content_item_id,
            channel_id=channel.id,
            platform=channel.platform,
            language=target_language,
            title=gw.title,
            body_text=gw.body_text,
            body_json=gw.body_json,
            hashtags=gw.hashtags,
            mentions=gw.mentions,
            link_url=gw.link_url,
            warnings_json=gateway_response_to_warnings_json(gw),
            created_by="ai",
            version=next_version,
            created_at=now,
        )
    )
    session.flush()

    if prev is not None:
        session.execute(
            update(PublicationTargetOrm)
            .where(
                PublicationTargetOrm.tenant_id == tenant_id,
                PublicationTargetOrm.render_variant_id == prev.id,
            )
            .values(render_variant_id=rv_id)
        )

    session.flush()
    return AdaptTranslateResult(
        content_item_id=content_item_id,
        channel_id=channel_id,
        render_variant_id=rv_id,
        previous_render_variant_id=prev.id if prev else None,
        usage_tokens_charged=usage_tokens_charged_for_billing(gw),
    )


@dataclass(slots=True)
class GenerateResult:
    content_item_id: str
    publication_plan_id: str | None
    render_variant_ids: list[str]
    publication_target_ids: list[str]
    usage_tokens_charged: int = 1


def build_gateway_generate_request(
    *,
    prompt: str | None = None,
    messages: list[dict[str, str]] | None = None,
    model: str | None = None,
    target_language: str | None = None,
) -> GatewayGenerateRequest:
    from postbridge.ai.schemas import GatewayChatMessage

    gmsgs = [GatewayChatMessage(role=m["role"], content=m["content"]) for m in messages] if messages else None
    try:
        return GatewayGenerateRequest(
            prompt=prompt,
            messages=gmsgs,
            target_language=target_language,
            model=model,
        )
    except ValueError as exc:
        raise ValidationError(
            code="VALIDATION_AI_GENERATE_INPUT",
            message=str(exc),
            details={},
        ) from exc


def public_dict_for_generate_result(session: Session, result: GenerateResult) -> dict[str, Any]:
    """Поля успешного generate для JSON и финального SSE (как у service_content_generate)."""
    out: dict[str, Any] = {
        "operation": "generate",
        "content_item_id": result.content_item_id,
        "publication_plan_id": result.publication_plan_id,
        "render_variant_ids": result.render_variant_ids,
        "publication_target_ids": result.publication_target_ids,
        "usage_tokens_charged": result.usage_tokens_charged,
    }
    ci = session.get(ContentItemOrm, result.content_item_id)
    if ci is not None:
        out["generated_title"] = ci.title
        out["generated_body_markdown"] = ci.body_markdown
    return out


def apply_generate_gateway_to_session(
    session: Session,
    *,
    tenant_id: str,
    gw: GatewayTextResponse,
    target_language: str | None = None,
    author_user_id: str | None = None,
    core_channel_ids: list[str] | None = None,
    dispatch: bool = False,
    correlation_id: str | None = None,
    content_item_id: str | None = None,
) -> GenerateResult:
    """Применяет ответ шлюза к content_item / плану (общая логика после полного текста модели)."""
    channels = core_channel_ids or []

    if content_item_id:
        if channels:
            raise ValidationError(
                code="VALIDATION_AI_GENERATE_INPUT",
                message="content_item_id refine cannot be combined with core_channel_ids",
                details={},
            )
        existing = session.get(ContentItemOrm, content_item_id)
        if existing is None or existing.tenant_id != tenant_id:
            raise ValidationError(
                code="VALIDATION_CONTENT_ITEM_NOT_FOUND",
                message="content item not found",
                details={"content_item_id": content_item_id},
            )
        now = datetime.now(UTC)
        if gw.title is not None:
            existing.title = gw.title
        if gw.body_text is not None:
            existing.body_markdown = gw.body_text
        if gw.body_json is not None:
            existing.body_structured_json = gw.body_json
        if target_language:
            existing.language = target_language
        existing.updated_at = now
        session.flush()
        stmt = select(RenderVariantOrm).where(
            RenderVariantOrm.tenant_id == tenant_id,
            RenderVariantOrm.content_item_id == content_item_id,
        )
        for rv in session.scalars(stmt).all():
            if gw.title is not None:
                rv.title = gw.title
            if gw.body_text is not None:
                rv.body_text = gw.body_text
            if gw.hashtags is not None:
                rv.hashtags = gw.hashtags
            if gw.mentions is not None:
                rv.mentions = gw.mentions
            if gw.link_url is not None:
                rv.link_url = gw.link_url
        session.flush()
        return GenerateResult(
            content_item_id=content_item_id,
            publication_plan_id=None,
            render_variant_ids=[],
            publication_target_ids=[],
            usage_tokens_charged=usage_tokens_charged_for_billing(gw),
        )

    if not channels:
        content_id = str(uuid4())
        now = datetime.now(UTC)
        session.add(
            ContentItemOrm(
                id=content_id,
                tenant_id=tenant_id,
                author_user_id=author_user_id,
                source_type=POSTBRIDGE_POST_SOURCE_TYPE,
                title=gw.title,
                body_markdown=gw.body_text,
                body_structured_json=gw.body_json,
                language=target_language,
                status="draft",
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        return GenerateResult(
            content_item_id=content_id,
            publication_plan_id=None,
            render_variant_ids=[],
            publication_target_ids=[],
            usage_tokens_charged=usage_tokens_charged_for_billing(gw),
        )

    chain = create_content_with_plan_and_targets(
        session,
        tenant_id=tenant_id,
        channel_ids=channels,
        author_user_id=author_user_id,
        source_type="ai_generated",
        title=gw.title,
        body_markdown=gw.body_text,
        body_structured_json=gw.body_json,
        language=target_language,
        content_status="draft",
        plan_strategy="immediate",
        plan_status="draft",
        target_status="pending",
    )
    # Перезаписать body_text в render variants из ответа шлюза (create_content копирует preview)
    for rv_id in chain.render_variant_ids:
        rv = session.get(RenderVariantOrm, rv_id)
        if rv is not None:
            rv.body_text = gw.body_text
            rv.title = gw.title
            rv.body_json = gw.body_json
            rv.hashtags = gw.hashtags
            rv.mentions = gw.mentions
            rv.link_url = gw.link_url
            rv.warnings_json = gateway_response_to_warnings_json(gw)
            rv.created_by = "ai"
    session.flush()

    if dispatch:
        from postbridge.workers.tasks import process_publication_target_task

        for tid in chain.publication_target_ids:
            process_publication_target_task.delay(tid, correlation_id)

    return GenerateResult(
        content_item_id=chain.content_item_id,
        publication_plan_id=chain.publication_plan_id,
        render_variant_ids=chain.render_variant_ids,
        publication_target_ids=chain.publication_target_ids,
        usage_tokens_charged=usage_tokens_charged_for_billing(gw),
    )


def generate_and_plan(
    session: Session,
    *,
    tenant_id: str,
    client: AiGatewayClient,
    prompt: str | None = None,
    messages: list[dict[str, str]] | None = None,
    model: str | None = None,
    target_language: str | None = None,
    author_user_id: str | None = None,
    core_channel_ids: list[str] | None = None,
    dispatch: bool = False,
    correlation_id: str | None = None,
    content_item_id: str | None = None,
) -> GenerateResult:
    gen_req = build_gateway_generate_request(
        prompt=prompt,
        messages=messages,
        model=model,
        target_language=target_language,
    )
    gw = client.generate_post(gen_req)
    result = apply_generate_gateway_to_session(
        session,
        tenant_id=tenant_id,
        gw=gw,
        target_language=target_language,
        author_user_id=author_user_id,
        core_channel_ids=core_channel_ids,
        dispatch=dispatch,
        correlation_id=correlation_id,
        content_item_id=content_item_id,
    )
    maybe_append_generate_chat_turn(
        session,
        tenant_id=tenant_id,
        content_item_id=result.content_item_id,
        flat_messages=messages,
        gw=gw,
    )
    return result
