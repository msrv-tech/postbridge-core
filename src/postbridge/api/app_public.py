"""Browser-safe app API for the shared Core frontend."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from postbridge.api.agent_internal import (
    AgentEditorMessageCreateBody,
    AgentPolicyUpsertBody,
    AgentRunCreateBody,
    AgentTaskCreateBody,
    ReviewResolveBody,
    create_service_agent_task,
    create_service_agent_editor_message,
    create_service_agent_run,
    delete_service_agent_task,
    get_service_agent_analytics_overview,
    get_service_agent_analytics_quality,
    get_service_agent_analytics_timeseries,
    get_service_agent_candidate,
    get_service_agent_editor_timeline,
    get_service_agent_run,
    get_service_review_queue_item,
    list_service_agent_policies,
    list_service_agent_candidates,
    list_service_agent_run_steps,
    list_service_agent_runs,
    list_service_agent_tasks,
    list_service_review_queue,
    pause_service_agent_task,
    resolve_service_review_queue_item,
    resume_service_agent_task,
    run_service_agent_task,
    upsert_service_agent_policy,
)
from postbridge.ai.factory import get_ai_gateway_client
from postbridge.config import get_settings
from postbridge.db import BatchImportFetchedPostOrm, BatchImportRunOrm, get_db_session
from postbridge.infrastructure.crypto.credentials import encrypt_credential_secret
from postbridge.integrations.registry import get_platform_capabilities
from postbridge.models.domain import (
    BridgeOrm,
    ChannelCredentialOrm,
    ChannelOrm,
    ContentItemOrm,
    MediaGenerationJobOrm,
    PublicationPlanOrm,
    PublicationTargetOrm,
    TenantOrm,
)
from postbridge.services.ai_content import (
    adapt_content_for_channel,
    generate_and_plan,
    public_dict_for_generate_result,
    translate_content_for_channel,
)
from postbridge.services.ai_editor_chat import (
    delete_ai_chat_messages,
    list_ai_chat_events,
    list_ai_chat_messages,
)
from postbridge.services.media_assets import store_media_asset
from postbridge.services.publication_planning import create_plan_and_targets_for_content_item
from postbridge.services.postbridge_workspace_content import (
    POSTBRIDGE_SCHEDULE_UNSET,
    content_item_to_api_dict,
    create_postbridge_content_item,
    delete_postbridge_content_item,
    get_postbridge_content_item,
    list_postbridge_content_items,
    update_postbridge_content_item,
)
from postbridge.storage.batch_import_run_store import BatchImportRunStore
from postbridge.workers.media_generation_tasks import process_media_generation_job_task
from postbridge.workers.tasks import process_batch_import_run_task, process_publication_target_task

router = APIRouter(prefix="/api/app")

LOCAL_ADMIN_USER_ID = "local-admin"
BRIDGE_MODES = frozenset({"live_sync", "migration"})
BRIDGE_STATUSES = frozenset({"active", "paused", "error"})


class BootstrapRequest(BaseModel):
    tenant_name: str | None = Field(default="Postbridge Self-host", max_length=256)


class ChannelCreateRequest(BaseModel):
    platform: str = Field(min_length=1, max_length=32)
    kind: str | None = Field(default=None, min_length=1, max_length=16)
    title: str = Field(min_length=1, max_length=512)
    external_id: str | None = Field(default=None, max_length=256)
    platform_channel_id: str | None = Field(default=None, max_length=256)
    status: str = Field(default="connected", min_length=1, max_length=32)
    can_read: bool | None = None
    can_write: bool | None = None
    credentials_ref: str | None = Field(default=None, max_length=256)
    config: dict[str, Any] | None = None
    capabilities: dict[str, Any] | None = None


class ChannelCredentialUpsertRequest(BaseModel):
    auth_type: str = Field(default="api_key", min_length=1, max_length=32)
    secret: dict[str, Any] | None = None
    status: str = Field(default="active", min_length=1, max_length=32)


class ChannelValidateRequest(BaseModel):
    platform: str = Field(min_length=1, max_length=32)
    platform_channel_id: str = Field(min_length=1, max_length=2048)
    role: str = Field(default="source", pattern="^(source|target)$")


class MaxVerificationRequest(BaseModel):
    platform_channel_id: str = Field(min_length=1, max_length=256)


class MaxVerificationVerifyRequest(BaseModel):
    platform_channel_id: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=1, max_length=64)


class VKCommunityTokenRequest(BaseModel):
    group_id: str = Field(min_length=1, max_length=256)
    access_token: str = Field(min_length=1, max_length=4096)


class LinkedInOrganizationsRequest(BaseModel):
    access_token: str = Field(min_length=1, max_length=4096)
    api_version: str | None = Field(default=None, max_length=32)


class LinkedInAccessTokenRequest(BaseModel):
    author_id: str = Field(min_length=1, max_length=256)
    access_token: str = Field(min_length=1, max_length=4096)
    api_version: str | None = Field(default=None, max_length=32)
    expires_at: datetime | None = None
    display: str | None = Field(default=None, max_length=512)


class BridgeCreateRequest(BaseModel):
    source_channel_id: str = Field(min_length=36, max_length=36)
    target_channel_id: str = Field(min_length=36, max_length=36)
    mode: str = Field(default="live_sync", min_length=1, max_length=32)
    status: str = Field(default="active", min_length=1, max_length=32)
    settings: dict[str, Any] | None = None


class BridgePatchRequest(BaseModel):
    status: str | None = Field(default=None, min_length=1, max_length=32)
    settings: dict[str, Any] | None = None
    adaptation_mode: str | None = Field(default=None, max_length=32)
    adaptation_instructions: str | None = None
    link_back_enabled: bool | None = None
    link_back_site_url: str | None = Field(default=None, max_length=2048)


class ConnectionCreateRequest(BaseModel):
    source_platform: str = Field(min_length=1, max_length=32)
    source_channel_id: str = Field(min_length=1, max_length=256)
    source_display: str | None = Field(default=None, max_length=512)
    target_platform: str = Field(min_length=1, max_length=32)
    target_channel_id: str | None = Field(default=None, max_length=256)
    target_display: str | None = Field(default=None, max_length=512)
    requested_limit: int = Field(default=0, ge=0, le=10_000)
    source_credentials_id: str | None = Field(default=None, max_length=128)
    target_credentials_id: str | None = Field(default=None, max_length=128)


class BatchImportStartRequest(BaseModel):
    bridge_id: str = Field(min_length=1, max_length=64)
    requested_limit: int = Field(default=20, ge=0, le=10_000)


class ContentItemCreateRequest(BaseModel):
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
    scheduled_publish_at: datetime | None = None
    live_sync_source_core_channel_id: str | None = Field(default=None, max_length=36)


class ContentItemPatchRequest(BaseModel):
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


class PublicationTargetsCreateRequest(BaseModel):
    channel_ids: list[str] = Field(min_length=1)
    dispatch: bool = False
    scheduled_at: datetime | None = None


class MediaGenerationJobCreateRequest(BaseModel):
    prompt: str | None = Field(default=None, max_length=2_000)
    title: str | None = Field(default=None, max_length=512)
    summary: str | None = Field(default=None, max_length=2_000)
    content_md: str | None = Field(default=None, max_length=50_000)
    style_prompt: str | None = Field(default=None, max_length=4_000)
    model: str | None = Field(default=None, max_length=128)
    target: str = Field(default="cover", pattern="^(cover|media)$")
    content_item_id: str | None = Field(default=None, min_length=36, max_length=36)


class ContentAdaptRequest(BaseModel):
    channel_id: str = Field(min_length=36, max_length=36)
    target_language: str | None = Field(default=None, max_length=16)
    model: str | None = Field(default=None, max_length=128)


class ContentTranslateRequest(BaseModel):
    channel_id: str = Field(min_length=36, max_length=36)
    target_language: str = Field(min_length=1, max_length=16)
    model: str | None = Field(default=None, max_length=128)


class ContentGenerateRequest(BaseModel):
    prompt: str | None = Field(default=None, max_length=50_000)
    messages: list[dict[str, str]] | None = None
    model: str | None = Field(default=None, max_length=128)
    target_language: str | None = Field(default=None, max_length=16)
    content_item_id: str | None = Field(default=None, min_length=36, max_length=36)


class WorkspaceSettingsPatchRequest(BaseModel):
    image_style_prompt: str | None = Field(default=None, max_length=4_000)


def _json_dumps_or_none(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads_or_empty(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _json_compatible_dict(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    return json.loads(json.dumps(value, ensure_ascii=False))


def _normalize_telegram_channel_id(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="Telegram channel id is required")
    match = re.search(r"(?:t\.me|telegram\.me)/(?:c/)?([^/?#]+)", value, re.IGNORECASE)
    if match:
        value = match.group(1).strip()
    if value.startswith("@") or value.lstrip("-").isdigit():
        return value
    if re.fullmatch(r"[A-Za-z0-9_]{5,}", value):
        return f"@{value}"
    raise HTTPException(status_code=400, detail="invalid Telegram channel id")


def _normalize_max_channel_id(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="MAX channel id is required")
    match = re.search(r"max\.(?:ru|com)/(?:channel/)?([^/?#]+)", value, re.IGNORECASE)
    if match:
        value = match.group(1).strip()
    return value


def _parse_vk_group_id(raw: str) -> int:
    value = (raw or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="VK group id is required")
    if re.fullmatch(r"-?\d+", value):
        return abs(int(value))
    match = re.search(r"vk\.(?:com|ru)/(?:club|public)(\d+)", value, re.IGNORECASE)
    if match:
        return int(match.group(1))
    raise HTTPException(status_code=400, detail="invalid VK group id")


def _normalize_vk_channel_id(raw: str) -> str:
    return f"-{_parse_vk_group_id(raw)}"


def _normalize_linkedin_author_urn(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="LinkedIn author id is required")
    if value.startswith("urn:li:organization:") or value.startswith("urn:li:person:"):
        return value
    if value.startswith("organization:") or value.startswith("person:"):
        prefix, ident = value.split(":", 1)
        ident = ident.strip()
        if ident:
            return f"urn:li:{prefix}:{ident}"
    if value.startswith("http://") or value.startswith("https://"):
        bits = [part for part in urlparse(value).path.split("/") if part]
        if len(bits) >= 2 and bits[0] in {"company", "in"}:
            kind = "organization" if bits[0] == "company" else "person"
            return f"urn:li:{kind}:{bits[1]}"
    if value.isdigit():
        return f"urn:li:organization:{value}"
    raise HTTPException(status_code=400, detail="invalid LinkedIn author id")


def _normalize_registry_channel_id(platform: str, raw: str) -> str:
    normalized_platform = platform.strip().lower()
    value = (raw or "").strip()
    if normalized_platform == "telegram":
        return _normalize_telegram_channel_id(value)
    if normalized_platform == "max":
        return _normalize_max_channel_id(value)
    if normalized_platform == "vk":
        return _normalize_vk_channel_id(value)
    if normalized_platform == "linkedin":
        return _normalize_linkedin_author_urn(value)
    if normalized_platform == "rss":
        if value.startswith("http://") or value.startswith("https://"):
            return value
        raise HTTPException(status_code=400, detail="RSS feed URL must start with http:// or https://")
    if normalized_platform == "postbridge":
        return value
    raise HTTPException(status_code=422, detail="invalid platform")


def _tenant_public_dict(row: TenantOrm) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _local_admin_public_dict() -> dict[str, Any]:
    return {
        "id": LOCAL_ADMIN_USER_ID,
        "display_name": "Local Admin",
        "role": "admin",
    }


def _selfhost_tenant(session: Session) -> TenantOrm | None:
    settings = get_settings()
    row = session.get(TenantOrm, settings.postbridge_selfhost_tenant_id)
    if row is not None:
        return row
    count = int(session.scalar(select(func.count()).select_from(TenantOrm)) or 0)
    if count == 1:
        return session.scalar(select(TenantOrm).limit(1))
    return None


def _require_selfhost_tenant(session: Session) -> TenantOrm:
    settings = get_settings()
    if settings.postbridge_app_mode != "selfhost":
        raise HTTPException(status_code=404, detail="app API is served by the SaaS BFF in saas mode")
    tenant = _selfhost_tenant(session)
    if tenant is None:
        raise HTTPException(status_code=409, detail="self-host tenant is not bootstrapped")
    return tenant


def _channel_public_dict(row: ChannelOrm) -> dict[str, Any]:
    config = _json_loads_or_empty(row.config_json)
    capabilities = _json_loads_or_empty(row.capabilities_json)
    platform_capabilities = get_platform_capabilities(row.platform)
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "platform": row.platform,
        "kind": row.kind,
        "title": row.title,
        "external_id": row.external_id,
        "platform_channel_id": row.external_id,
        "display": row.title,
        "can_read": capabilities.get("can_read", row.kind in {"source", "both"}),
        "can_write": capabilities.get("can_write", row.kind in {"destination", "target", "both"}),
        "live_sync_source_supported": bool(
            capabilities.get(
                "live_sync_source_supported",
                platform_capabilities.live_sync_source_supported if platform_capabilities else False,
            )
        ),
        "credentials_ref": config.get("credentials_ref"),
        "status": row.status,
        "config": config,
        "capabilities": capabilities,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _credential_public_dict(row: ChannelCredentialOrm) -> dict[str, Any]:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "auth_type": row.auth_type,
        "status": row.status,
        "has_secret": bool((row.encrypted_secret or "").strip()),
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _require_selfhost_channel(session: Session, channel_id: str) -> tuple[TenantOrm, ChannelOrm]:
    tenant = _require_selfhost_tenant(session)
    row = session.get(ChannelOrm, channel_id)
    if row is None or row.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="channel not found")
    return tenant, row


def _credential_for_channel(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
) -> ChannelCredentialOrm | None:
    return session.scalar(
        select(ChannelCredentialOrm)
        .where(
            ChannelCredentialOrm.tenant_id == tenant_id,
            ChannelCredentialOrm.channel_id == channel_id,
        )
        .order_by(ChannelCredentialOrm.created_at.asc())
        .limit(1)
    )


def _create_or_update_managed_credential_channel(
    session: Session,
    *,
    tenant_id: str,
    platform: str,
    platform_channel_id: str,
    title: str,
    secret: dict[str, Any],
    auth_type: str,
    can_read: bool,
    can_write: bool,
    expires_at: datetime | None = None,
) -> tuple[ChannelOrm, ChannelCredentialOrm]:
    row = session.scalar(
        select(ChannelOrm)
        .where(
            ChannelOrm.tenant_id == tenant_id,
            ChannelOrm.platform == platform,
            ChannelOrm.external_id == platform_channel_id,
        )
        .limit(1)
    )
    now = datetime.now(UTC)
    capabilities = {"can_read": can_read, "can_write": can_write}
    if row is None:
        row = ChannelOrm(
            id=str(uuid4()),
            tenant_id=tenant_id,
            platform=platform,
            kind="both" if can_read and can_write else "source" if can_read else "destination",
            title=title,
            external_id=platform_channel_id,
            status="connected",
            config_json=_json_dumps_or_none({}),
            capabilities_json=_json_dumps_or_none(capabilities),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
    else:
        row.title = title or row.title
        row.kind = "both" if can_read and can_write else "source" if can_read else "destination"
        row.status = "connected"
        row.capabilities_json = _json_dumps_or_none({**_json_loads_or_empty(row.capabilities_json), **capabilities})
        row.updated_at = now

    encrypted_secret = encrypt_credential_secret(_json_dumps_or_none(secret))
    credential = _credential_for_channel(session, tenant_id=tenant_id, channel_id=row.id)
    if credential is None:
        credential = ChannelCredentialOrm(
            id=str(uuid4()),
            tenant_id=tenant_id,
            channel_id=row.id,
            auth_type=auth_type,
            encrypted_secret=encrypted_secret,
            expires_at=expires_at,
            status="active",
            created_at=now,
            updated_at=now,
        )
        session.add(credential)
    else:
        credential.auth_type = auth_type
        credential.encrypted_secret = encrypted_secret
        credential.expires_at = expires_at
        credential.status = "active"
        credential.updated_at = now
    row.config_json = _json_dumps_or_none({**_json_loads_or_empty(row.config_json), "credentials_ref": row.id})
    session.commit()
    session.refresh(row)
    session.refresh(credential)
    return row, credential


def _validate_bridge_mode(mode: str) -> str:
    value = mode.strip().lower()
    if value not in BRIDGE_MODES:
        raise HTTPException(status_code=422, detail="invalid bridge mode")
    return value


def _validate_bridge_status(status: str) -> str:
    value = status.strip().lower()
    if value not in BRIDGE_STATUSES:
        raise HTTPException(status_code=422, detail="invalid bridge status")
    return value


def _require_selfhost_channels(
    session: Session,
    *,
    tenant_id: str,
    source_channel_id: str,
    target_channel_id: str,
) -> tuple[ChannelOrm, ChannelOrm]:
    source = session.get(ChannelOrm, source_channel_id)
    target = session.get(ChannelOrm, target_channel_id)
    if source is None or source.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="source channel not found")
    if target is None or target.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="target channel not found")
    return source, target


def _find_selfhost_channel(
    session: Session,
    *,
    tenant_id: str,
    platform: str,
    channel_ref: str | None,
) -> ChannelOrm | None:
    if not channel_ref:
        return None
    row = session.get(ChannelOrm, channel_ref)
    if row is not None and row.tenant_id == tenant_id:
        return row
    return session.scalar(
        select(ChannelOrm)
        .where(
            ChannelOrm.tenant_id == tenant_id,
            ChannelOrm.platform == platform,
            ChannelOrm.external_id == channel_ref,
        )
        .limit(1)
    )


def _get_or_create_selfhost_rss_target(
    session: Session,
    *,
    tenant_id: str,
    title: str | None,
) -> ChannelOrm:
    row = session.scalar(
        select(ChannelOrm)
        .where(
            ChannelOrm.tenant_id == tenant_id,
            ChannelOrm.platform == "rss",
            ChannelOrm.external_id == "rss",
        )
        .limit(1)
    )
    if row is not None:
        return row
    now = datetime.now(UTC)
    row = ChannelOrm(
        id=str(uuid4()),
        tenant_id=tenant_id,
        platform="rss",
        kind="destination",
        title=(title or "RSS").strip() or "RSS",
        external_id="rss",
        status="connected",
        capabilities_json=_json_dumps_or_none({"can_read": False, "can_write": True}),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def _bridge_public_dict(row: BridgeOrm) -> dict[str, Any]:
    settings = row.settings_json or {}
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "owner_user_id": row.saas_user_id,
        "source_channel_id": row.source_channel_id,
        "target_channel_id": row.target_channel_id,
        "status": row.status,
        "mode": row.mode,
        "settings": settings,
        "adaptation_mode": settings.get("adaptation_mode", "rule_only"),
        "adaptation_instructions": settings.get("adaptation_instructions", ""),
        "link_back_enabled": bool(settings.get("link_back_enabled", False)),
        "link_back_site_url": settings.get("link_back_site_url", ""),
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _batch_import_run_public_dict(row: BatchImportRunOrm, *, fetched_posts_count: int | None = None) -> dict[str, Any]:
    error_details = _json_loads_or_empty(row.error_details_json)
    error_payload = None
    if row.error_code or row.error_message:
        error_payload = {
            "code": row.error_code,
            "message": row.error_message,
            "details": error_details,
            "source": row.error_source,
            "retryable": bool(row.error_retryable),
            "correlation_id": row.correlation_id,
        }
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "source_channel": row.source_channel,
        "target_channel": row.target_channel,
        "source_platform": row.source_platform,
        "target_platform": row.target_platform,
        "source_core_channel_id": row.source_core_channel_id,
        "target_core_channel_id": row.target_core_channel_id,
        "status": row.status,
        "requested_limit": row.requested_limit,
        "processed_posts": row.processed_posts,
        "fetched_posts_count": fetched_posts_count,
        "retry_count": row.retry_count,
        "correlation_id": row.correlation_id,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "error_payload": error_payload,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def _content_item_public_dict(row) -> dict[str, Any]:
    data = content_item_to_api_dict(row)
    out: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _publication_target_public_dict(
    row: PublicationTargetOrm,
    *,
    channel: ChannelOrm | None = None,
    plan: PublicationPlanOrm | None = None,
) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "publication_plan_id": row.publication_plan_id,
        "content_item_id": plan.content_item_id if plan else None,
        "channel_id": row.channel_id,
        "channel_title": channel.title if channel else None,
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
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _media_generation_job_public_dict(row: MediaGenerationJobOrm) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "requester_user_id": row.requester_user_id,
        "content_item_id": row.content_item_id,
        "target": row.target,
        "status": row.status,
        "url": row.url,
        "media_asset_id": row.media_asset_id,
        "prompt": row.prompt,
        "usage_tokens_charged": row.usage_tokens_charged,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def _selfhost_billing_summary() -> dict[str, Any]:
    return {
        "enabled": False,
        "status": "active",
        "plan_code": "selfhost",
        "current_period_end": None,
        "ai_platform_adapt_enabled": True,
        "ai_content_tokens_used_day": 0,
        "ai_content_tokens_limit_day": None,
        "ai_content_tokens_used_month": 0,
        "ai_content_tokens_limit_month": None,
        "ai_content_gitsell_tokens_used_day": 0,
        "ai_content_gitsell_tokens_limit_day": None,
        "ai_content_gitsell_tokens_used_month": 0,
        "ai_content_gitsell_tokens_limit_month": None,
    }


def _workspace_settings_public_dict(tenant: TenantOrm) -> dict[str, Any]:
    return {
        "workspace_id": "local",
        "tenant_id": tenant.id,
        "name": tenant.name,
        "image_style_prompt": tenant.image_style_prompt or "",
        "billing": _selfhost_billing_summary(),
        "agent_policy": {
            "editor_instructions": "",
            "search_instructions": "",
            "preferred_domains": [],
            "blocked_domains": [],
            "blocked_url_patterns": [],
        },
    }


def _selfhost_agent_policy() -> dict[str, Any]:
    return {
        "editor_instructions": "",
        "search_instructions": "",
        "preferred_domains": [],
        "blocked_domains": [],
        "blocked_url_patterns": [],
    }


def _require_selfhost_bridge(session: Session, bridge_id: str) -> tuple[TenantOrm, BridgeOrm]:
    tenant = _require_selfhost_tenant(session)
    row = session.scalar(
        select(BridgeOrm).where(
            BridgeOrm.id == bridge_id,
            BridgeOrm.tenant_id == tenant.id,
            BridgeOrm.saas_user_id == LOCAL_ADMIN_USER_ID,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="bridge not found")
    return tenant, row


def _require_selfhost_batch_import_run(session: Session, run_id: str) -> tuple[TenantOrm, BatchImportRunOrm]:
    tenant = _require_selfhost_tenant(session)
    row = session.get(BatchImportRunOrm, run_id)
    if row is None or row.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="job not found")
    return tenant, row


def _require_selfhost_publication_target(
    session: Session,
    target_id: str,
) -> tuple[TenantOrm, PublicationTargetOrm]:
    tenant = _require_selfhost_tenant(session)
    row = session.get(PublicationTargetOrm, target_id)
    if row is None or row.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="publication target not found")
    return tenant, row


def _require_selfhost_media_generation_job(
    session: Session,
    job_id: str,
) -> tuple[TenantOrm, MediaGenerationJobOrm]:
    tenant = _require_selfhost_tenant(session)
    row = session.get(MediaGenerationJobOrm, job_id)
    if row is None or row.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="media generation job not found")
    return tenant, row


def _default_locale() -> str:
    settings = get_settings()
    return (settings.postbridge_default_locale or "").strip().lower() or "en"


def _locale_locked() -> bool:
    raw_env_locale = (os.getenv("POSTBRIDGE_DEFAULT_LOCALE") or "").strip().lower()
    return raw_env_locale == "ru"


def _require_ai_enabled() -> None:
    if not get_settings().ai_gateway_enabled:
        raise HTTPException(status_code=422, detail="AI gateway is disabled")


def _effective_ai_response_language(explicit: str | None) -> str | None:
    value = (explicit or "").strip()
    if value:
        return value[:16]
    default = get_settings().ai_gateway_default_response_language
    if not default:
        return None
    default = default.strip()
    return default[:16] if default else None


@router.get("/dashboard/summary", include_in_schema=False)
def get_dashboard_summary(session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Return a compact self-host dashboard summary from Core data."""
    tenant = _require_selfhost_tenant(session)
    channels_count = int(
        session.scalar(select(func.count()).select_from(ChannelOrm).where(ChannelOrm.tenant_id == tenant.id)) or 0
    )
    bridges_count = int(
        session.scalar(
            select(func.count())
            .select_from(BridgeOrm)
            .where(BridgeOrm.tenant_id == tenant.id, BridgeOrm.saas_user_id == LOCAL_ADMIN_USER_ID)
        )
        or 0
    )
    content_items_count = int(
        session.scalar(
            select(func.count())
            .select_from(ContentItemOrm)
            .where(ContentItemOrm.tenant_id == tenant.id)
        )
        or 0
    )
    pending_targets_count = int(
        session.scalar(
            select(func.count())
            .select_from(PublicationTargetOrm)
            .where(PublicationTargetOrm.tenant_id == tenant.id, PublicationTargetOrm.status == "pending")
        )
        or 0
    )
    return {
        "workspace_id": "local",
        "tenant_id": tenant.id,
        "billing": _selfhost_billing_summary(),
        "migration_product": None,
        "channels_count": channels_count,
        "bridges_count": bridges_count,
        "content_items_count": content_items_count,
        "pending_publication_targets_count": pending_targets_count,
    }


@router.get("/dashboard/jobs", include_in_schema=False)
def list_dashboard_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_db_session),
) -> dict[str, list[dict[str, Any]]]:
    """Return recent self-host batch import jobs for dashboard widgets."""
    tenant = _require_selfhost_tenant(session)
    rows = list(
        session.scalars(
            select(BatchImportRunOrm)
            .where(BatchImportRunOrm.tenant_id == tenant.id)
            .order_by(BatchImportRunOrm.updated_at.desc())
            .limit(limit)
        ).all()
    )
    return {"items": [_batch_import_run_public_dict(row) for row in rows]}


@router.post("/connections/create", include_in_schema=False)
def create_connection_from_wizard(
    body: ConnectionCreateRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Create a live-sync bridge from the legacy SaaS wizard payload."""
    tenant = _require_selfhost_tenant(session)
    source = _find_selfhost_channel(
        session,
        tenant_id=tenant.id,
        platform=body.source_platform,
        channel_ref=body.source_channel_id,
    )
    if source is None:
        raise HTTPException(status_code=404, detail="source channel not found")
    if body.target_platform == "rss":
        target = _get_or_create_selfhost_rss_target(session, tenant_id=tenant.id, title=body.target_display)
    else:
        target = _find_selfhost_channel(
            session,
            tenant_id=tenant.id,
            platform=body.target_platform,
            channel_ref=body.target_channel_id,
        )
    if target is None:
        raise HTTPException(status_code=404, detail="target channel not found")

    now = datetime.now(UTC)
    settings = {
        "requested_limit": body.requested_limit,
        "source_display": body.source_display or source.title,
        "target_display": body.target_display or target.title,
    }
    if body.source_credentials_id:
        settings["source_credentials_id"] = body.source_credentials_id
    if body.target_credentials_id:
        settings["target_credentials_id"] = body.target_credentials_id

    row = BridgeOrm(
        id=str(uuid4()),
        tenant_id=tenant.id,
        saas_user_id=LOCAL_ADMIN_USER_ID,
        source_channel_id=source.id,
        target_channel_id=target.id,
        mode="live_sync",
        status="active",
        settings_json=_json_compatible_dict(settings),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    try:
        session.commit()
        session.refresh(row)
        status = "created"
    except IntegrityError:
        session.rollback()
        row = session.scalar(
            select(BridgeOrm)
            .where(
                BridgeOrm.tenant_id == tenant.id,
                BridgeOrm.saas_user_id == LOCAL_ADMIN_USER_ID,
                BridgeOrm.source_channel_id == source.id,
                BridgeOrm.target_channel_id == target.id,
                BridgeOrm.mode == "live_sync",
            )
            .limit(1)
        )
        if row is None:
            raise HTTPException(status_code=409, detail="bridge already exists") from None
        status = "exists"

    return {"ok": True, "status": status, "bridge": _bridge_public_dict(row)}


@router.post("/jobs/start", include_in_schema=False)
def start_batch_import_job(
    body: BatchImportStartRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    tenant, bridge = _require_selfhost_bridge(session, body.bridge_id)
    source = session.get(ChannelOrm, bridge.source_channel_id)
    target = session.get(ChannelOrm, bridge.target_channel_id)
    if source is None or source.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="source channel not found")
    if target is None or target.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="target channel not found")
    correlation_id = getattr(request.state, "correlation_id", None) or str(uuid4())
    store = BatchImportRunStore(session)
    run, _created = store.create_run(
        tenant_id=tenant.id,
        source_channel=source.external_id or source.id,
        target_channel=target.external_id or target.id,
        requested_limit=body.requested_limit,
        correlation_id=correlation_id,
        target_core_channel_id=target.id,
        source_platform=source.platform,
        target_platform=target.platform,
        source_core_channel_id=source.id,
    )
    process_batch_import_run_task.delay(run.id, correlation_id)
    row = session.get(BatchImportRunOrm, run.id)
    if row is None:
        raise HTTPException(status_code=500, detail="job was not persisted")
    return _batch_import_run_public_dict(row)


@router.get("/jobs", include_in_schema=False)
def list_batch_import_jobs(
    status: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    tenant = _require_selfhost_tenant(session)
    stmt = (
        select(BatchImportRunOrm)
        .where(BatchImportRunOrm.tenant_id == tenant.id)
        .order_by(BatchImportRunOrm.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status:
        stmt = stmt.where(BatchImportRunOrm.status == status)
    total_stmt = select(func.count()).select_from(BatchImportRunOrm).where(BatchImportRunOrm.tenant_id == tenant.id)
    if status:
        total_stmt = total_stmt.where(BatchImportRunOrm.status == status)
    rows = list(session.scalars(stmt).all())
    total = int(session.scalar(total_stmt) or 0)
    return {"items": [_batch_import_run_public_dict(row) for row in rows], "total": total}


@router.get("/jobs/{job_id}", include_in_schema=False)
def get_batch_import_job(
    job_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    _tenant, row = _require_selfhost_batch_import_run(session, job_id)
    fetched_posts_count = int(
        session.scalar(
            select(func.count())
            .select_from(BatchImportFetchedPostOrm)
            .where(BatchImportFetchedPostOrm.batch_import_run_id == row.id)
        )
        or 0
    )
    return _batch_import_run_public_dict(row, fetched_posts_count=fetched_posts_count)


@router.post("/jobs/{job_id}/retry", include_in_schema=False)
def retry_batch_import_job(
    job_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    _tenant, row = _require_selfhost_batch_import_run(session, job_id)
    correlation_id = getattr(request.state, "correlation_id", None) or str(uuid4())
    if not BatchImportRunStore(session).retry_manual(row.id, correlation_id):
        raise HTTPException(status_code=409, detail="job is not retryable")
    process_batch_import_run_task.delay(row.id, correlation_id)
    refreshed = session.get(BatchImportRunOrm, row.id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _batch_import_run_public_dict(refreshed)


@router.post("/jobs/{job_id}/pause", include_in_schema=False)
def pause_batch_import_job(job_id: str, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    _tenant, row = _require_selfhost_batch_import_run(session, job_id)
    if row.status == "paused":
        return _batch_import_run_public_dict(row)
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="only pending jobs can be paused")
    now = datetime.now(UTC)
    row.status = "paused"
    row.updated_at = now
    session.add(row)
    session.commit()
    session.refresh(row)
    return _batch_import_run_public_dict(row)


@router.post("/jobs/{job_id}/cancel", include_in_schema=False)
def cancel_batch_import_job(job_id: str, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    _tenant, row = _require_selfhost_batch_import_run(session, job_id)
    now = datetime.now(UTC)
    row.status = "failed"
    row.error_code = "VALIDATION_JOB_CANCELLED"
    row.error_message = "Job cancelled by user"
    row.error_source = "user"
    row.error_retryable = False
    row.error_details_json = json.dumps({"code": "VALIDATION_JOB_CANCELLED"}, ensure_ascii=True)
    row.completed_at = now
    row.updated_at = now
    session.add(row)
    session.commit()
    session.refresh(row)
    return _batch_import_run_public_dict(row)


@router.delete("/jobs/{job_id}", status_code=204, include_in_schema=False)
def delete_batch_import_job(job_id: str, session: Session = Depends(get_db_session)) -> Response:
    _tenant, row = _require_selfhost_batch_import_run(session, job_id)
    session.execute(delete(BatchImportFetchedPostOrm).where(BatchImportFetchedPostOrm.batch_import_run_id == row.id))
    BatchImportRunStore(session).delete_enqueued_posts_for_run(row.id)
    session.delete(row)
    session.commit()
    return Response(status_code=204)


@router.get("/settings", include_in_schema=False)
def get_workspace_settings(session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Return self-host workspace settings."""
    tenant = _require_selfhost_tenant(session)
    return _workspace_settings_public_dict(tenant)


@router.put("/settings", include_in_schema=False)
def update_workspace_settings(
    body: WorkspaceSettingsPatchRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Update self-host workspace settings."""
    tenant = _require_selfhost_tenant(session)
    if "image_style_prompt" in body.model_fields_set:
        tenant.image_style_prompt = body.image_style_prompt or ""
    tenant.updated_at = datetime.now(UTC)
    session.add(tenant)
    session.commit()
    session.refresh(tenant)
    return _workspace_settings_public_dict(tenant)


@router.get("/runtime-config", include_in_schema=False)
def get_app_runtime_config() -> dict[str, Any]:
    """Return non-secret runtime config for the browser app."""
    settings = get_settings()
    app_mode = settings.postbridge_app_mode
    is_selfhost = app_mode == "selfhost"
    return {
        "app_mode": app_mode,
        "api": {
            "base_path": "/api/app",
        },
        "i18n": {
            "default_locale": _default_locale(),
            "locale_locked": _locale_locked(),
        },
        "features": {
            "billing": {"enabled": not is_selfhost},
            "workspaces": {"enabled": not is_selfhost},
            "multi_tenant": {"enabled": not is_selfhost},
            "managed_credentials": {"enabled": True, "mode": "core" if is_selfhost else "bff"},
            "local_auth": {"enabled": is_selfhost},
            "agent": {"enabled": True},
            "agent_ops": {"enabled": True},
            "embeddings_maintenance": {"enabled": False},
            "media_generation": {"enabled": True},
            "review_queue": {"enabled": True},
        },
        "capabilities": {
            "billing": {"enabled": not is_selfhost},
            "workspaces": {"enabled": not is_selfhost},
            "multiTenant": {"enabled": not is_selfhost},
            "managedCredentials": {"enabled": True, "mode": "core" if is_selfhost else "bff"},
            "localAuth": {"enabled": is_selfhost},
            "agent": {"enabled": True},
            "agentOps": {"enabled": True},
            "embeddingsMaintenance": {"enabled": False},
            "mediaGeneration": {"enabled": True},
            "reviewQueue": {"enabled": True},
        },
    }


@router.get("/auth/providers", include_in_schema=False)
def get_auth_providers() -> dict[str, Any]:
    """Return disabled SaaS auth providers for the self-host browser app."""
    return {"items": [], "providers": [], "local_auth": True}


@router.post("/auth/magic-link/{action}", include_in_schema=False)
def handle_disabled_magic_link(action: str) -> dict[str, Any]:
    """Acknowledge SaaS magic-link actions as disabled in self-host."""
    return {"ok": False, "disabled": True, "action": action}


@router.api_route("/auth/telegram-web/{path:path}", methods=["GET", "POST"], include_in_schema=False)
def handle_disabled_telegram_web_auth(path: str) -> dict[str, Any]:
    """Acknowledge Telegram WebApp auth as disabled in self-host."""
    return {"status": "disabled", "path": path}


@router.get("/news", include_in_schema=False)
def list_public_news() -> dict[str, Any]:
    """Return an empty news feed; marketing news is owned by the SaaS layer."""
    return {"items": [], "total": 0}


@router.get("/news/{slug}", include_in_schema=False)
def get_public_news_item(slug: str) -> dict[str, Any]:
    """Return an empty news detail shell for shared frontend routes."""
    return {"slug": slug, "title": "Postbridge", "content_md": "", "published_at": None}


@router.post("/billing-email/request", include_in_schema=False)
def request_billing_email_verification() -> dict[str, Any]:
    """Billing email verification is not required in self-host."""
    return {"ok": True, "disabled": True, "dev_code": "0000"}


@router.post("/billing-email/verify", include_in_schema=False)
def verify_billing_email() -> dict[str, Any]:
    """Billing email verification is not required in self-host."""
    return {"ok": True, "disabled": True}


@router.get("/billing/plans", include_in_schema=False)
def list_billing_plans(session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Return no paid SaaS plans for the self-host app."""
    _require_selfhost_tenant(session)
    return {"items": [], "billing": _selfhost_billing_summary()}


@router.api_route("/billing/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
def handle_disabled_billing(path: str, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Return an explicit disabled response for SaaS billing actions."""
    _require_selfhost_tenant(session)
    return {
        "ok": False,
        "disabled": True,
        "path": path,
        "billing": _selfhost_billing_summary(),
        "payment_url": None,
        "invoice_url": None,
    }


@router.post("/platform-previews", include_in_schema=False)
def create_platform_previews(session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Platform previews are optional; return an empty preview set in self-host."""
    _require_selfhost_tenant(session)
    return {"items": []}


@router.get("/agent/workspace-policy", include_in_schema=False)
def get_agent_workspace_policy(session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Return self-host agent policy defaults."""
    _require_selfhost_tenant(session)
    return _selfhost_agent_policy()


@router.put("/agent/workspace-policy", include_in_schema=False)
def update_agent_workspace_policy(session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Accept policy saves as a no-op until persisted self-host policy is added."""
    _require_selfhost_tenant(session)
    return _selfhost_agent_policy()


@router.get("/agent/embeddings/lifecycle", include_in_schema=False)
def get_agent_embeddings_lifecycle(session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Return an explicit disabled lifecycle snapshot for embeddings maintenance."""
    _require_selfhost_tenant(session)
    return {
        "backend": "core",
        "native_mode": True,
        "channels": [],
        "totals": {
            "materials": 0,
            "stored_embeddings": 0,
            "missing_embeddings": 0,
            "stale_embeddings": 0,
        },
    }


@router.post("/agent/reindex/{path:path}", include_in_schema=False)
def handle_disabled_agent_reindex(path: str, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Return skipped for SaaS-only agent reindex actions."""
    _require_selfhost_tenant(session)
    return {"status": "skipped", "path": path, "reason": "embeddings maintenance is not enabled in self-host UI yet"}


@router.post("/agent/embeddings/maintenance", include_in_schema=False)
def handle_disabled_agent_embeddings_maintenance(session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Return skipped for SaaS-only agent embeddings maintenance."""
    _require_selfhost_tenant(session)
    return {"status": "skipped", "reason": "embeddings maintenance is not enabled in self-host UI yet"}


@router.post("/agent/embeddings/compact", include_in_schema=False)
def handle_disabled_agent_embeddings_compact(session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Return skipped for SaaS-only agent embeddings compaction."""
    _require_selfhost_tenant(session)
    return {"status": "skipped", "reason": "embeddings maintenance is not enabled in self-host UI yet"}


@router.post("/agent/cleanup", include_in_schema=False)
def handle_disabled_agent_cleanup(session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Return skipped for SaaS-only agent cleanup."""
    _require_selfhost_tenant(session)
    return {"status": "skipped", "reason": "embeddings maintenance is not enabled in self-host UI yet"}


@router.get("/session", include_in_schema=False)
def get_app_session(session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Return local browser session context for the shared frontend."""
    settings = get_settings()
    if settings.postbridge_app_mode != "selfhost":
        return {
            "app_mode": settings.postbridge_app_mode,
            "bootstrapped": False,
            "authenticated": False,
            "user": None,
            "tenant": None,
        }
    tenant = _selfhost_tenant(session)
    if tenant is None:
        return {
            "app_mode": "selfhost",
            "bootstrapped": False,
            "authenticated": False,
            "user": None,
            "tenant": None,
        }
    return {
        "app_mode": "selfhost",
        "bootstrapped": True,
        "authenticated": True,
        "user": _local_admin_public_dict(),
        "tenant": _tenant_public_dict(tenant),
    }


@router.post("/bootstrap", include_in_schema=False)
def bootstrap_app(
    body: BootstrapRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Create the default self-host tenant if it does not exist."""
    settings = get_settings()
    if settings.postbridge_app_mode != "selfhost":
        return {
            "app_mode": settings.postbridge_app_mode,
            "bootstrapped": False,
            "authenticated": False,
            "user": None,
            "tenant": None,
        }
    tenant = _selfhost_tenant(session)
    if tenant is None:
        tenant = TenantOrm(
            id=settings.postbridge_selfhost_tenant_id,
            name=body.tenant_name or "Postbridge Self-host",
        )
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
    return {
        "app_mode": "selfhost",
        "bootstrapped": True,
        "authenticated": True,
        "user": _local_admin_public_dict(),
        "tenant": _tenant_public_dict(tenant),
    }


@router.get("/channels", include_in_schema=False)
def list_channels(
    platform: str | None = Query(default=None, max_length=32),
    kind: str | None = Query(default=None, max_length=16),
    status: str | None = Query(default=None, max_length=32),
    session: Session = Depends(get_db_session),
) -> dict[str, list[dict[str, Any]]]:
    """List channels for the self-host tenant."""
    tenant = _require_selfhost_tenant(session)
    stmt = select(ChannelOrm).where(ChannelOrm.tenant_id == tenant.id)
    if platform:
        stmt = stmt.where(ChannelOrm.platform == platform.strip().lower())
    if kind:
        stmt = stmt.where(ChannelOrm.kind == kind.strip().lower())
    if status:
        stmt = stmt.where(ChannelOrm.status == status.strip().lower())
    rows = session.scalars(stmt.order_by(ChannelOrm.created_at.asc())).all()
    return {"items": [_channel_public_dict(row) for row in rows]}


@router.post("/channel-registry/validate", include_in_schema=False)
def validate_channel_registry(
    body: ChannelValidateRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Validate and normalize a channel id for the self-host add-channel flow."""
    _require_selfhost_tenant(session)
    platform = body.platform.strip().lower()
    normalized = _normalize_registry_channel_id(platform, body.platform_channel_id)
    return {
        "ok": True,
        "display": normalized,
        "platform_channel_id": normalized,
        "role": body.role,
        "errors": [],
    }


@router.post("/channel-registry/max/request-verification", include_in_schema=False)
def request_max_verification(
    body: MaxVerificationRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return a local verification code for the self-host MAX onboarding flow."""
    _require_selfhost_tenant(session)
    normalized = _normalize_max_channel_id(body.platform_channel_id)
    code = f"PB-{uuid4().hex[:6].upper()}"
    return {"code": code, "deeplink": None, "platform_channel_id": normalized, "expires_at": None}


@router.post("/channel-registry/max/verify", include_in_schema=False)
def verify_max_channel(
    body: MaxVerificationVerifyRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Accept a previously generated local code for self-host MAX onboarding."""
    _require_selfhost_tenant(session)
    normalized = _normalize_max_channel_id(body.platform_channel_id)
    return {"ok": True, "display": normalized, "platform_channel_id": normalized, "errors": []}


@router.post("/credentials/vk/community-token", include_in_schema=False)
def create_vk_community_token_credential(
    body: VKCommunityTokenRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Store VK community credentials directly in Core for self-host."""
    tenant = _require_selfhost_tenant(session)
    platform_channel_id = _normalize_vk_channel_id(body.group_id)
    channel, _credential = _create_or_update_managed_credential_channel(
        session,
        tenant_id=tenant.id,
        platform="vk",
        platform_channel_id=platform_channel_id,
        title=f"VK {platform_channel_id.lstrip('-')}",
        secret={"access_token": body.access_token.strip(), "group_id": platform_channel_id.lstrip("-")},
        auth_type="vk_community_token",
        can_read=True,
        can_write=True,
    )
    return {"id": channel.id, "platform_channel_id": platform_channel_id, "display": channel.title}


@router.get("/credentials/linkedin/authorize-url", include_in_schema=False)
def get_linkedin_authorize_url(session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """LinkedIn OAuth is supplied by the SaaS BFF; self-host supports manual token entry."""
    _require_selfhost_tenant(session)
    return {"authorize_url": "", "disabled": True}


@router.post("/credentials/linkedin/organizations", include_in_schema=False)
def list_linkedin_organizations_for_token(
    body: LinkedInOrganizationsRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return an empty discovery result; manual author id entry is supported in self-host."""
    _require_selfhost_tenant(session)
    _ = body
    return {"items": []}


@router.post("/credentials/linkedin/access-token", include_in_schema=False)
def create_linkedin_access_token_credential(
    body: LinkedInAccessTokenRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Store LinkedIn publisher credentials directly in Core for self-host."""
    tenant = _require_selfhost_tenant(session)
    author_urn = _normalize_linkedin_author_urn(body.author_id)
    display = (body.display or "").strip() or "LinkedIn"
    channel, _credential = _create_or_update_managed_credential_channel(
        session,
        tenant_id=tenant.id,
        platform="linkedin",
        platform_channel_id=author_urn,
        title=display,
        secret={
            "access_token": body.access_token.strip(),
            "author_urn": author_urn,
            **({"api_version": body.api_version} if body.api_version else {}),
        },
        auth_type="linkedin_access_token",
        can_read=False,
        can_write=True,
        expires_at=body.expires_at,
    )
    return {"id": channel.id, "platform_channel_id": author_urn, "display": channel.title}


@router.post("/channels", include_in_schema=False)
def create_channel(
    body: ChannelCreateRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Create a channel for the self-host tenant."""
    tenant = _require_selfhost_tenant(session)
    credentials_channel = session.get(ChannelOrm, body.credentials_ref) if body.credentials_ref else None
    if credentials_channel is not None and credentials_channel.tenant_id == tenant.id:
        credentials_channel.platform = body.platform.strip().lower()
        credentials_channel.kind = (
            body.kind.strip().lower()
            if body.kind
            else ("both" if body.can_read and body.can_write else "source" if body.can_read else "destination")
        )
        credentials_channel.title = body.title
        credentials_channel.external_id = body.external_id or body.platform_channel_id
        credentials_channel.status = body.status.strip().lower()
        credentials_channel.config_json = _json_dumps_or_none(
            {
                **_json_loads_or_empty(credentials_channel.config_json),
                **(body.config or {}),
                "credentials_ref": credentials_channel.id,
            }
        )
        credentials_channel.capabilities_json = _json_dumps_or_none(
            {
                **_json_loads_or_empty(credentials_channel.capabilities_json),
                **(body.capabilities or {}),
                **({"can_read": body.can_read} if body.can_read is not None else {}),
                **({"can_write": body.can_write} if body.can_write is not None else {}),
            }
        )
        credentials_channel.updated_at = datetime.now(UTC)
        session.commit()
        session.refresh(credentials_channel)
        return _channel_public_dict(credentials_channel)
    row = ChannelOrm(
        id=str(uuid4()),
        tenant_id=tenant.id,
        platform=body.platform.strip().lower(),
        kind=(
            body.kind.strip().lower()
            if body.kind
            else ("both" if body.can_read and body.can_write else "source" if body.can_read else "destination")
        ),
        title=body.title,
        external_id=body.external_id or body.platform_channel_id,
        status=body.status.strip().lower(),
        config_json=_json_dumps_or_none(
            {
                **(body.config or {}),
                **({"credentials_ref": body.credentials_ref} if body.credentials_ref else {}),
            }
        ),
        capabilities_json=_json_dumps_or_none(
            {
                **(body.capabilities or {}),
                **({"can_read": body.can_read} if body.can_read is not None else {}),
                **({"can_write": body.can_write} if body.can_write is not None else {}),
            }
        ),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _channel_public_dict(row)


@router.get("/channels/{channel_id}", include_in_schema=False)
def get_channel(
    channel_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return one self-host channel."""
    _, row = _require_selfhost_channel(session, channel_id)
    return _channel_public_dict(row)


@router.delete("/channels/{channel_id}", include_in_schema=False)
def delete_channel(
    channel_id: str,
    session: Session = Depends(get_db_session),
) -> Response:
    """Delete one self-host channel."""
    _, row = _require_selfhost_channel(session, channel_id)
    session.delete(row)
    session.commit()
    return Response(status_code=204)


@router.get("/channels/{channel_id}/credential", include_in_schema=False)
def get_channel_credential(
    channel_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return credential metadata without exposing the plaintext secret."""
    tenant, row = _require_selfhost_channel(session, channel_id)
    _ = row
    credential = _credential_for_channel(session, tenant_id=tenant.id, channel_id=channel_id)
    if credential is None:
        return {
            "channel_id": channel_id,
            "auth_type": None,
            "status": None,
            "has_secret": False,
            "expires_at": None,
            "created_at": None,
            "updated_at": None,
        }
    return _credential_public_dict(credential)


@router.put("/channels/{channel_id}/credential", include_in_schema=False)
def upsert_channel_credential(
    channel_id: str,
    body: ChannelCredentialUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Create or replace encrypted credential data for one self-host channel."""
    tenant, _channel = _require_selfhost_channel(session, channel_id)
    secret_json = _json_dumps_or_none(body.secret)
    encrypted_secret = encrypt_credential_secret(secret_json) if secret_json is not None else None
    credential = _credential_for_channel(session, tenant_id=tenant.id, channel_id=channel_id)
    now = datetime.now(UTC)
    if credential is None:
        credential = ChannelCredentialOrm(
            id=str(uuid4()),
            tenant_id=tenant.id,
            channel_id=channel_id,
            auth_type=body.auth_type.strip().lower(),
            encrypted_secret=encrypted_secret,
            status=body.status.strip().lower(),
            created_at=now,
            updated_at=now,
        )
        session.add(credential)
    else:
        credential.auth_type = body.auth_type.strip().lower()
        credential.encrypted_secret = encrypted_secret
        credential.status = body.status.strip().lower()
        credential.updated_at = now
    session.commit()
    session.refresh(credential)
    return _credential_public_dict(credential)


@router.delete("/channels/{channel_id}/credential", include_in_schema=False)
def delete_channel_credential(
    channel_id: str,
    session: Session = Depends(get_db_session),
) -> Response:
    """Delete credential data for one self-host channel."""
    tenant, _channel = _require_selfhost_channel(session, channel_id)
    credential = _credential_for_channel(session, tenant_id=tenant.id, channel_id=channel_id)
    if credential is None:
        raise HTTPException(status_code=404, detail="channel credential not found")
    session.delete(credential)
    session.commit()
    return Response(status_code=204)


@router.get("/bridges", include_in_schema=False)
def list_bridges(
    mode: str | None = Query(default=None, max_length=32),
    status: str | None = Query(default=None, max_length=32),
    source_channel_id: str | None = Query(default=None, max_length=36),
    target_channel_id: str | None = Query(default=None, max_length=36),
    session: Session = Depends(get_db_session),
) -> dict[str, list[dict[str, Any]]]:
    """List bridges for the self-host tenant."""
    tenant = _require_selfhost_tenant(session)
    stmt = select(BridgeOrm).where(
        BridgeOrm.tenant_id == tenant.id,
        BridgeOrm.saas_user_id == LOCAL_ADMIN_USER_ID,
    )
    if mode:
        stmt = stmt.where(BridgeOrm.mode == mode.strip().lower())
    if status:
        stmt = stmt.where(BridgeOrm.status == status.strip().lower())
    if source_channel_id:
        stmt = stmt.where(BridgeOrm.source_channel_id == source_channel_id)
    if target_channel_id:
        stmt = stmt.where(BridgeOrm.target_channel_id == target_channel_id)
    rows = session.scalars(stmt.order_by(BridgeOrm.created_at.asc())).all()
    return {"items": [_bridge_public_dict(row) for row in rows]}


@router.post("/bridges", include_in_schema=False)
def create_bridge(
    body: BridgeCreateRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Create a bridge between two self-host channels."""
    tenant = _require_selfhost_tenant(session)
    mode = _validate_bridge_mode(body.mode)
    status = _validate_bridge_status(body.status)
    _require_selfhost_channels(
        session,
        tenant_id=tenant.id,
        source_channel_id=body.source_channel_id,
        target_channel_id=body.target_channel_id,
    )
    now = datetime.now(UTC)
    row = BridgeOrm(
        id=str(uuid4()),
        tenant_id=tenant.id,
        saas_user_id=LOCAL_ADMIN_USER_ID,
        source_channel_id=body.source_channel_id,
        target_channel_id=body.target_channel_id,
        mode=mode,
        status=status,
        settings_json=_json_compatible_dict(body.settings),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        existing = session.scalar(
            select(BridgeOrm)
            .where(
                BridgeOrm.tenant_id == tenant.id,
                BridgeOrm.saas_user_id == LOCAL_ADMIN_USER_ID,
                BridgeOrm.source_channel_id == body.source_channel_id,
                BridgeOrm.target_channel_id == body.target_channel_id,
                BridgeOrm.mode == mode,
            )
            .limit(1)
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="bridge already exists") from exc
        raise HTTPException(status_code=500, detail="bridge creation failed") from exc
    session.refresh(row)
    return _bridge_public_dict(row)


@router.get("/bridges/live-sync-targets", include_in_schema=False)
def list_live_sync_targets(
    source_channel_id: str = Query(..., min_length=36, max_length=36),
    session: Session = Depends(get_db_session),
) -> dict[str, list[dict[str, Any]]]:
    """List active live-sync targets for one self-host source channel."""
    tenant = _require_selfhost_tenant(session)
    source = session.get(ChannelOrm, source_channel_id)
    if source is None or source.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="source channel not found")
    rows = session.scalars(
        select(BridgeOrm).where(
            BridgeOrm.tenant_id == tenant.id,
            BridgeOrm.saas_user_id == LOCAL_ADMIN_USER_ID,
            BridgeOrm.source_channel_id == source_channel_id,
            BridgeOrm.status == "active",
            BridgeOrm.mode == "live_sync",
        )
    ).all()
    items: list[dict[str, Any]] = []
    for row in rows:
        target = session.get(ChannelOrm, row.target_channel_id)
        if target is None or target.tenant_id != tenant.id:
            continue
        items.append(
            {
                "bridge_id": row.id,
                "target_channel_id": target.id,
                "platform": target.platform,
                "external_id": target.external_id,
                "bridge_settings": row.settings_json or {},
            }
        )
    return {"items": items}


@router.get("/bridges/{bridge_id}", include_in_schema=False)
def get_bridge(
    bridge_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return one self-host bridge."""
    _, row = _require_selfhost_bridge(session, bridge_id)
    return _bridge_public_dict(row)


@router.patch("/bridges/{bridge_id}", include_in_schema=False)
def patch_bridge(
    bridge_id: str,
    body: BridgePatchRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Update self-host bridge status/settings."""
    _, row = _require_selfhost_bridge(session, bridge_id)
    patch_fields = {
        "adaptation_mode",
        "adaptation_instructions",
        "link_back_enabled",
        "link_back_site_url",
    }
    has_settings_patch = "settings" in body.model_fields_set or any(field in body.model_fields_set for field in patch_fields)
    if body.status is None and not has_settings_patch:
        raise HTTPException(status_code=422, detail="empty bridge patch")
    if body.status is not None:
        row.status = _validate_bridge_status(body.status)
    if has_settings_patch:
        settings = dict(row.settings_json or {})
        if "settings" in body.model_fields_set:
            settings.update(body.settings or {})
        for field in patch_fields:
            if field in body.model_fields_set:
                settings[field] = getattr(body, field)
        row.settings_json = _json_compatible_dict(settings)
    row.updated_at = datetime.now(UTC)
    session.commit()
    session.refresh(row)
    return _bridge_public_dict(row)


@router.delete("/bridges/{bridge_id}", include_in_schema=False)
def delete_bridge(
    bridge_id: str,
    session: Session = Depends(get_db_session),
) -> Response:
    """Delete one self-host bridge."""
    _, row = _require_selfhost_bridge(session, bridge_id)
    session.delete(row)
    session.commit()
    return Response(status_code=204)


@router.get("/content-items", include_in_schema=False)
def list_content_items(
    status: str | None = Query(default=None, pattern="^(draft|published)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> dict[str, list[dict[str, Any]]]:
    """List self-host Postbridge content items."""
    tenant = _require_selfhost_tenant(session)
    rows = list_postbridge_content_items(
        session,
        tenant_id=tenant.id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"items": [_content_item_public_dict(row) for row in rows]}


@router.post("/content-items", include_in_schema=False)
def create_content_item(
    body: ContentItemCreateRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Create a self-host Postbridge content item."""
    tenant = _require_selfhost_tenant(session)
    if body.status == "published" and not body.content_md.strip():
        raise HTTPException(status_code=422, detail="content_md is required for published status")
    row = create_postbridge_content_item(
        session,
        tenant_id=tenant.id,
        author_user_id=LOCAL_ADMIN_USER_ID,
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
        saas_workspace_id=None,
    )
    session.commit()
    session.refresh(row)
    return _content_item_public_dict(row)


@router.get("/content-items/{content_id}", include_in_schema=False)
def get_content_item(
    content_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return one self-host Postbridge content item."""
    tenant = _require_selfhost_tenant(session)
    row = get_postbridge_content_item(session, tenant_id=tenant.id, content_id=content_id)
    if row is None:
        raise HTTPException(status_code=404, detail="content item not found")
    return _content_item_public_dict(row)


@router.patch("/content-items/{content_id}", include_in_schema=False)
def patch_content_item(
    content_id: str,
    body: ContentItemPatchRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Update a self-host Postbridge content item."""
    tenant = _require_selfhost_tenant(session)
    row = get_postbridge_content_item(session, tenant_id=tenant.id, content_id=content_id)
    if row is None:
        raise HTTPException(status_code=404, detail="content item not found")
    scheduled_at = (
        body.scheduled_publish_at
        if "scheduled_publish_at" in body.model_fields_set
        else POSTBRIDGE_SCHEDULE_UNSET
    )
    source_channel_id = (
        body.live_sync_source_core_channel_id
        if "live_sync_source_core_channel_id" in body.model_fields_set
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
        scheduled_publish_at=scheduled_at,
        live_sync_source_core_channel_id=source_channel_id,
    )
    session.commit()
    session.refresh(row)
    return _content_item_public_dict(row)


@router.get("/content-items/{content_id}/publication-targets", include_in_schema=False)
def list_content_item_publication_targets(
    content_id: str,
    status: str | None = Query(default=None, max_length=32),
    session: Session = Depends(get_db_session),
) -> dict[str, list[dict[str, Any]]]:
    """List publication targets for one self-host content item."""
    tenant = _require_selfhost_tenant(session)
    row = get_postbridge_content_item(session, tenant_id=tenant.id, content_id=content_id)
    if row is None:
        raise HTTPException(status_code=404, detail="content item not found")
    stmt = (
        select(PublicationTargetOrm, ChannelOrm, PublicationPlanOrm)
        .join(PublicationPlanOrm, PublicationTargetOrm.publication_plan_id == PublicationPlanOrm.id)
        .join(ChannelOrm, PublicationTargetOrm.channel_id == ChannelOrm.id)
        .where(
            PublicationTargetOrm.tenant_id == tenant.id,
            PublicationPlanOrm.tenant_id == tenant.id,
            PublicationPlanOrm.content_item_id == content_id,
        )
    )
    if status:
        stmt = stmt.where(PublicationTargetOrm.status == status.strip().lower())
    rows = session.execute(stmt.order_by(PublicationTargetOrm.created_at.asc())).all()
    return {
        "items": [
            _publication_target_public_dict(target, channel=channel, plan=plan)
            for target, channel, plan in rows
        ]
    }


@router.post("/content-items/{content_id}/publication-targets", include_in_schema=False)
def create_content_item_publication_targets(
    content_id: str,
    body: PublicationTargetsCreateRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Create publication targets for one self-host content item."""
    tenant = _require_selfhost_tenant(session)
    row = get_postbridge_content_item(session, tenant_id=tenant.id, content_id=content_id)
    if row is None:
        raise HTTPException(status_code=404, detail="content item not found")
    if not (row.body_markdown or "").strip():
        raise HTTPException(status_code=422, detail="content_md is required before publication")
    strategy = "scheduled" if body.scheduled_at is not None else "immediate"
    plan_status = "scheduled" if strategy == "scheduled" else "draft"
    try:
        result = create_plan_and_targets_for_content_item(
            session,
            tenant_id=tenant.id,
            content_item=row,
            channel_ids=body.channel_ids,
            plan_strategy=strategy,
            plan_status=plan_status,
            target_status="pending",
            scheduled_at=body.scheduled_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    dispatched: list[str] = []
    if body.dispatch:
        correlation_id = getattr(request.state, "correlation_id", None) or "selfhost-app"
        for target_id in result.publication_target_ids:
            process_publication_target_task.delay(target_id, correlation_id)
            dispatched.append(target_id)
    return {
        "content_item_id": result.content_item_id,
        "publication_plan_id": result.publication_plan_id,
        "render_variant_ids": result.render_variant_ids,
        "publication_target_ids": result.publication_target_ids,
        "dispatched_target_ids": dispatched,
    }


@router.get("/publication-targets", include_in_schema=False)
def list_publication_targets(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None, max_length=32),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """List publication targets for the self-host tenant."""
    tenant = _require_selfhost_tenant(session)
    base = (
        select(PublicationTargetOrm, ChannelOrm, PublicationPlanOrm)
        .join(PublicationPlanOrm, PublicationTargetOrm.publication_plan_id == PublicationPlanOrm.id)
        .join(ChannelOrm, PublicationTargetOrm.channel_id == ChannelOrm.id)
        .where(PublicationTargetOrm.tenant_id == tenant.id, PublicationPlanOrm.tenant_id == tenant.id)
    )
    count_stmt = (
        select(func.count())
        .select_from(PublicationTargetOrm)
        .join(PublicationPlanOrm, PublicationTargetOrm.publication_plan_id == PublicationPlanOrm.id)
        .where(PublicationTargetOrm.tenant_id == tenant.id, PublicationPlanOrm.tenant_id == tenant.id)
    )
    if status:
        status_value = status.strip().lower()
        base = base.where(PublicationTargetOrm.status == status_value)
        count_stmt = count_stmt.where(PublicationTargetOrm.status == status_value)
    rows = session.execute(
        base.order_by(PublicationTargetOrm.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    total = int(session.scalar(count_stmt) or 0)
    return {
        "items": [
            _publication_target_public_dict(target, channel=channel, plan=plan)
            for target, channel, plan in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.delete("/content-items/{content_id}", include_in_schema=False)
def delete_content_item(
    content_id: str,
    session: Session = Depends(get_db_session),
) -> Response:
    """Delete a self-host Postbridge content item."""
    tenant = _require_selfhost_tenant(session)
    row = get_postbridge_content_item(session, tenant_id=tenant.id, content_id=content_id)
    if row is None:
        raise HTTPException(status_code=404, detail="content item not found")
    delete_postbridge_content_item(session, row=row)
    session.commit()
    return Response(status_code=204)


@router.post("/content-items/generate", include_in_schema=False)
def generate_content_item(
    body: ContentGenerateRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Generate a new self-host content item or refine an existing one."""
    tenant = _require_selfhost_tenant(session)
    _require_ai_enabled()
    if body.content_item_id:
        row = get_postbridge_content_item(
            session,
            tenant_id=tenant.id,
            content_id=body.content_item_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="content item not found")
    correlation_id = getattr(request.state, "correlation_id", None) or "selfhost-app"
    client = get_ai_gateway_client()
    result = generate_and_plan(
        session,
        tenant_id=tenant.id,
        prompt=body.prompt,
        messages=body.messages,
        model=body.model,
        client=client,
        target_language=_effective_ai_response_language(body.target_language),
        author_user_id=LOCAL_ADMIN_USER_ID,
        core_channel_ids=None,
        dispatch=False,
        correlation_id=correlation_id,
        content_item_id=body.content_item_id,
    )
    out = public_dict_for_generate_result(session, result)
    session.commit()
    return out


@router.post("/content-items/{content_id}/adapt", include_in_schema=False)
def adapt_content_item(
    content_id: str,
    body: ContentAdaptRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Adapt one self-host content item for a target channel."""
    tenant = _require_selfhost_tenant(session)
    _require_ai_enabled()
    row = get_postbridge_content_item(session, tenant_id=tenant.id, content_id=content_id)
    if row is None:
        raise HTTPException(status_code=404, detail="content item not found")
    client = get_ai_gateway_client()
    result = adapt_content_for_channel(
        session,
        tenant_id=tenant.id,
        content_item_id=content_id,
        channel_id=body.channel_id,
        client=client,
        target_language=_effective_ai_response_language(body.target_language),
        model=body.model,
    )
    out = {
        "operation": "adapt",
        "content_item_id": result.content_item_id,
        "channel_id": result.channel_id,
        "render_variant_id": result.render_variant_id,
        "previous_render_variant_id": result.previous_render_variant_id,
        "usage_tokens_charged": result.usage_tokens_charged,
    }
    session.commit()
    return out


@router.post("/content-items/{content_id}/translate", include_in_schema=False)
def translate_content_item(
    content_id: str,
    body: ContentTranslateRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Translate one self-host content item for a target channel."""
    tenant = _require_selfhost_tenant(session)
    _require_ai_enabled()
    row = get_postbridge_content_item(session, tenant_id=tenant.id, content_id=content_id)
    if row is None:
        raise HTTPException(status_code=404, detail="content item not found")
    client = get_ai_gateway_client()
    result = translate_content_for_channel(
        session,
        tenant_id=tenant.id,
        content_item_id=content_id,
        channel_id=body.channel_id,
        target_language=body.target_language,
        client=client,
        model=body.model,
    )
    out = {
        "operation": "translate",
        "content_item_id": result.content_item_id,
        "channel_id": result.channel_id,
        "render_variant_id": result.render_variant_id,
        "previous_render_variant_id": result.previous_render_variant_id,
        "usage_tokens_charged": result.usage_tokens_charged,
    }
    session.commit()
    return out


@router.get("/content-items/{content_id}/ai-chat", include_in_schema=False)
def list_content_item_ai_chat(
    content_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return AI editor chat history for one self-host content item."""
    tenant = _require_selfhost_tenant(session)
    row = get_postbridge_content_item(session, tenant_id=tenant.id, content_id=content_id)
    if row is None:
        raise HTTPException(status_code=404, detail="content item not found")
    return {
        "messages": list_ai_chat_messages(session, tenant_id=tenant.id, content_item_id=content_id),
        "events": list_ai_chat_events(session, tenant_id=tenant.id, content_item_id=content_id),
    }


@router.delete("/content-items/{content_id}/ai-chat", include_in_schema=False)
def delete_content_item_ai_chat(
    content_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, int]:
    """Clear AI editor chat history for one self-host content item."""
    tenant = _require_selfhost_tenant(session)
    row = get_postbridge_content_item(session, tenant_id=tenant.id, content_id=content_id)
    if row is None:
        raise HTTPException(status_code=404, detail="content item not found")
    deleted = delete_ai_chat_messages(session, tenant_id=tenant.id, content_item_id=content_id)
    session.commit()
    return {"deleted": deleted}


@router.get("/agent/tasks", include_in_schema=False)
def list_agent_tasks_app(
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """List self-host agent tasks."""
    tenant = _require_selfhost_tenant(session)
    return list_service_agent_tasks(tenant_id=tenant.id, session=session)


@router.post("/agent/tasks", include_in_schema=False)
def create_agent_task_app(
    body: AgentTaskCreateBody,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Create one self-host agent task."""
    tenant = _require_selfhost_tenant(session)
    return create_service_agent_task(body, tenant_id=tenant.id, session=session)


@router.post("/agent/tasks/{task_id}/pause", include_in_schema=False)
def pause_agent_task_app(
    task_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Pause one self-host agent task."""
    tenant = _require_selfhost_tenant(session)
    return pause_service_agent_task(task_id, tenant_id=tenant.id, session=session)


@router.post("/agent/tasks/{task_id}/resume", include_in_schema=False)
def resume_agent_task_app(
    task_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Resume one self-host agent task."""
    tenant = _require_selfhost_tenant(session)
    return resume_service_agent_task(task_id, tenant_id=tenant.id, session=session)


@router.delete("/agent/tasks/{task_id}", include_in_schema=False)
def delete_agent_task_app(
    task_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Archive one self-host agent task."""
    tenant = _require_selfhost_tenant(session)
    return delete_service_agent_task(task_id, tenant_id=tenant.id, session=session)


@router.post("/agent/tasks/{task_id}/run", include_in_schema=False)
def run_agent_task_app(
    task_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Run one self-host agent task now."""
    tenant = _require_selfhost_tenant(session)
    return run_service_agent_task(task_id, request, tenant_id=tenant.id, session=session)


@router.get("/agent/runs", include_in_schema=False)
def list_agent_runs_app(
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """List self-host agent runs."""
    tenant = _require_selfhost_tenant(session)
    return list_service_agent_runs(tenant_id=tenant.id, session=session)


@router.post("/agent/runs", include_in_schema=False)
def create_agent_run_app(
    body: AgentRunCreateBody,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Run the self-host agent once."""
    tenant = _require_selfhost_tenant(session)
    return create_service_agent_run(body, tenant_id=tenant.id, session=session)


@router.get("/agent/runs/{run_id}", include_in_schema=False)
def get_agent_run_app(
    run_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return one self-host agent run."""
    tenant = _require_selfhost_tenant(session)
    return get_service_agent_run(run_id, tenant_id=tenant.id, session=session)


@router.get("/agent/runs/{run_id}/steps", include_in_schema=False)
def list_agent_run_steps_app(
    run_id: str,
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Return self-host agent run steps."""
    tenant = _require_selfhost_tenant(session)
    return list_service_agent_run_steps(run_id, tenant_id=tenant.id, session=session)


@router.get("/agent/candidates", include_in_schema=False)
def list_agent_candidates_app(
    run_id: str | None = None,
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """List self-host agent content candidates."""
    tenant = _require_selfhost_tenant(session)
    return list_service_agent_candidates(run_id=run_id, tenant_id=tenant.id, session=session)


@router.get("/agent/candidates/{candidate_id}", include_in_schema=False)
def get_agent_candidate_app(
    candidate_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return one self-host agent content candidate."""
    tenant = _require_selfhost_tenant(session)
    return get_service_agent_candidate(candidate_id, tenant_id=tenant.id, session=session)


@router.get("/agent/content-items/{content_item_id}/timeline", include_in_schema=False)
def get_agent_editor_timeline_app(
    content_item_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return self-host agent editor timeline for one content item."""
    tenant = _require_selfhost_tenant(session)
    return get_service_agent_editor_timeline(content_item_id, tenant_id=tenant.id, session=session)


@router.post("/agent/content-items/{content_item_id}/messages", include_in_schema=False)
def create_agent_editor_message_app(
    content_item_id: str,
    body: AgentEditorMessageCreateBody,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Send one self-host agent editor message."""
    tenant = _require_selfhost_tenant(session)
    return create_service_agent_editor_message(
        content_item_id,
        body,
        tenant_id=tenant.id,
        session=session,
    )


@router.get("/review-queue", include_in_schema=False)
def list_review_queue_app(
    status: str | None = None,
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """List self-host review queue items."""
    tenant = _require_selfhost_tenant(session)
    return list_service_review_queue(status=status, tenant_id=tenant.id, session=session)


@router.get("/review-queue/{review_item_id}", include_in_schema=False)
def get_review_queue_item_app(
    review_item_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return one self-host review queue item."""
    tenant = _require_selfhost_tenant(session)
    return get_service_review_queue_item(review_item_id, tenant_id=tenant.id, session=session)


@router.post("/review-queue/{review_item_id}/resolve", include_in_schema=False)
def resolve_review_queue_item_app(
    review_item_id: str,
    body: ReviewResolveBody,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Resolve one self-host review queue item."""
    tenant = _require_selfhost_tenant(session)
    return resolve_service_review_queue_item(
        review_item_id,
        body,
        tenant_id=tenant.id,
        session=session,
    )


@router.get("/agent/analytics/overview", include_in_schema=False)
def get_agent_analytics_overview_app(
    channel_id: str | None = None,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return self-host agent analytics overview."""
    tenant = _require_selfhost_tenant(session)
    return get_service_agent_analytics_overview(channel_id=channel_id, tenant_id=tenant.id, session=session)


@router.get("/agent/analytics/timeseries", include_in_schema=False)
def get_agent_analytics_timeseries_app(
    channel_id: str | None = None,
    days: int = Query(default=7, ge=1, le=365),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return self-host agent analytics timeseries."""
    tenant = _require_selfhost_tenant(session)
    return get_service_agent_analytics_timeseries(
        channel_id=channel_id,
        days=days,
        tenant_id=tenant.id,
        session=session,
    )


@router.get("/agent/analytics/quality", include_in_schema=False)
def get_agent_analytics_quality_app(
    channel_id: str | None = None,
    days: int | None = Query(default=None, ge=1, le=365),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return self-host agent quality analytics."""
    tenant = _require_selfhost_tenant(session)
    return get_service_agent_analytics_quality(
        channel_id=channel_id,
        days=days,
        tenant_id=tenant.id,
        session=session,
    )


@router.get("/agent/policies", include_in_schema=False)
def list_agent_policies_app(
    channel_id: str | None = None,
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]] | dict[str, Any]:
    """List self-host agent policies."""
    tenant = _require_selfhost_tenant(session)
    return list_service_agent_policies(channel_id=channel_id, tenant_id=tenant.id, session=session)


@router.put("/agent/policies", include_in_schema=False)
def upsert_agent_policy_app(
    body: AgentPolicyUpsertBody,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Create or update one self-host agent policy."""
    tenant = _require_selfhost_tenant(session)
    return upsert_service_agent_policy(body, tenant_id=tenant.id, session=session)


@router.post("/media/upload", include_in_schema=False)
async def upload_media(
    file: UploadFile = File(...),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Upload one self-host image asset."""
    tenant = _require_selfhost_tenant(session)
    data = await file.read()
    return store_media_asset(
        session,
        tenant_id=tenant.id,
        data=data,
        content_type=file.content_type or "",
    )


@router.post("/media/generation-jobs", status_code=202, include_in_schema=False)
def create_media_generation_job(
    body: MediaGenerationJobCreateRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Queue one self-host media generation job."""
    if not get_settings().ai_gateway_enabled:
        raise HTTPException(status_code=422, detail="AI gateway is disabled")
    tenant = _require_selfhost_tenant(session)
    correlation_id = getattr(request.state, "correlation_id", None) or "selfhost-app"
    if not any(
        (value or "").strip()
        for value in (body.prompt, body.title, body.summary, body.content_md)
    ):
        raise HTTPException(status_code=422, detail="prompt or post text is required")
    if body.content_item_id:
        row = get_postbridge_content_item(
            session,
            tenant_id=tenant.id,
            content_id=body.content_item_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="content item not found")
    payload = body.model_dump(exclude_none=True)
    job = MediaGenerationJobOrm(
        id=str(uuid4()),
        tenant_id=tenant.id,
        requester_user_id=LOCAL_ADMIN_USER_ID,
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
    return _media_generation_job_public_dict(job)


@router.get("/media/generation-jobs", include_in_schema=False)
def list_media_generation_jobs(
    limit: int = Query(default=10, ge=1, le=25),
    session: Session = Depends(get_db_session),
) -> dict[str, list[dict[str, Any]]]:
    """List recent self-host media generation jobs."""
    tenant = _require_selfhost_tenant(session)
    rows = list(
        session.scalars(
            select(MediaGenerationJobOrm)
            .where(MediaGenerationJobOrm.tenant_id == tenant.id)
            .order_by(MediaGenerationJobOrm.created_at.desc())
            .limit(limit)
        ).all()
    )
    return {"items": [_media_generation_job_public_dict(row) for row in rows]}


@router.get("/media/generation-jobs/{job_id}", include_in_schema=False)
def get_media_generation_job(
    job_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return one self-host media generation job."""
    _tenant, row = _require_selfhost_media_generation_job(session, job_id)
    return _media_generation_job_public_dict(row)


@router.get("/publication-targets/{target_id}", include_in_schema=False)
def get_publication_target(
    target_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return one self-host publication target."""
    tenant, row = _require_selfhost_publication_target(session, target_id)
    channel = session.get(ChannelOrm, row.channel_id)
    plan = session.get(PublicationPlanOrm, row.publication_plan_id)
    if channel is not None and channel.tenant_id != tenant.id:
        channel = None
    if plan is not None and plan.tenant_id != tenant.id:
        plan = None
    return _publication_target_public_dict(row, channel=channel, plan=plan)


@router.post("/publication-targets/{target_id}/dispatch", include_in_schema=False)
def dispatch_publication_target(
    target_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Enqueue one self-host publication target."""
    _tenant, row = _require_selfhost_publication_target(session, target_id)
    correlation_id = getattr(request.state, "correlation_id", None) or "selfhost-app"
    process_publication_target_task.delay(row.id, correlation_id)
    return {"status": "enqueued", "target_id": row.id}
