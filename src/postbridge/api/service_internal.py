"""Internal service API для вызовов из SaaS BFF (CORE_SERVICE_TOKEN + X-Tenant-Id)."""

from __future__ import annotations

import json
import os
from typing import Any
import secrets
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from postbridge.api.schemas import (
    BatchImportRunResponse,
    CreateBatchImportRunRequest,
    ErrorEnvelope,
    JobMetrics,
)
from postbridge.api.service_auth import require_service_tenant
from postbridge.ai.factory import get_ai_gateway_client
from postbridge.config import get_settings
from postbridge.db import AiServiceIdempotencyOrm, get_db_session
from postbridge.domain.errors import InternalError, PostbridgeError, ValidationError
from postbridge.integrations.registry import adapt_post_dict_for_platform, platform_capabilities_public_map
from postbridge.infrastructure.crypto.credentials import encrypt_credential_secret
from postbridge.domain.models import BatchImportRun
from postbridge.models.domain import (
    BridgeOrm,
    ChannelCredentialOrm,
    ChannelOrm,
    ContentItemOrm,
    MediaGenerationJobOrm,
    PublicationTargetOrm,
    RssFeedOrm,
    TenantOrm,
)
from postbridge.observability.logging import log_job_created
from postbridge.observability.metrics import (
    inc_jobs_created,
    inc_jobs_created_idempotency_dedup,
)
from postbridge.services.bridge_adaptation import adapt_post_for_bridge
from postbridge.services.ai_image_generation import build_post_image_prompt, generate_image_bytes
from postbridge.services.ai_content import (
    adapt_content_for_channel,
    apply_generate_gateway_to_session,
    build_gateway_generate_request,
    generate_and_plan,
    public_dict_for_generate_result,
    translate_content_for_channel,
)
from postbridge.services.ai_editor_chat import (
    delete_ai_chat_messages,
    list_ai_chat_events,
    list_ai_chat_messages,
    maybe_append_generate_chat_turn,
    require_content_item_for_tenant,
)
from postbridge.services.postbridge_workspace_content import (
    POSTBRIDGE_SCHEDULE_UNSET,
    content_item_to_api_dict,
    create_postbridge_content_item,
    delete_postbridge_content_item,
    get_postbridge_content_item,
    list_postbridge_content_items,
    update_postbridge_content_item,
)
from postbridge.services.media_assets import store_media_asset
from postbridge.services.publication_planning import create_content_with_plan_and_targets
from postbridge.storage.batch_import_run_store import BatchImportRunStore
from postbridge.workers.tasks import process_batch_import_run_task, process_publication_target_task
from postbridge.workers.media_generation_tasks import process_media_generation_job_task

router = APIRouter()


def _build_batch_import_run_response(
    run: BatchImportRun,
    *,
    fetched_posts_count: int | None = None,
) -> BatchImportRunResponse:
    duration_ms = None
    if run.started_at is not None and run.completed_at is not None:
        duration_ms = int((run.completed_at - run.started_at).total_seconds() * 1000)

    error: ErrorEnvelope | None = None
    if run.error_code and run.error_message and run.error_source and run.error_retryable is not None:
        error = ErrorEnvelope(
            code=run.error_code,
            message=run.error_message,
            details=run.error_details or {},
            source=run.error_source,
            retryable=run.error_retryable,
            correlation_id=run.correlation_id or "unknown",
        )

    return BatchImportRunResponse(
        id=run.id,
        idempotency_key=run.idempotency_key,
        source_channel=run.source_channel,
        target_channel=run.target_channel,
        source_core_channel_id=run.source_core_channel_id,
        target_core_channel_id=run.target_core_channel_id,
        status=run.status.value,
        requested_limit=run.requested_limit,
        processed_posts=run.processed_posts,
        fetched_posts_count=fetched_posts_count,
        correlation_id=run.correlation_id or "unknown",
        error=error,
        metrics=JobMetrics(duration_ms=duration_ms, retry_count=run.retry_count),
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


class TenantEnsureBody(BaseModel):
    name: str | None = Field(default=None, max_length=256)


class TenantSettingsBody(BaseModel):
    image_style_prompt: str = Field(default="", max_length=4000)


class TenantSettingsResponse(TenantSettingsBody):
    updated_at: datetime | None = None


@router.get("/internal/service/runtime-config", include_in_schema=False)
def get_service_runtime_config(
    _tenant_id: str = Depends(require_service_tenant),
) -> dict[str, object]:
    settings = get_settings()
    raw_env_locale = (os.getenv("POSTBRIDGE_DEFAULT_LOCALE") or "").strip().lower()
    default_locale = (settings.postbridge_default_locale or "").strip().lower() or "en"
    return {
        "default_locale": default_locale,
        "locale_locked": raw_env_locale == "ru",
    }


@router.post("/internal/service/tenants/ensure", include_in_schema=False)
def ensure_tenant(
    body: TenantEnsureBody,
    request: Request,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    now = datetime.now(UTC)
    row = session.get(TenantOrm, tenant_id)
    if row is None:
        session.add(TenantOrm(id=tenant_id, name=body.name))
    elif body.name is not None:
        row.name = body.name
        row.updated_at = now
    session.commit()
    return {"tenant_id": tenant_id, "status": "ok"}


@router.get(
    "/internal/service/tenant/settings",
    response_model=TenantSettingsResponse,
    include_in_schema=False,
)
def get_tenant_settings(
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> TenantSettingsResponse:
    row = _require_tenant(session, tenant_id)
    return TenantSettingsResponse(
        image_style_prompt=row.image_style_prompt or "",
        updated_at=row.updated_at,
    )


@router.put(
    "/internal/service/tenant/settings",
    response_model=TenantSettingsResponse,
    include_in_schema=False,
)
def update_tenant_settings(
    body: TenantSettingsBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> TenantSettingsResponse:
    row = _require_tenant(session, tenant_id)
    row.image_style_prompt = (body.image_style_prompt or "").strip()
    row.updated_at = datetime.now(UTC)
    session.add(row)
    session.commit()
    session.refresh(row)
    return TenantSettingsResponse(
        image_style_prompt=row.image_style_prompt or "",
        updated_at=row.updated_at,
    )


class RuleAdaptPostTextBody(BaseModel):
    """Тело rule-based адаптации черновика поста под целевую платформу (без LLM)."""

    model_config = ConfigDict(extra="forbid")

    post: dict[str, Any]
    platform: str = Field(min_length=1, max_length=32)


class BridgeAdaptPostBody(BaseModel):
    """Bridge-level post adaptation, including optional AI mode from bridge settings."""

    model_config = ConfigDict(extra="forbid")

    post: dict[str, Any]
    platform: str = Field(min_length=1, max_length=32)
    bridge_settings: dict[str, Any] | None = None
    target_channel_id: str | None = Field(default=None, min_length=36, max_length=36)
    content_item_id: str | None = Field(default=None, min_length=36, max_length=36)


@router.post("/internal/service/platforms/adapt-post-text", include_in_schema=False)
def service_rule_adapt_post_text(
    body: RuleAdaptPostTextBody,
    tenant_id: str = Depends(require_service_tenant),
) -> dict[str, str]:
    """Склеивает/обрезает поля поста под platform; tenant обязателен для контракта BFF (без доступа к БД)."""
    _ = tenant_id
    raw = json.dumps(body.post, ensure_ascii=False)
    if len(raw) > 500_000:
        raise ValidationError(
            code="VALIDATION_REQUEST_INVALID",
            message="post payload too large",
            message_key="error.validation.post_payload_too_large",
            details={"max_bytes": 500_000},
        )
    return {"text": adapt_post_dict_for_platform(body.post, body.platform)}


@router.post("/internal/service/platforms/adapt-post-for-bridge", include_in_schema=False)
def service_adapt_post_for_bridge(
    body: BridgeAdaptPostBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Adapt a post for a bridge target using bridge settings and platform limits."""
    if body.target_channel_id:
        _assert_core_channels_in_tenant(session, tenant_id, body.target_channel_id)
    try:
        result = adapt_post_for_bridge(
            session,
            tenant_id=tenant_id,
            post=body.post,
            platform=body.platform,
            bridge_settings=body.bridge_settings,
            target_channel_id=body.target_channel_id,
            content_item_id=body.content_item_id,
        )
    except ValueError as exc:
        raise ValidationError(
            code="VALIDATION_REQUEST_INVALID",
            message=str(exc),
            message_key="error.validation.request_invalid",
            details={},
        ) from exc
    session.commit()
    return {
        "text": result.text,
        "status": result.status,
        "mode": result.mode,
        "platform": result.platform,
        "limit": result.limit,
        "fallback_used": result.fallback_used,
        "reason": result.reason,
        "run_id": result.run_id,
        "token_usage": result.token_usage or {},
    }


@router.get("/internal/service/platforms/capabilities", include_in_schema=False)
def service_list_platform_capabilities(
    tenant_id: str = Depends(require_service_tenant),
) -> dict[str, dict[str, dict[str, Any]]]:
    """Продуктовые флаги платформ из реестра; tenant обязателен для контракта BFF."""
    _ = tenant_id
    return {"platforms": platform_capabilities_public_map()}


class ChannelCredentialPayload(BaseModel):
    auth_type: str = Field(default="api_key", max_length=32)
    encrypted_secret: str | None = None
    meta_json: str | None = None
    status: str = Field(default="active", max_length=32)


class ChannelEnsureBody(BaseModel):
    channel_id: str | None = Field(default=None, min_length=36, max_length=36)
    platform: str = Field(max_length=32)
    kind: str = Field(default="destination", max_length=16)
    title: str = Field(max_length=512)
    external_id: str | None = Field(default=None, max_length=256)
    config_json: str | None = None
    capabilities_json: str | None = None
    status: str = Field(default="connected", max_length=32)
    credential: ChannelCredentialPayload | None = None


@router.post("/internal/service/channels/ensure", include_in_schema=False)
def ensure_channel(
    body: ChannelEnsureBody,
    request: Request,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    ch_id = body.channel_id or str(uuid4())
    now = datetime.now(UTC)
    tenant = session.get(TenantOrm, tenant_id)
    if tenant is None:
        raise ValidationError(
            code="VALIDATION_TENANT_NOT_FOUND",
            message="tenant does not exist; call tenants/ensure first",
            message_key="error.validation.tenant_not_found_ensure_first",
            details={"tenant_id": tenant_id},
        )
    row = session.get(ChannelOrm, ch_id)
    if row is None:
        session.add(
            ChannelOrm(
                id=ch_id,
                tenant_id=tenant_id,
                platform=body.platform,
                kind=body.kind,
                title=body.title,
                external_id=body.external_id,
                status=body.status,
                config_json=body.config_json,
                capabilities_json=body.capabilities_json,
            )
        )
    else:
        if row.tenant_id != tenant_id:
            raise ValidationError(
                code="VALIDATION_CHANNEL_TENANT_MISMATCH",
                message="channel belongs to another tenant",
                message_key="error.validation.channel_tenant_mismatch",
                details={"channel_id": ch_id},
            )
        row.platform = body.platform
        row.kind = body.kind
        row.title = body.title
        row.external_id = body.external_id
        row.status = body.status
        row.config_json = body.config_json
        row.capabilities_json = body.capabilities_json
        row.updated_at = now

    session.flush()

    if body.credential is not None:
        cred = body.credential
        stored_secret = encrypt_credential_secret(cred.encrypted_secret)
        existing = session.scalar(
            select(ChannelCredentialOrm)
            .where(
                ChannelCredentialOrm.channel_id == ch_id,
                ChannelCredentialOrm.tenant_id == tenant_id,
            )
            .limit(1)
        )
        if existing is None:
            session.add(
                ChannelCredentialOrm(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    channel_id=ch_id,
                    auth_type=cred.auth_type,
                    encrypted_secret=stored_secret,
                    meta_json=cred.meta_json,
                    status=cred.status,
                )
            )
        else:
            existing.auth_type = cred.auth_type
            existing.encrypted_secret = stored_secret
            existing.meta_json = cred.meta_json
            existing.status = cred.status
            existing.updated_at = now

    session.commit()
    return {"channel_id": ch_id, "status": "ok"}


@router.get("/internal/service/channels/lookup", include_in_schema=False)
def lookup_service_channel(
    platform: str,
    external_id: str,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    row = session.scalar(
        select(ChannelOrm)
        .where(
            ChannelOrm.tenant_id == tenant_id,
            ChannelOrm.platform == platform,
            ChannelOrm.external_id == external_id,
        )
        .limit(1)
    )
    if row is None:
        raise ValidationError(
            code="VALIDATION_CHANNEL_NOT_FOUND",
            message="channel not found for platform/external_id",
            message_key="error.validation.channel_not_found_for_platform_external_id",
            details={"platform": platform, "external_id": external_id},
        )
    return {"channel_id": row.id}


@router.get(
    "/internal/service/channels/{channel_id}/credential-json",
    include_in_schema=False,
)
def get_service_channel_credential_json(
    channel_id: str,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict:
    ch = session.get(ChannelOrm, channel_id)
    if ch is None or ch.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_CHANNEL_NOT_FOUND",
            message="channel not found",
            message_key="error.validation.channel_not_found",
            details={"channel_id": channel_id},
        )
    cred = session.scalar(
        select(ChannelCredentialOrm)
        .where(
            ChannelCredentialOrm.channel_id == channel_id,
            ChannelCredentialOrm.tenant_id == tenant_id,
        )
        .limit(1)
    )
    if cred is None:
        raise ValidationError(
            code="VALIDATION_CREDENTIALS_NOT_FOUND",
            message="no credential row for channel",
            message_key="error.validation.credentials_not_found",
            details={"channel_id": channel_id},
        )
    from postbridge.infrastructure.crypto.credentials import decode_channel_credential_raw

    raw = decode_channel_credential_raw(cred)
    if not raw.strip():
        return {"secret": {}}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValidationError(
            code="VALIDATION_CREDENTIALS_INVALID_JSON",
            message="credential secret is not valid JSON",
            message_key="error.validation.credentials_invalid_json",
            details={"channel_id": channel_id},
        )
    if not isinstance(data, dict):
        return {"secret": {}}
    return {"secret": data}


@router.delete(
    "/internal/service/channels/{channel_id}",
    status_code=204,
    include_in_schema=False,
)
def delete_service_channel(
    channel_id: str,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> Response:
    ch = session.get(ChannelOrm, channel_id)
    if ch is None or ch.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_CHANNEL_NOT_FOUND",
            message="channel not found",
            message_key="error.validation.channel_not_found",
            details={"channel_id": channel_id},
        )
    session.delete(ch)
    session.commit()
    return Response(status_code=204)


class ServicePublicationCreate(BaseModel):
    core_channel_ids: list[str] = Field(min_length=1)
    title: str | None = Field(default=None, max_length=512)
    body_markdown: str | None = None
    media_url: str | None = Field(default=None, max_length=2048)
    media_urls: list[str] | None = None
    author_user_id: str | None = Field(default=None, max_length=64)
    dispatch: bool = False


@router.post("/internal/service/publications", include_in_schema=False)
def create_service_publication(
    body: ServicePublicationCreate,
    request: Request,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict:
    correlation_id = getattr(request.state, "correlation_id", None) or "unknown"
    result = create_content_with_plan_and_targets(
        session,
        tenant_id=tenant_id,
        channel_ids=body.core_channel_ids,
        author_user_id=body.author_user_id,
        title=body.title,
        body_markdown=body.body_markdown,
        media_url=body.media_url,
        media_urls=body.media_urls,
        content_status="ready",
        plan_strategy="immediate",
        plan_status="scheduled",
        target_status="pending",
    )
    session.commit()
    dispatched: list[str] = []
    if body.dispatch:
        for tid in result.publication_target_ids:
            process_publication_target_task.delay(tid, correlation_id)
            dispatched.append(tid)
    return {
        "content_item_id": result.content_item_id,
        "publication_plan_id": result.publication_plan_id,
        "render_variant_ids": result.render_variant_ids,
        "publication_target_ids": result.publication_target_ids,
        "dispatched_target_ids": dispatched,
    }


@router.get("/internal/service/publication-targets/{target_id}", include_in_schema=False)
def get_service_publication_target(
    target_id: str,
    request: Request,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict:
    row = session.get(PublicationTargetOrm, target_id)
    if row is None or row.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_PUBLICATION_TARGET_NOT_FOUND",
            message="publication target not found",
            message_key="error.validation.publication_target_not_found",
            details={"target_id": target_id},
        )
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "publication_plan_id": row.publication_plan_id,
        "channel_id": row.channel_id,
        "platform": row.platform,
        "render_variant_id": row.render_variant_id,
        "status": row.status,
        "scheduled_at": row.scheduled_at.isoformat() if row.scheduled_at else None,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "external_post_id": row.external_post_id,
        "external_url": row.external_url,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "retry_count": row.retry_count,
    }


@router.post(
    "/internal/service/publication-targets/{target_id}/dispatch",
    include_in_schema=False,
)
def dispatch_service_publication_target(
    target_id: str,
    request: Request,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    correlation_id = getattr(request.state, "correlation_id", None)
    row = session.get(PublicationTargetOrm, target_id)
    if row is None or row.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_PUBLICATION_TARGET_NOT_FOUND",
            message="publication target not found",
            message_key="error.validation.publication_target_not_found",
            details={"target_id": target_id},
        )
    process_publication_target_task.delay(target_id, correlation_id)
    return {"status": "enqueued", "target_id": target_id}


def _ai_idempotency_lookup(session: Session, tenant_id: str, key: str) -> dict | None:
    row = session.scalar(
        select(AiServiceIdempotencyOrm).where(
            AiServiceIdempotencyOrm.tenant_id == tenant_id,
            AiServiceIdempotencyOrm.idempotency_key == key,
        )
    )
    if row is None:
        return None
    return json.loads(row.response_json)


def _require_ai_enabled() -> None:
    if not get_settings().ai_gateway_enabled:
        raise ValidationError(
            code="VALIDATION_AI_GATEWAY_DISABLED",
            message="AI gateway is disabled (set AI_GATEWAY_ENABLED=1)",
            message_key="error.validation.ai_gateway_disabled",
            details={},
        )


def _require_tenant(session: Session, tenant_id: str) -> TenantOrm:
    row = session.get(TenantOrm, tenant_id)
    if row is None:
        raise ValidationError(
            code="VALIDATION_TENANT_NOT_FOUND",
            message="tenant not found",
            message_key="error.validation.tenant_not_found",
            details={"tenant_id": tenant_id},
        )
    return row


def _effective_ai_response_language(explicit: str | None) -> str | None:
    """Язык ответа модели: из тела запроса, иначе AI_GATEWAY_DEFAULT_RESPONSE_LANGUAGE."""
    t = (explicit or "").strip()
    if t:
        return t[:16]
    d = get_settings().ai_gateway_default_response_language
    if not d:
        return None
    ds = d.strip()
    return ds[:16] if ds else None


class ServiceContentAdaptBody(BaseModel):
    channel_id: str = Field(min_length=36, max_length=36)
    target_language: str | None = Field(default=None, max_length=16)
    model: str | None = Field(default=None, max_length=128)


class ServiceContentTranslateBody(BaseModel):
    channel_id: str = Field(min_length=36, max_length=36)
    target_language: str = Field(min_length=1, max_length=16)
    model: str | None = Field(default=None, max_length=128)


class ServiceContentGenerateBody(BaseModel):
    prompt: str | None = Field(default=None, max_length=50_000)
    messages: list[dict[str, str]] | None = None
    model: str | None = Field(default=None, max_length=128)
    target_language: str | None = Field(default=None, max_length=16)
    author_user_id: str | None = Field(default=None, max_length=64)
    core_channel_ids: list[str] | None = None
    dispatch: bool = False
    content_item_id: str | None = Field(default=None, min_length=36, max_length=36)

    @model_validator(mode="after")
    def prompt_or_messages(self) -> ServiceContentGenerateBody:
        if self.content_item_id and self.core_channel_ids:
            raise ValueError("content_item_id cannot be combined with core_channel_ids")
        if self.messages:
            for m in self.messages:
                if not isinstance(m, dict):
                    raise ValueError("messages entries must be objects")
                if "role" not in m or "content" not in m:
                    raise ValueError("each message requires role and content")
            return self
        if self.prompt is not None and str(self.prompt).strip():
            return self
        raise ValueError("Either prompt or non-empty messages is required")


class ServiceMediaGenerateBody(BaseModel):
    prompt: str | None = Field(default=None, max_length=2_000)
    title: str | None = Field(default=None, max_length=512)
    summary: str | None = Field(default=None, max_length=2_000)
    content_md: str | None = Field(default=None, max_length=50_000)
    style_prompt: str | None = Field(default=None, max_length=4_000)
    model: str | None = Field(default=None, max_length=128)
    target: str = Field(default="cover", pattern="^(cover|media)$")
    content_item_id: str | None = Field(default=None, min_length=36, max_length=36)
    requester_user_id: str | None = Field(default=None, max_length=64)


class ServiceMediaGenerationJobResponse(BaseModel):
    id: str
    tenant_id: str
    requester_user_id: str | None = None
    content_item_id: str | None = None
    target: str
    status: str
    url: str | None = None
    media_asset_id: str | None = None
    prompt: str | None = None
    usage_tokens_charged: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


class ServiceMediaGenerationJobListResponse(BaseModel):
    items: list[ServiceMediaGenerationJobResponse]


def _ai_idempotency_persist(
    session: Session,
    *,
    tenant_id: str,
    idempotency_key: str,
    payload: dict,
) -> None:
    session.add(
        AiServiceIdempotencyOrm(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            response_json=json.dumps(payload, ensure_ascii=False),
        )
    )


@router.post("/internal/service/content-items/{content_id}/adapt", include_in_schema=False)
def service_content_adapt(
    content_id: str,
    body: ServiceContentAdaptBody,
    request: Request,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict:
    _require_ai_enabled()
    correlation_id = getattr(request.state, "correlation_id", None) or "unknown"
    idem = (request.headers.get("X-Idempotency-Key") or "").strip()
    if idem:
        if len(idem) > 128:
            raise ValidationError(
                code="VALIDATION_IDEMPOTENCY_KEY_INVALID",
                message="X-Idempotency-Key must be at most 128 characters",
                message_key="error.validation.idempotency_key_invalid",
                details={},
            )
        cached = _ai_idempotency_lookup(session, tenant_id, idem)
        if cached is not None:
            return cached

    client = get_ai_gateway_client()
    for _attempt in range(3):
        try:
            result = adapt_content_for_channel(
                session,
                tenant_id=tenant_id,
                content_item_id=content_id,
                channel_id=body.channel_id,
                client=client,
                target_language=_effective_ai_response_language(body.target_language),
                model=body.model,
            )
            _ut = result.usage_tokens_charged
            out = {
                "operation": "adapt",
                "content_item_id": result.content_item_id,
                "channel_id": result.channel_id,
                "render_variant_id": result.render_variant_id,
                "previous_render_variant_id": result.previous_render_variant_id,
                "usage_tokens_charged": _ut,
            }
            if idem:
                _ai_idempotency_persist(session, tenant_id=tenant_id, idempotency_key=idem, payload=out)
            session.commit()
            return out
        except IntegrityError:
            session.rollback()
            if not idem:
                raise
            cached = _ai_idempotency_lookup(session, tenant_id, idem)
            if cached is not None:
                return cached
    raise InternalError(
        "AI adapt idempotency race failed",
        details={"correlation_id": correlation_id},
    )


@router.post("/internal/service/content-items/{content_id}/translate", include_in_schema=False)
def service_content_translate(
    content_id: str,
    body: ServiceContentTranslateBody,
    request: Request,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict:
    _require_ai_enabled()
    correlation_id = getattr(request.state, "correlation_id", None) or "unknown"
    idem = (request.headers.get("X-Idempotency-Key") or "").strip()
    if idem:
        if len(idem) > 128:
            raise ValidationError(
                code="VALIDATION_IDEMPOTENCY_KEY_INVALID",
                message="X-Idempotency-Key must be at most 128 characters",
                message_key="error.validation.idempotency_key_invalid",
                details={},
            )
        cached = _ai_idempotency_lookup(session, tenant_id, idem)
        if cached is not None:
            return cached

    client = get_ai_gateway_client()
    for _attempt in range(3):
        try:
            result = translate_content_for_channel(
                session,
                tenant_id=tenant_id,
                content_item_id=content_id,
                channel_id=body.channel_id,
                target_language=body.target_language,
                client=client,
                model=body.model,
            )
            _ut = result.usage_tokens_charged
            out = {
                "operation": "translate",
                "content_item_id": result.content_item_id,
                "channel_id": result.channel_id,
                "render_variant_id": result.render_variant_id,
                "previous_render_variant_id": result.previous_render_variant_id,
                "usage_tokens_charged": _ut,
            }
            if idem:
                _ai_idempotency_persist(session, tenant_id=tenant_id, idempotency_key=idem, payload=out)
            session.commit()
            return out
        except IntegrityError:
            session.rollback()
            if not idem:
                raise
            cached = _ai_idempotency_lookup(session, tenant_id, idem)
            if cached is not None:
                return cached
    raise InternalError(
        "AI translate idempotency race failed",
        details={"correlation_id": correlation_id},
    )


@router.post("/internal/service/content-items/generate", include_in_schema=False)
def service_content_generate(
    body: ServiceContentGenerateBody,
    request: Request,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict:
    _require_ai_enabled()
    correlation_id = getattr(request.state, "correlation_id", None) or "unknown"
    idem = (request.headers.get("X-Idempotency-Key") or "").strip()
    if idem:
        if len(idem) > 128:
            raise ValidationError(
                code="VALIDATION_IDEMPOTENCY_KEY_INVALID",
                message="X-Idempotency-Key must be at most 128 characters",
                message_key="error.validation.idempotency_key_invalid",
                details={},
            )
        cached = _ai_idempotency_lookup(session, tenant_id, idem)
        if cached is not None:
            return cached

    client = get_ai_gateway_client()
    eff_lang = _effective_ai_response_language(body.target_language)
    for _attempt in range(3):
        try:
            result = generate_and_plan(
                session,
                tenant_id=tenant_id,
                prompt=body.prompt,
                messages=body.messages,
                model=body.model,
                client=client,
                target_language=eff_lang,
                author_user_id=body.author_user_id,
                core_channel_ids=body.core_channel_ids,
                dispatch=body.dispatch,
                correlation_id=correlation_id,
                content_item_id=body.content_item_id,
            )
            out = public_dict_for_generate_result(session, result)
            if idem:
                _ai_idempotency_persist(session, tenant_id=tenant_id, idempotency_key=idem, payload=out)
            session.commit()
            return out
        except IntegrityError:
            session.rollback()
            if not idem:
                raise
            cached = _ai_idempotency_lookup(session, tenant_id, idem)
            if cached is not None:
                return cached
    raise InternalError(
        "AI generate idempotency race failed",
        details={"correlation_id": correlation_id},
    )


def _sse_generate_event(obj: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


@router.post("/internal/service/content-items/generate-stream", include_in_schema=False)
def service_content_generate_stream(
    body: ServiceContentGenerateBody,
    request: Request,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> StreamingResponse:
    """Потоковая генерация (SSE). Идемпотентность по X-Idempotency-Key не поддерживается."""
    _require_ai_enabled()
    correlation_id = getattr(request.state, "correlation_id", None) or "unknown"

    def event_stream():
        eff_lang = _effective_ai_response_language(body.target_language)
        try:
            gen_req = build_gateway_generate_request(
                prompt=body.prompt,
                messages=body.messages,
                model=body.model,
                target_language=eff_lang,
            )
        except ValidationError as exc:
            yield _sse_generate_event(
                {
                    "type": "error",
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            )
            return

        client = get_ai_gateway_client()
        try:
            for ev in client.iter_generate_post(gen_req):
                et = ev.get("type")
                if et == "delta":
                    yield _sse_generate_event({"type": "delta", "text": ev.get("text") or ""})
                elif et == "complete":
                    gw = ev.get("gateway")
                    if gw is None:
                        raise InternalError(
                            "stream complete event without gateway",
                            details={"correlation_id": correlation_id},
                        )
                    result = apply_generate_gateway_to_session(
                        session,
                        tenant_id=tenant_id,
                        gw=gw,
                        target_language=eff_lang,
                        author_user_id=body.author_user_id,
                        core_channel_ids=body.core_channel_ids,
                        dispatch=body.dispatch,
                        correlation_id=correlation_id,
                        content_item_id=body.content_item_id,
                    )
                    maybe_append_generate_chat_turn(
                        session,
                        tenant_id=tenant_id,
                        content_item_id=result.content_item_id,
                        flat_messages=body.messages,
                        gw=gw,
                    )
                    done_payload = public_dict_for_generate_result(session, result)
                    done_payload["type"] = "done"
                    session.commit()
                    yield _sse_generate_event(done_payload)
        except PostbridgeError as exc:
            session.rollback()
            yield _sse_generate_event(
                {
                    "type": "error",
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            )
        except Exception as exc:
            session.rollback()
            yield _sse_generate_event(
                {
                    "type": "error",
                    "code": "INTERNAL_STREAM_FAILURE",
                    "message": "streamed generate failed",
                    "details": {
                        "correlation_id": correlation_id,
                        "exception_type": type(exc).__name__,
                    },
                }
            )

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


@router.get(
    "/internal/service/content-items/postbridge/{content_id}/ai-chat",
    include_in_schema=False,
)
def service_content_ai_chat_list(
    content_id: str,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    require_content_item_for_tenant(session, tenant_id=tenant_id, content_item_id=content_id)
    return {
        "messages": list_ai_chat_messages(session, tenant_id=tenant_id, content_item_id=content_id),
        "events": list_ai_chat_events(session, tenant_id=tenant_id, content_item_id=content_id),
    }


@router.delete(
    "/internal/service/content-items/postbridge/{content_id}/ai-chat",
    include_in_schema=False,
)
def service_content_ai_chat_clear(
    content_id: str,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    require_content_item_for_tenant(session, tenant_id=tenant_id, content_item_id=content_id)
    n = delete_ai_chat_messages(session, tenant_id=tenant_id, content_item_id=content_id)
    session.commit()
    return {"deleted": n}


# --- Batch import (M1 replacement): tenant-scoped batch_import_runs ---


@router.post(
    "/internal/service/batch-import-runs",
    response_model=BatchImportRunResponse,
    status_code=201,
    include_in_schema=False,
)
def create_service_batch_import_run(
    payload: CreateBatchImportRunRequest,
    request: Request,
    response: Response,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> BatchImportRunResponse:
    correlation_id = getattr(request.state, "correlation_id", None) or "unknown"
    idempotency_key = request.headers.get("X-Idempotency-Key") or payload.idempotency_key
    store = BatchImportRunStore(session)
    try:
        run, created = store.create_run(
            tenant_id=tenant_id,
            source_channel=payload.source_channel,
            target_channel=payload.target_channel,
            requested_limit=payload.requested_limit,
            correlation_id=correlation_id,
            target_core_channel_id=payload.target_core_channel_id,
            idempotency_key=idempotency_key,
            source_platform=payload.source_platform,
            target_platform=payload.target_platform,
            source_core_channel_id=payload.source_core_channel_id,
        )
        if created:
            log_job_created(run.id, correlation_id, idempotency_dedup=False)
            inc_jobs_created()
            try:
                process_batch_import_run_task.delay(run.id, correlation_id)
            except Exception:
                session.expire_all()
                updated = store.get_run(run.id)
                if updated:
                    return _build_batch_import_run_response(updated)
                raise
            response.status_code = 201
        else:
            log_job_created(run.id, correlation_id, idempotency_dedup=True)
            inc_jobs_created_idempotency_dedup()
            response.status_code = 200
        return _build_batch_import_run_response(run)
    except PostbridgeError:
        raise
    except Exception as exc:
        raise InternalError(
            "Failed to create batch import run",
            details={"exception_type": type(exc).__name__},
        ) from exc


@router.get("/internal/service/batch-import-runs", include_in_schema=False)
def list_service_batch_import_runs(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> list[BatchImportRunResponse]:
    store = BatchImportRunStore(session)
    try:
        runs = store.list_runs(tenant_id=tenant_id, status=status, limit=limit, offset=offset)
        return [_build_batch_import_run_response(r) for r in runs]
    except PostbridgeError:
        raise
    except Exception as exc:
        raise InternalError(
            "Failed to list batch import runs",
            details={"exception_type": type(exc).__name__},
        ) from exc


@router.get(
    "/internal/service/batch-import-runs/{run_id}",
    response_model=BatchImportRunResponse,
    include_in_schema=False,
)
def get_service_batch_import_run(
    run_id: str,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> BatchImportRunResponse:
    store = BatchImportRunStore(session)
    try:
        run = store.get_run(run_id, tenant_id=tenant_id)
        if run is None:
            raise ValidationError(
                code="VALIDATION_MIGRATION_RUN_NOT_FOUND",
                message="migration run not found",
                message_key="error.validation.migration_run_not_found",
                details={"run_id": run_id},
            )
        fetched_count = store.count_fetched_posts(run_id)
        return _build_batch_import_run_response(run, fetched_posts_count=fetched_count)
    except PostbridgeError:
        raise
    except Exception as exc:
        raise InternalError(
            "Failed to read batch import run",
            details={"run_id": run_id, "exception_type": type(exc).__name__},
        ) from exc


@router.post(
    "/internal/service/batch-import-runs/{run_id}/retry",
    response_model=BatchImportRunResponse,
    include_in_schema=False,
)
def retry_service_batch_import_run(
    run_id: str,
    request: Request,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> BatchImportRunResponse:
    correlation_id = getattr(request.state, "correlation_id", None) or "unknown"
    store = BatchImportRunStore(session)
    try:
        run = store.get_run(run_id, tenant_id=tenant_id)
        if run is None:
            raise ValidationError(
                code="VALIDATION_MIGRATION_RUN_NOT_FOUND",
                message="migration run not found",
                message_key="error.validation.migration_run_not_found",
                details={"run_id": run_id},
            )
        ok = store.retry_manual(run_id, correlation_id)
        if not ok:
            raise ValidationError(
                code="VALIDATION_BATCH_IMPORT_RUN_NOT_RETRYABLE",
                message="job is not in failed state",
                message_key="error.validation.batch_import_run_not_retryable",
                details={"run_id": run_id},
            )
        run_after = store.get_run(run_id, tenant_id=tenant_id)
        assert run_after is not None
        process_batch_import_run_task.delay(run_id, correlation_id)
        fetched_count = store.count_fetched_posts(run_id)
        return _build_batch_import_run_response(run_after, fetched_posts_count=fetched_count)
    except PostbridgeError:
        raise
    except Exception as exc:
        raise InternalError(
            "Failed to retry batch import run",
            details={"run_id": run_id, "exception_type": type(exc).__name__},
        ) from exc


class PostbridgeContentCreateBody(BaseModel):
    content_md: str = ""
    content_plain: str | None = None
    media_url: str | None = None
    media_urls: list[str] | None = None
    title: str | None = Field(default=None, max_length=512)
    summary: str | None = None
    link_url: str | None = None
    cta: str | None = None
    tags: list[str] | None = None
    author: str | None = Field(default=None, max_length=255)
    cover_image_url: str | None = None
    status: str = Field(default="draft", pattern="^(draft|published)$")
    author_user_id: str | None = Field(default=None, max_length=64)
    scheduled_publish_at: datetime | None = None
    live_sync_source_core_channel_id: str | None = Field(default=None, max_length=36)
    saas_workspace_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_content_for_status(self) -> "PostbridgeContentCreateBody":
        if self.status == "published" and not self.content_md.strip():
            raise ValueError("content_md is required for published status")
        return self


class PostbridgeContentPatchBody(BaseModel):
    model_config = {"extra": "ignore"}

    content_md: str | None = None
    content_plain: str | None = None
    media_url: str | None = None
    media_urls: list[str] | None = None
    title: str | None = Field(default=None, max_length=512)
    summary: str | None = None
    link_url: str | None = None
    cta: str | None = None
    tags: list[str] | None = None
    author: str | None = Field(default=None, max_length=255)
    cover_image_url: str | None = None
    status: str | None = Field(default=None, pattern="^(draft|published)$")
    scheduled_publish_at: datetime | None = None
    live_sync_source_core_channel_id: str | None = Field(default=None, max_length=36)
    saas_workspace_id: str | None = Field(default=None, max_length=64)


@router.post("/internal/service/content-items/postbridge", include_in_schema=False)
def create_postbridge_content(
    body: PostbridgeContentCreateBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict:
    row = create_postbridge_content_item(
        session,
        tenant_id=tenant_id,
        author_user_id=body.author_user_id,
        content_md=body.content_md,
        content_plain=body.content_plain,
        media_url=body.media_url,
        media_urls=body.media_urls,
        title=body.title,
        summary=body.summary,
        link_url=body.link_url,
        cta=body.cta,
        tags=body.tags,
        author=body.author,
        cover_image_url=body.cover_image_url,
        status=body.status,
        scheduled_publish_at=body.scheduled_publish_at,
        live_sync_source_core_channel_id=body.live_sync_source_core_channel_id,
        saas_workspace_id=body.saas_workspace_id,
    )
    session.commit()
    return content_item_to_api_dict(row)


@router.get("/internal/service/content-items/postbridge", include_in_schema=False)
def list_postbridge_content(
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    if limit < 1 or limit > 200:
        raise ValidationError(
            code="VALIDATION_INVALID_LIMIT",
            message="limit must be 1..200",
            message_key="error.validation.invalid_limit",
            details={},
        )
    if offset < 0:
        raise ValidationError(
            code="VALIDATION_INVALID_OFFSET",
            message="offset must be >= 0",
            message_key="error.validation.invalid_offset",
            details={},
        )
    rows = list_postbridge_content_items(
        session,
        tenant_id=tenant_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"items": [content_item_to_api_dict(r) for r in rows]}


@router.get(
    "/internal/service/content-items/postbridge/{content_id}",
    include_in_schema=False,
)
def get_postbridge_content(
    content_id: str,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict:
    row = get_postbridge_content_item(session, tenant_id=tenant_id, content_id=content_id)
    if row is None:
        raise ValidationError(
            code="VALIDATION_CONTENT_ITEM_NOT_FOUND",
            message="content item not found",
            message_key="error.validation.content_item_not_found",
            details={"content_id": content_id},
        )
    return content_item_to_api_dict(row)


@router.patch(
    "/internal/service/content-items/postbridge/{content_id}",
    include_in_schema=False,
)
def patch_postbridge_content(
    content_id: str,
    body: PostbridgeContentPatchBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict:
    row = get_postbridge_content_item(session, tenant_id=tenant_id, content_id=content_id)
    if row is None:
        raise ValidationError(
            code="VALIDATION_CONTENT_ITEM_NOT_FOUND",
            message="content item not found",
            message_key="error.validation.content_item_not_found",
            details={"content_id": content_id},
        )
    sched_at = (
        body.scheduled_publish_at
        if "scheduled_publish_at" in body.model_fields_set
        else POSTBRIDGE_SCHEDULE_UNSET
    )
    src_ch = (
        body.live_sync_source_core_channel_id
        if "live_sync_source_core_channel_id" in body.model_fields_set
        else POSTBRIDGE_SCHEDULE_UNSET
    )
    ws_id = (
        body.saas_workspace_id
        if "saas_workspace_id" in body.model_fields_set
        else POSTBRIDGE_SCHEDULE_UNSET
    )
    update_postbridge_content_item(
        session,
        row=row,
        content_md=body.content_md,
        content_plain=body.content_plain,
        media_url=body.media_url,
        media_urls=body.media_urls,
        title=body.title,
        summary=body.summary,
        link_url=body.link_url,
        cta=body.cta,
        tags=body.tags,
        author=body.author,
        cover_image_url=body.cover_image_url,
        status=body.status,
        scheduled_publish_at=sched_at,
        live_sync_source_core_channel_id=src_ch,
        saas_workspace_id=ws_id,
    )
    session.commit()
    return content_item_to_api_dict(row)


@router.delete(
    "/internal/service/content-items/postbridge/{content_id}",
    status_code=204,
    include_in_schema=False,
)
def delete_postbridge_content(
    content_id: str,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> Response:
    row = get_postbridge_content_item(session, tenant_id=tenant_id, content_id=content_id)
    if row is None:
        raise ValidationError(
            code="VALIDATION_CONTENT_ITEM_NOT_FOUND",
            message="content item not found",
            message_key="error.validation.content_item_not_found",
            details={"content_id": content_id},
        )
    delete_postbridge_content_item(session, row=row)
    session.commit()
    return Response(status_code=204)


@router.post("/internal/service/media/upload", include_in_schema=False)
async def service_upload_media(
    request: Request,
    file: UploadFile = File(...),
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Загрузка изображения в каноническое хранилище Core + строка media_assets."""
    _ = request
    data = await file.read()
    return store_media_asset(
        session,
        tenant_id=tenant_id,
        data=data,
        content_type=file.content_type or "",
    )


@router.post("/internal/service/media/generate", include_in_schema=False)
def service_generate_media(
    body: ServiceMediaGenerateBody,
    request: Request,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require_ai_enabled()
    correlation_id = getattr(request.state, "correlation_id", None) or "unknown"
    if not any(
        (value or "").strip()
        for value in (body.prompt, body.title, body.summary, body.content_md)
    ):
        raise ValidationError(
            code="VALIDATION_IMAGE_PROMPT_REQUIRED",
            message="Prompt or post text is required",
            details={},
        )
    tenant = _require_tenant(session, tenant_id)
    content_row = None
    if body.content_item_id:
        content_row = session.get(ContentItemOrm, body.content_item_id)
        if content_row is None or content_row.tenant_id != tenant_id:
            raise ValidationError(
                code="VALIDATION_CONTENT_ITEM_NOT_FOUND",
                message="content item not found",
                message_key="error.validation.content_item_not_found",
                details={"content_item_id": body.content_item_id},
            )
    final_prompt = build_post_image_prompt(
        user_prompt=body.prompt,
        title=body.title,
        summary=body.summary,
        content_md=body.content_md,
        style_prompt=body.style_prompt or (tenant.image_style_prompt or ""),
    )
    result = generate_image_bytes(
        final_prompt,
        model=body.model,
        correlation_id=correlation_id,
    )
    stored = store_media_asset(
        session,
        tenant_id=tenant_id,
        data=result.data,
        content_type=result.content_type,
    )
    if content_row is not None:
        if body.target == "cover":
            update_postbridge_content_item(session, row=content_row, cover_image_url=stored["url"])
        else:
            update_postbridge_content_item(
                session,
                row=content_row,
                media_url=stored["url"],
                media_urls=[stored["url"]],
                cover_image_url=stored["url"],
            )
        session.commit()
    return {
        **stored,
        "prompt": final_prompt,
        "usage_tokens_charged": result.usage_tokens_charged,
    }


def _media_generation_job_response(job: MediaGenerationJobOrm) -> ServiceMediaGenerationJobResponse:
    return ServiceMediaGenerationJobResponse(
        id=job.id,
        tenant_id=job.tenant_id,
        requester_user_id=job.requester_user_id,
        content_item_id=job.content_item_id,
        target=job.target,
        status=job.status,
        url=job.url,
        media_asset_id=job.media_asset_id,
        prompt=job.prompt,
        usage_tokens_charged=job.usage_tokens_charged,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


@router.post(
    "/internal/service/media/generation-jobs",
    response_model=ServiceMediaGenerationJobResponse,
    status_code=202,
    include_in_schema=False,
)
def create_service_media_generation_job(
    body: ServiceMediaGenerateBody,
    request: Request,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> ServiceMediaGenerationJobResponse:
    _require_ai_enabled()
    correlation_id = getattr(request.state, "correlation_id", None) or "unknown"
    if not any(
        (value or "").strip()
        for value in (body.prompt, body.title, body.summary, body.content_md)
    ):
        raise ValidationError(
            code="VALIDATION_IMAGE_PROMPT_REQUIRED",
            message="Prompt or post text is required",
            details={},
        )
    if body.content_item_id:
        row = session.get(ContentItemOrm, body.content_item_id)
        if row is None or row.tenant_id != tenant_id:
            raise ValidationError(
                code="VALIDATION_CONTENT_ITEM_NOT_FOUND",
                message="content item not found",
                message_key="error.validation.content_item_not_found",
                details={"content_item_id": body.content_item_id},
            )
    payload = body.model_dump(exclude_none=True)
    job = MediaGenerationJobOrm(
        id=str(uuid4()),
        tenant_id=tenant_id,
        requester_user_id=body.requester_user_id,
        content_item_id=body.content_item_id,
        target=body.target,
        status="pending",
        request_payload=payload,
        correlation_id=correlation_id,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    try:
        process_media_generation_job_task.delay(job.id, correlation_id)
    except Exception as exc:
        job.status = "failed"
        job.error_code = "MEDIA_GENERATION_QUEUE_FAILED"
        job.error_message = "media generation queue is unavailable"
        job.error_payload = {"exception": str(exc)}
        job.completed_at = datetime.now(UTC)
        session.add(job)
        session.commit()
        session.refresh(job)
    return _media_generation_job_response(job)


@router.get(
    "/internal/service/media/generation-jobs",
    response_model=ServiceMediaGenerationJobListResponse,
    include_in_schema=False,
)
def list_service_media_generation_jobs(
    limit: int = 10,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> ServiceMediaGenerationJobListResponse:
    safe_limit = max(1, min(25, int(limit or 10)))
    jobs = list(
        session.scalars(
            select(MediaGenerationJobOrm)
            .where(MediaGenerationJobOrm.tenant_id == tenant_id)
            .order_by(MediaGenerationJobOrm.created_at.desc())
            .limit(safe_limit)
        ).all()
    )
    return ServiceMediaGenerationJobListResponse(
        items=[_media_generation_job_response(job) for job in jobs]
    )


@router.get(
    "/internal/service/media/generation-jobs/{job_id}",
    response_model=ServiceMediaGenerationJobResponse,
    include_in_schema=False,
)
def get_service_media_generation_job(
    job_id: str,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> ServiceMediaGenerationJobResponse:
    job = session.get(MediaGenerationJobOrm, job_id)
    if job is None or job.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_MEDIA_GENERATION_JOB_NOT_FOUND",
            message="media generation job not found",
            details={"job_id": job_id},
        )
    return _media_generation_job_response(job)


# --- Bridges & RSS feeds (SaaS BFF, tenant-scoped) ---

_BRIDGE_STATUSES = frozenset({"active", "paused", "error"})
_BRIDGE_MODES = frozenset({"live_sync", "migration"})


def _assert_core_channels_in_tenant(
    session: Session, tenant_id: str, *channel_ids: str
) -> None:
    ids = [c for c in channel_ids if c]
    if not ids:
        return
    rows = list(
        session.scalars(
            select(ChannelOrm).where(
                ChannelOrm.id.in_(ids),
                ChannelOrm.tenant_id == tenant_id,
            )
        ).all()
    )
    found = {r.id for r in rows}
    missing = [i for i in ids if i not in found]
    if missing:
        raise ValidationError(
            code="VALIDATION_CHANNEL_NOT_FOUND",
            message="channel not found or wrong tenant",
            message_key="error.validation.channel_not_found_or_wrong_tenant",
            details={"channel_ids": missing},
        )


class ServiceBridgeCreateBody(BaseModel):
    saas_user_id: str = Field(min_length=1, max_length=64)
    source_channel_id: str = Field(min_length=36, max_length=36)
    target_channel_id: str = Field(min_length=36, max_length=36)
    mode: str = Field(default="live_sync", max_length=32)
    status: str = Field(default="active", max_length=32)
    settings_json: dict | None = None


class ServiceBridgePatchBody(BaseModel):
    status: str | None = Field(default=None, max_length=32)
    settings_json: dict | None = None


class ServiceBridgeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    saas_user_id: str
    source_channel_id: str
    target_channel_id: str
    status: str
    mode: str
    settings_json: dict | None
    created_at: datetime
    updated_at: datetime


class ServiceLiveSyncTargetOut(BaseModel):
    bridge_id: str
    target_channel_id: str
    platform: str
    external_id: str | None
    bridge_settings: dict | None = None


@router.post("/internal/service/bridges", include_in_schema=False)
def service_create_bridge(
    body: ServiceBridgeCreateBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> ServiceBridgeOut:
    if body.mode not in _BRIDGE_MODES:
        raise ValidationError(
            code="VALIDATION_REQUEST_INVALID",
            message="invalid mode",
            message_key="error.validation.invalid_mode",
            details={"mode": body.mode},
        )
    if body.status not in _BRIDGE_STATUSES:
        raise ValidationError(
            code="VALIDATION_REQUEST_INVALID",
            message="invalid status",
            message_key="error.validation.invalid_status",
            details={"status": body.status},
        )
    _assert_core_channels_in_tenant(
        session, tenant_id, body.source_channel_id, body.target_channel_id
    )
    now = datetime.now(UTC)
    bridge_id = uuid4().hex
    row = BridgeOrm(
        id=bridge_id,
        tenant_id=tenant_id,
        saas_user_id=body.saas_user_id,
        source_channel_id=body.source_channel_id,
        target_channel_id=body.target_channel_id,
        mode=body.mode,
        status=body.status,
        settings_json=body.settings_json,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValidationError(
            code="VALIDATION_BRIDGE_DUPLICATE",
            message="bridge already exists for this user and channel pair",
            message_key="error.validation.bridge_duplicate",
            details={},
        ) from exc
    session.refresh(row)
    return ServiceBridgeOut.model_validate(row)


@router.get("/internal/service/bridges", include_in_schema=False)
def service_list_bridges(
    saas_user_id: str = Query(..., min_length=1, max_length=64),
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, list[ServiceBridgeOut]]:
    rows = list(
        session.scalars(
            select(BridgeOrm)
            .where(
                BridgeOrm.tenant_id == tenant_id,
                BridgeOrm.saas_user_id == saas_user_id,
            )
            .order_by(BridgeOrm.created_at.asc())
        ).all()
    )
    return {"items": [ServiceBridgeOut.model_validate(r, from_attributes=True) for r in rows]}


@router.get(
    "/internal/service/bridges/live-sync-targets",
    include_in_schema=False,
)
def service_list_live_sync_targets_by_source(
    source_channel_id: str = Query(..., min_length=36, max_length=36),
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, list[ServiceLiveSyncTargetOut]]:
    _assert_core_channels_in_tenant(session, tenant_id, source_channel_id)
    bridges = list(
        session.scalars(
            select(BridgeOrm).where(
                BridgeOrm.tenant_id == tenant_id,
                BridgeOrm.source_channel_id == source_channel_id,
                BridgeOrm.status == "active",
                BridgeOrm.mode == "live_sync",
            )
        ).all()
    )
    out: list[ServiceLiveSyncTargetOut] = []
    for b in bridges:
        tgt = session.get(ChannelOrm, b.target_channel_id)
        if tgt is None or tgt.tenant_id != tenant_id:
            continue
        out.append(
            ServiceLiveSyncTargetOut(
                bridge_id=b.id,
                target_channel_id=tgt.id,
                platform=tgt.platform,
                external_id=tgt.external_id,
                bridge_settings=b.settings_json,
            )
        )
    return {"items": out}


@router.get("/internal/service/bridges/find", include_in_schema=False)
def service_find_bridge(
    saas_user_id: str = Query(..., min_length=1, max_length=64),
    source_channel_id: str = Query(..., min_length=36, max_length=36),
    target_channel_id: str = Query(..., min_length=36, max_length=36),
    mode: str = Query(default="live_sync", max_length=32),
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, ServiceBridgeOut | None]:
    if mode not in _BRIDGE_MODES:
        raise ValidationError(
            code="VALIDATION_REQUEST_INVALID",
            message="invalid mode",
            message_key="error.validation.invalid_mode",
            details={"mode": mode},
        )
    row = session.scalar(
        select(BridgeOrm).where(
            BridgeOrm.tenant_id == tenant_id,
            BridgeOrm.saas_user_id == saas_user_id,
            BridgeOrm.source_channel_id == source_channel_id,
            BridgeOrm.target_channel_id == target_channel_id,
            BridgeOrm.mode == mode,
        )
    )
    if row is None:
        return {"bridge": None}
    return {"bridge": ServiceBridgeOut.model_validate(row, from_attributes=True)}


@router.get("/internal/service/bridges/live-sync-count", include_in_schema=False)
def service_count_live_sync_bridges(
    saas_user_id: str = Query(..., min_length=1, max_length=64),
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, int]:
    count = session.scalar(
        select(func.count())
        .select_from(BridgeOrm)
        .where(
            BridgeOrm.tenant_id == tenant_id,
            BridgeOrm.saas_user_id == saas_user_id,
            BridgeOrm.mode == "live_sync",
            BridgeOrm.status == "active",
        )
    )
    return {"count": int(count or 0)}


@router.get("/internal/service/bridges/{bridge_id}", include_in_schema=False)
def service_get_bridge(
    bridge_id: str,
    saas_user_id: str = Query(..., min_length=1, max_length=64),
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> ServiceBridgeOut:
    row = session.scalar(
        select(BridgeOrm).where(
            BridgeOrm.id == bridge_id,
            BridgeOrm.tenant_id == tenant_id,
            BridgeOrm.saas_user_id == saas_user_id,
        )
    )
    if row is None:
        raise ValidationError(
            code="VALIDATION_BRIDGE_NOT_FOUND",
            message="bridge not found",
            message_key="error.validation.bridge_not_found",
            details={"bridge_id": bridge_id},
        )
    return ServiceBridgeOut.model_validate(row)


@router.patch("/internal/service/bridges/{bridge_id}", include_in_schema=False)
def service_patch_bridge(
    bridge_id: str,
    body: ServiceBridgePatchBody,
    saas_user_id: str = Query(..., min_length=1, max_length=64),
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> ServiceBridgeOut:
    if body.status is None and "settings_json" not in body.model_fields_set:
        raise ValidationError(
            code="VALIDATION_REQUEST_INVALID",
            message="empty bridge patch",
            message_key="error.validation.empty_bridge_patch",
            details={},
        )
    if body.status is not None and body.status not in _BRIDGE_STATUSES:
        raise ValidationError(
            code="VALIDATION_REQUEST_INVALID",
            message="invalid status",
            message_key="error.validation.invalid_status",
            details={"status": body.status},
        )
    row = session.scalar(
        select(BridgeOrm).where(
            BridgeOrm.id == bridge_id,
            BridgeOrm.tenant_id == tenant_id,
            BridgeOrm.saas_user_id == saas_user_id,
        )
    )
    if row is None:
        raise ValidationError(
            code="VALIDATION_BRIDGE_NOT_FOUND",
            message="bridge not found",
            message_key="error.validation.bridge_not_found",
            details={"bridge_id": bridge_id},
        )
    if body.status is not None:
        row.status = body.status
    if "settings_json" in body.model_fields_set:
        row.settings_json = body.settings_json
    row.updated_at = datetime.now(UTC)
    session.commit()
    session.refresh(row)
    return ServiceBridgeOut.model_validate(row)


@router.delete("/internal/service/bridges/{bridge_id}", include_in_schema=False)
def service_delete_bridge(
    bridge_id: str,
    saas_user_id: str = Query(..., min_length=1, max_length=64),
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> Response:
    row = session.scalar(
        select(BridgeOrm).where(
            BridgeOrm.id == bridge_id,
            BridgeOrm.tenant_id == tenant_id,
            BridgeOrm.saas_user_id == saas_user_id,
        )
    )
    if row is None:
        raise ValidationError(
            code="VALIDATION_BRIDGE_NOT_FOUND",
            message="bridge not found",
            message_key="error.validation.bridge_not_found",
            details={"bridge_id": bridge_id},
        )
    session.delete(row)
    session.commit()
    return Response(status_code=204)


class ServiceRssFeedCreateBody(BaseModel):
    source_channel_id: str = Field(min_length=36, max_length=36)
    saas_user_id: str | None = Field(default=None, max_length=64)
    id: str | None = Field(default=None, max_length=64)
    secret_token: str | None = Field(default=None, max_length=128)


class ServiceRssFeedOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    source_channel_id: str
    secret_token: str
    saas_user_id: str | None
    created_at: datetime


@router.post("/internal/service/rss-feeds", include_in_schema=False)
def service_create_rss_feed(
    body: ServiceRssFeedCreateBody,
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> ServiceRssFeedOut:
    _assert_core_channels_in_tenant(session, tenant_id, body.source_channel_id)
    now = datetime.now(UTC)
    feed_id = (body.id or "").strip() or secrets.token_hex(16)
    if len(feed_id) > 64:
        raise ValidationError(
            code="VALIDATION_REQUEST_INVALID",
            message="feed id too long",
            message_key="error.validation.feed_id_too_long",
            details={},
        )
    secret = (body.secret_token or "").strip() or secrets.token_urlsafe(32)
    if len(secret) > 128:
        raise ValidationError(
            code="VALIDATION_REQUEST_INVALID",
            message="secret_token too long",
            message_key="error.validation.secret_token_too_long",
            details={},
        )
    row = RssFeedOrm(
        id=feed_id,
        tenant_id=tenant_id,
        source_channel_id=body.source_channel_id,
        secret_token=secret,
        saas_user_id=body.saas_user_id,
        created_at=now,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValidationError(
            code="VALIDATION_RSS_FEED_DUPLICATE",
            message="rss feed id already exists",
            message_key="error.validation.rss_feed_duplicate",
            details={"id": feed_id},
        ) from exc
    session.refresh(row)
    return ServiceRssFeedOut.model_validate(row, from_attributes=True)


@router.get("/internal/service/rss-feeds", include_in_schema=False)
def service_list_rss_feeds(
    saas_user_id: str = Query(..., min_length=1, max_length=64),
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> dict[str, list[ServiceRssFeedOut]]:
    rows = list(
        session.scalars(
            select(RssFeedOrm)
            .where(
                RssFeedOrm.tenant_id == tenant_id,
                RssFeedOrm.saas_user_id == saas_user_id,
            )
            .order_by(RssFeedOrm.created_at.desc())
        ).all()
    )
    return {"items": [ServiceRssFeedOut.model_validate(r, from_attributes=True) for r in rows]}


@router.delete("/internal/service/rss-feeds/{feed_id}", include_in_schema=False)
def service_delete_rss_feed(
    feed_id: str,
    saas_user_id: str = Query(..., min_length=1, max_length=64),
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> Response:
    row = session.scalar(
        select(RssFeedOrm).where(
            RssFeedOrm.id == feed_id,
            RssFeedOrm.tenant_id == tenant_id,
            RssFeedOrm.saas_user_id == saas_user_id,
        )
    )
    if row is None:
        raise ValidationError(
            code="VALIDATION_RSS_FEED_NOT_FOUND",
            message="rss feed not found",
            message_key="error.validation.rss_feed_not_found",
            details={"feed_id": feed_id},
        )
    session.delete(row)
    session.commit()
    return Response(status_code=204)


@router.get("/internal/service/rss-feeds/lookup", include_in_schema=False)
def service_lookup_rss_feed(
    feed_id: str = Query(..., min_length=1, max_length=64),
    secret_token: str = Query(..., min_length=1, max_length=128),
    tenant_id: str = Depends(require_service_tenant),
    session: Session = Depends(get_db_session),
) -> ServiceRssFeedOut:
    row = session.scalar(
        select(RssFeedOrm).where(
            RssFeedOrm.id == feed_id,
            RssFeedOrm.tenant_id == tenant_id,
            RssFeedOrm.secret_token == secret_token,
        )
    )
    if row is None:
        raise ValidationError(
            code="VALIDATION_RSS_FEED_NOT_FOUND",
            message="rss feed not found",
            message_key="error.validation.rss_feed_not_found",
            details={"feed_id": feed_id},
        )
    return ServiceRssFeedOut.model_validate(row, from_attributes=True)
