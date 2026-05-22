"""Browser-safe app API for the shared Core frontend."""

from __future__ import annotations

import base64
import hashlib
import hmac
import asyncio
import http.client
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import ssl
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urljoin, urlparse, urlunparse
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

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
from postbridge.ai.client import HttpAiGatewayClient
from postbridge.ai.factory import get_ai_gateway_client
from postbridge.config import get_settings
from postbridge.db import BatchImportFetchedPostOrm, BatchImportRunOrm, get_db_session
from postbridge.infrastructure.crypto.credentials import (
    decrypt_credential_secret,
    encrypt_credential_secret,
    get_fernet_for_credentials,
)
from postbridge.integrations.registry import (
    RULE_POST_TEXT_LIMITS,
    adapt_post_dict_for_platform,
    get_platform_capabilities,
)
from postbridge.integrations.telegram.publisher import TG_BOT_API, TelegramPublisher, _chat_id_from_channel
from postbridge.models.domain import (
    BridgeOrm,
    ChannelCredentialOrm,
    ChannelOrm,
    ContentItemOrm,
    InstallationSecretOrm,
    LlmProviderConfigOrm,
    MediaGenerationJobOrm,
    PublicationPlanOrm,
    PublicationTargetOrm,
    TenantOrm,
)
from postbridge.services.bridge_adaptation import adapt_post_for_bridge
from postbridge.services.ai_content import (
    adapt_content_for_channel,
    generate_and_plan,
    public_dict_for_generate_result,
    translate_content_for_channel,
)
from postbridge.services.live_sync_queue import queue_live_sync_publish
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
from postbridge.versioning import build_release_update_command, is_newer_version, normalize_version_tag
from postbridge.workers.media_generation_tasks import process_media_generation_job_task
from postbridge.workers.tasks import process_batch_import_run_task, process_publication_target_task

LOCAL_ADMIN_USER_ID = "local-admin"
LOCAL_ADMIN_SECRET_CATEGORY = "local_admin"
BRIDGE_MODES = frozenset({"live_sync", "migration"})
BRIDGE_STATUSES = frozenset({"active", "paused", "error"})
INSTALLATION_SECRET_CATEGORIES = frozenset(
    {
        "ai_gateway",
        "telegram_bot",
        "telegram_import",
        "media_storage",
        "max",
        "vk",
        "linkedin",
    }
)
INSTALLATION_SECRET_LABELS = {
    "ai_gateway": "AI Gateway",
    "telegram_bot": "Telegram Bot",
    "telegram_import": "Telegram Import",
    "media_storage": "Media Storage",
    "max": "MAX",
    "vk": "VK",
    "linkedin": "LinkedIn",
}
RSS_FEED_ID_MAX_LENGTH = 128
RSS_SOURCE_VALIDATION_MAX_REDIRECTS = 3
RSS_SOURCE_VALIDATION_TIMEOUT_SECONDS = 2.0
TELEGRAM_IMPORT_FLOW_TTL_SECONDS = 900
TELEGRAM_IMPORT_FLOW_CATEGORY = "telegram_import_flow"

AUTH_EXEMPT_PREFIXES = (
    "/api/app/auth/",
    "/api/app/gitsell-device/",
)
AUTH_EXEMPT_PATHS = {
    "/api/app/runtime-config",
    "/api/app/session",
    "/api/app/bootstrap",
    "/api/app/version-check",
    "/api/app/news",
}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _local_auth_secret() -> bytes:
    settings = get_settings()
    raw = settings.core_service_token or settings.credentials_encryption_key or ""
    if not raw.strip():
        raise HTTPException(status_code=503, detail="local auth secret is not configured")
    return raw.encode("utf-8")


def _hash_password(password: str, *, salt: str | None = None) -> str:
    salt_hex = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        260_000,
    ).hex()
    return f"pbkdf2_sha256$260000${salt_hex}${digest}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, expected = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        ).hex()
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False


def _make_local_session_token(username: str) -> str:
    exp = int(time.time()) + 30 * 24 * 60 * 60
    payload = _b64url(json.dumps({"sub": LOCAL_ADMIN_USER_ID, "username": username, "exp": exp}, separators=(",", ":")).encode("utf-8"))
    sig = _b64url(hmac.new(_local_auth_secret(), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def _verify_local_session_token(token: str) -> dict[str, Any] | None:
    if "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    expected = _b64url(hmac.new(_local_auth_secret(), payload.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(_b64url_decode(payload))
    except Exception:
        return None
    if data.get("sub") != LOCAL_ADMIN_USER_ID or int(data.get("exp") or 0) < int(time.time()):
        return None
    return data


def _require_bootstrap_crypto() -> None:
    try:
        get_fernet_for_credentials()
        _local_auth_secret()
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                "CREDENTIALS_ENCRYPTION_KEY must be set to a valid Fernet key before self-host setup. "
                "Run scripts/init_self_host_env.py or generate one with Fernet.generate_key()."
            ),
        ) from exc


def _find_selfhost_tenant_for_auth(session: Session) -> TenantOrm | None:
    settings = get_settings()
    row = session.get(TenantOrm, settings.postbridge_selfhost_tenant_id)
    if row is not None:
        return row
    count = int(session.scalar(select(func.count()).select_from(TenantOrm)) or 0)
    if count == 1:
        return session.scalar(select(TenantOrm).limit(1))
    return None


def _local_admin_secret_row(session: Session, tenant_id: str) -> InstallationSecretOrm | None:
    return session.scalar(
        select(InstallationSecretOrm).where(
            InstallationSecretOrm.tenant_id == tenant_id,
            InstallationSecretOrm.category == LOCAL_ADMIN_SECRET_CATEGORY,
        )
    )


def _decode_local_admin_secret(row: InstallationSecretOrm | None) -> dict[str, Any]:
    if row is None:
        return {}
    raw = decrypt_credential_secret(row.encrypted_secret)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_auth_exempt(path: str) -> bool:
    is_news_path = path == "/api/app/news" or bool(re.fullmatch(r"/api/app/news/[^/]+", path))
    return (
        path in AUTH_EXEMPT_PATHS
        or is_news_path
        or any(path.startswith(prefix) for prefix in AUTH_EXEMPT_PREFIXES)
    )


def require_selfhost_app_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db_session),
) -> None:
    settings = get_settings()
    if settings.postbridge_app_mode != "selfhost" or _is_auth_exempt(request.url.path):
        return
    if settings.app_env == "test" and os.getenv("POSTBRIDGE_TEST_REQUIRE_AUTH") != "1":
        return
    tenant = _find_selfhost_tenant_for_auth(session)
    if tenant is None:
        raise HTTPException(status_code=409, detail="self-host tenant is not bootstrapped")
    if _local_admin_secret_row(session, tenant.id) is None:
        raise HTTPException(status_code=403, detail="self-host admin is not configured")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip() or _verify_local_session_token(token.strip()) is None:
        raise HTTPException(status_code=401, detail="authentication required")


logger = logging.getLogger(__name__)

public_router = APIRouter(prefix="/api/app")
router = APIRouter(prefix="/api/app", dependencies=[Depends(require_selfhost_app_auth)])


class LiveSyncQueueError(RuntimeError):
    def __init__(self, queued_count: int):
        super().__init__("live sync job queueing failed")
        self.queued_count = queued_count


class BootstrapRequest(BaseModel):
    tenant_name: str | None = Field(default="Postbridge Self-host", max_length=256)
    admin_username: str = Field(default="admin", min_length=1, max_length=128)
    admin_password: str | None = Field(default=None, min_length=8, max_length=512)
    current_admin_password: str | None = Field(default=None, min_length=8, max_length=512)
    locale: str | None = Field(default=None, max_length=16)
    installation_secrets: dict[str, "InstallationSecretUpsertRequest"] | None = None


class LocalLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class GitsellDeviceStartRequest(BaseModel):
    locale: str = Field(default="en", min_length=2, max_length=16)
    instance_id: str = Field(min_length=8, max_length=128)
    instance_label: str | None = Field(default="Postbridge Self-host", max_length=128)


class GitsellDevicePollRequest(BaseModel):
    locale: str = Field(default="en", min_length=2, max_length=16)
    device_code: str = Field(min_length=8, max_length=512)


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


class InstallationSecretUpsertRequest(BaseModel):
    secret: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    status: str = Field(default="configured", min_length=1, max_length=32)


class TelegramImportStartRequest(BaseModel):
    api_id: str = Field(min_length=1, max_length=32)
    api_hash: str = Field(min_length=1, max_length=128)
    phone: str = Field(min_length=5, max_length=32)


class TelegramImportCompleteRequest(BaseModel):
    flow_id: str = Field(min_length=1, max_length=128)
    code: str | None = Field(default=None, max_length=32)
    password: str | None = Field(default=None, max_length=256)


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


class PlatformPreviewRequest(BaseModel):
    content_md: str | None = Field(default=None, max_length=500_000)
    content: str | None = Field(default=None, max_length=500_000)
    title: str | None = Field(default=None, max_length=512)
    summary: str | None = Field(default=None, max_length=2048)
    link_url: str | None = Field(default=None, max_length=2048)
    cta: str | None = Field(default=None, max_length=512)
    content_item_id: str | None = Field(default=None, max_length=36)
    include_ai_adaptation: bool = False


def _json_dumps_or_none(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads_or_empty(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _json_compatible_dict(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    return json.loads(json.dumps(value, ensure_ascii=False))


def _bool_capability(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


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
        if not value:
            raise HTTPException(status_code=400, detail="connections.validation.rss.url_required")
        if value.startswith("http://") or value.startswith("https://"):
            return value
        raise HTTPException(status_code=400, detail="connections.validation.rss.url_invalid")
    if normalized_platform == "postbridge":
        return value
    raise HTTPException(status_code=422, detail="invalid platform")


def _normalize_rss_target_feed_id(raw: str | None) -> str:
    value = (raw or "rss").strip()
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")
    if not value:
        value = "rss"
    if len(value) > RSS_FEED_ID_MAX_LENGTH:
        raise HTTPException(status_code=422, detail="connections.validation.rss.feed_id_too_long")
    return value


def _rss_target_feed_id_for_url(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value or len(value) > RSS_FEED_ID_MAX_LENGTH:
        return None
    if re.search(r"[^a-zA-Z0-9_-]", value):
        return None
    return value


def _public_rss_host_addresses(hostname: str | None) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    host = (hostname or "").strip()
    if not host:
        return []
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            resolved = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror:
            return []
        addresses = []
        for item in resolved:
            sockaddr = item[4]
            if sockaddr:
                addresses.append(ipaddress.ip_address(sockaddr[0]))
    unique_addresses = list(dict.fromkeys(addresses))
    if not unique_addresses or not all(address.is_global for address in unique_addresses):
        return []
    return unique_addresses


def _is_public_rss_host(hostname: str | None) -> bool:
    return bool(_public_rss_host_addresses(hostname))


def _validate_public_rss_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and _is_public_rss_host(parsed.hostname)


def _fetch_public_rss_url_once(value: str) -> httpx.Response | None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    addresses = _public_rss_host_addresses(parsed.hostname)
    if not addresses:
        return None
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, ""))
    host_header = parsed.netloc
    sock = socket.create_connection(
        (addresses[0].compressed, port),
        timeout=RSS_SOURCE_VALIDATION_TIMEOUT_SECONDS,
    )
    try:
        if parsed.scheme == "https":
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=host)
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "User-Agent: Postbridge RSS Validator\r\n"
            "Accept: application/rss+xml, application/xml, text/xml, */*;q=0.1\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = http.client.HTTPResponse(sock)
        response.begin()
        return httpx.Response(
            response.status,
            headers=response.getheaders(),
            request=httpx.Request("GET", value),
        )
    finally:
        sock.close()


def _validate_rss_source_url(url: str) -> list[str]:
    value = (url or "").strip()
    if not value:
        return ["connections.validation.rss.url_required"]
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ["connections.validation.rss.url_invalid"]
    current_url = value
    try:
        for _ in range(RSS_SOURCE_VALIDATION_MAX_REDIRECTS + 1):
            response = _fetch_public_rss_url_once(current_url)
            if response is None:
                return ["connections.validation.rss.url_invalid"]
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            location = response.headers.get("location")
            if not location:
                return ["connections.validation.rss.unreachable"]
            current_url = urljoin(str(response.url), location)
        else:
            return ["connections.validation.rss.unreachable"]
        response.raise_for_status()
    except (httpx.HTTPError, OSError, ssl.SSLError, http.client.HTTPException, UnicodeError):
        return ["connections.validation.rss.unreachable"]
    return []


def _tenant_public_dict(row: TenantOrm) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _local_admin_public_dict(username: str = "admin") -> dict[str, Any]:
    return {
        "id": LOCAL_ADMIN_USER_ID,
        "display_name": username or "admin",
        "username": username or "admin",
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
    can_read = _bool_capability(capabilities.get("can_read"), row.kind in {"source", "both"})
    can_write = _bool_capability(capabilities.get("can_write"), row.kind in {"destination", "target", "both"})
    payload = {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "platform": row.platform,
        "kind": row.kind,
        "title": row.title,
        "external_id": row.external_id,
        "platform_channel_id": row.external_id,
        "display": row.title,
        "can_read": can_read,
        "can_write": can_write,
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
    if row.platform == "rss" and can_write and get_settings().postbridge_app_mode == "selfhost":
        feed_id = _rss_target_feed_id_for_url(row.external_id or row.id)
        if feed_id:
            payload["rss_feed_url"] = f"/rss/{quote(feed_id, safe='')}.xml"
    return payload


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


def _installation_secret_public_dict(row: InstallationSecretOrm) -> dict[str, Any]:
    return {
        "category": row.category,
        "label": INSTALLATION_SECRET_LABELS.get(row.category, row.category),
        "status": row.status,
        "configured": bool((row.encrypted_secret or "").strip()),
        "config": _json_loads_or_empty(row.config_json),
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _empty_installation_secret_public_dict(category: str) -> dict[str, Any]:
    return {
        "category": category,
        "label": INSTALLATION_SECRET_LABELS.get(category, category),
        "status": "missing",
        "configured": False,
        "config": {},
        "created_at": None,
        "updated_at": None,
    }


def _gitsell_origin_for_locale(locale: str) -> str:
    normalized = (locale or "").strip().lower()
    return "https://gitsell.ru" if normalized.startswith("ru") else "https://gitsell.tech"


def _gitsell_gateway_config(locale: str) -> dict[str, str]:
    settings = get_settings()
    origin = _gitsell_origin_for_locale(locale)
    return {
        "origin": origin,
        "base_url": f"{origin}/api/v1",
        "default_model": settings.ai_gateway_default_model or settings.agent_llm_default_model or "gpt-5.4-mini",
        "image_model": settings.ai_image_generation_model or "gpt-image-2",
        "image_size": settings.ai_image_generation_size or "1536x1024",
    }


def _gitsell_error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:300] or f"HTTP {response.status_code}"
    if isinstance(data, dict):
        return str(
            data.get("error_description")
            or data.get("detail")
            or data.get("message")
            or data.get("error")
            or f"HTTP {response.status_code}"
        )
    return f"HTTP {response.status_code}"


def _welcome_post_locale(locale: str | None) -> str:
    normalized = (locale or "").strip().lower()
    if normalized.startswith("ru"):
        return "ru"
    return "en"


def _welcome_post_payload(locale: str | None) -> dict[str, Any]:
    if _welcome_post_locale(locale) == "ru":
        return {
            "title": "\u0414\u043e\u0431\u0440\u043e \u043f\u043e\u0436\u0430\u043b\u043e\u0432\u0430\u0442\u044c \u0432 Postbridge",
            "summary": "\u0421\u0442\u0430\u0440\u0442\u043e\u0432\u044b\u0439 \u043f\u043e\u0441\u0442 \u0434\u043b\u044f \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438 self-host \u0440\u0430\u0431\u043e\u0447\u0435\u0433\u043e \u043f\u0440\u043e\u0441\u0442\u0440\u0430\u043d\u0441\u0442\u0432\u0430.",
            "content_md": "\n".join(
                [
                    "# \u0414\u043e\u0431\u0440\u043e \u043f\u043e\u0436\u0430\u043b\u043e\u0432\u0430\u0442\u044c \u0432 Postbridge",
                    "",
                    "\u042d\u0442\u043e \u043f\u0435\u0440\u0432\u044b\u0439 \u043f\u043e\u0441\u0442 \u0432 \u0432\u0430\u0448\u0435\u043c self-host workspace. \u0415\u0433\u043e \u043c\u043e\u0436\u043d\u043e \u0438\u0437\u043c\u0435\u043d\u0438\u0442\u044c, \u0430\u0434\u0430\u043f\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0447\u0435\u0440\u0435\u0437 \u0418\u0418 \u0438 \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u0432 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0451\u043d\u043d\u044b\u0435 \u043a\u0430\u043d\u0430\u043b\u044b.",
                    "",
                    "\u0427\u0442\u043e \u0434\u0430\u043b\u044c\u0448\u0435:",
                    "",
                    "1. \u0414\u043e\u0431\u0430\u0432\u044c\u0442\u0435 \u043a\u0430\u043d\u0430\u043b-\u043f\u0440\u0438\u0451\u043c\u043d\u0438\u043a: RSS, Telegram, VK, MAX \u0438\u043b\u0438 \u0434\u0440\u0443\u0433\u0443\u044e \u043f\u043b\u0430\u0442\u0444\u043e\u0440\u043c\u0443.",
                    "2. \u0421\u043e\u0437\u0434\u0430\u0439\u0442\u0435 \u043c\u043e\u0441\u0442 \u0438\u0437 Postbridge Source \u0432 \u0446\u0435\u043b\u0435\u0432\u043e\u0439 \u043a\u0430\u043d\u0430\u043b.",
                    "3. \u041e\u0442\u0440\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u0443\u0439\u0442\u0435 \u044d\u0442\u043e\u0442 \u043f\u043e\u0441\u0442 \u0438\u043b\u0438 \u0441\u043e\u0437\u0434\u0430\u0439\u0442\u0435 \u043d\u043e\u0432\u044b\u0439.",
                ]
            ),
        }
    return {
        "title": "Welcome to Postbridge",
        "summary": "A starter post for checking your self-host workspace.",
        "content_md": "\n".join(
            [
                "# Welcome to Postbridge",
                "",
                "This is the first post in your self-host workspace. You can edit it, adapt it with AI, and send it to connected channels.",
                "",
                "Next steps:",
                "",
                "1. Add a target channel: RSS, Telegram, VK, MAX, or another platform.",
                "2. Create a bridge from Postbridge Source to that target channel.",
                "3. Edit this post or create a new one.",
            ]
        ),
    }


def _ensure_selfhost_welcome_content(
    session: Session,
    *,
    tenant_id: str,
    locale: str | None,
) -> None:
    source = session.scalar(
        select(ChannelOrm)
        .where(
            ChannelOrm.tenant_id == tenant_id,
            ChannelOrm.platform == "postbridge",
            ChannelOrm.external_id == "postbridge-local",
        )
        .limit(1)
    )
    now = datetime.now(UTC)
    if source is None:
        source = ChannelOrm(
            id=str(uuid4()),
            tenant_id=tenant_id,
            platform="postbridge",
            kind="source",
            title="Postbridge Source",
            external_id="postbridge-local",
            status="connected",
            config_json=_json_dumps_or_none({}),
            capabilities_json=_json_dumps_or_none(
                {"can_read": True, "can_write": False, "live_sync_source_supported": True}
            ),
            created_at=now,
            updated_at=now,
        )
        session.add(source)
        session.flush()
    existing_welcome_id = session.scalar(
        select(ContentItemOrm.id)
        .where(
            ContentItemOrm.tenant_id == tenant_id,
            ContentItemOrm.source_type == "postbridge",
            ContentItemOrm.body_structured_json.contains('"welcome"'),
        )
        .limit(1)
    )
    if existing_welcome_id:
        return
    payload = _welcome_post_payload(locale)
    row = create_postbridge_content_item(
        session,
        tenant_id=tenant_id,
        author_user_id=LOCAL_ADMIN_USER_ID,
        content_md=payload["content_md"],
        content_plain=None,
        media_url=None,
        media_urls=None,
        title=payload["title"],
        summary=payload["summary"],
        link_url=None,
        cta=None,
        tags=["welcome"],
        author="Postbridge",
        cover_image_url=None,
        status="draft",
        live_sync_source_core_channel_id=None,
    )
    row.language = _welcome_post_locale(locale)
    session.add(row)


def _require_installation_secret_category(category: str) -> str:
    normalized = category.strip().lower().replace("-", "_")
    if normalized not in INSTALLATION_SECRET_CATEGORIES:
        raise HTTPException(status_code=404, detail="installation secret category not found")
    return normalized


def _installation_secret_payload(
    session: Session,
    *,
    tenant_id: str,
    category: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    row = session.scalar(
        select(InstallationSecretOrm).where(
            InstallationSecretOrm.tenant_id == tenant_id,
            InstallationSecretOrm.category == category,
        )
    )
    if row is None:
        return {}, {}
    config = _json_loads_or_empty(row.config_json)
    secret: dict[str, Any] = {}
    if not (row.encrypted_secret or "").strip():
        return config, secret
    raw_secret = decrypt_credential_secret(row.encrypted_secret)
    if raw_secret:
        try:
            parsed = json.loads(raw_secret)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"{category} installation secret payload is not valid JSON",
            ) from exc
        if isinstance(parsed, dict):
            secret = parsed
    return config, secret


def _has_installation_secret_payload(secret: dict[str, Any] | None) -> bool:
    if not secret:
        return False
    return any(value is not None and str(value).strip() for value in secret.values())


def _upsert_installation_secret_row(
    session: Session,
    *,
    tenant_id: str,
    category: str,
    secret: dict[str, Any] | None,
    config: dict[str, Any] | None = None,
    status: str = "configured",
) -> InstallationSecretOrm:
    now = datetime.now(UTC)
    row = session.scalar(
        select(InstallationSecretOrm).where(
            InstallationSecretOrm.tenant_id == tenant_id,
            InstallationSecretOrm.category == category,
        )
    )
    encrypted_secret = (
        encrypt_credential_secret(_json_dumps_or_none(secret))
        if _has_installation_secret_payload(secret)
        else None
    )
    if row is None:
        row = InstallationSecretOrm(
            id=str(uuid4()),
            tenant_id=tenant_id,
            category=category,
            status=status.strip().lower(),
            encrypted_secret=encrypted_secret,
            config_json=_json_dumps_or_none(config or {}),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.status = status.strip().lower()
        row.encrypted_secret = encrypted_secret
        row.config_json = _json_dumps_or_none(config or {})
        row.updated_at = now
    return row


def _telegram_import_flow_row(session: Session, *, tenant_id: str) -> InstallationSecretOrm | None:
    return session.scalar(
        select(InstallationSecretOrm).where(
            InstallationSecretOrm.tenant_id == tenant_id,
            InstallationSecretOrm.category == TELEGRAM_IMPORT_FLOW_CATEGORY,
        )
    )


def _cleanup_telegram_import_flow(session: Session, *, tenant_id: str) -> None:
    row = _telegram_import_flow_row(session, tenant_id=tenant_id)
    if row is None:
        return
    _config, secret = _installation_secret_payload(
        session,
        tenant_id=tenant_id,
        category=TELEGRAM_IMPORT_FLOW_CATEGORY,
    )
    if float(secret.get("expires_at") or 0) <= time.time():
        session.delete(row)
        session.commit()


def _load_telegram_import_flow(
    session: Session,
    *,
    tenant_id: str,
    flow_id: str,
) -> tuple[InstallationSecretOrm, dict[str, Any]]:
    row = _telegram_import_flow_row(session, tenant_id=tenant_id)
    if row is None or row.status != "pending":
        raise HTTPException(status_code=404, detail="Telegram login flow expired")
    _config, secret = _installation_secret_payload(
        session,
        tenant_id=tenant_id,
        category=TELEGRAM_IMPORT_FLOW_CATEGORY,
    )
    if secret.get("flow_id") != flow_id or float(secret.get("expires_at") or 0) <= time.time():
        session.delete(row)
        session.commit()
        raise HTTPException(status_code=404, detail="Telegram login flow expired")
    return row, secret


def _new_telegram_string_session(session_string: str | None = None):
    from telethon.sessions import StringSession

    return StringSession(session_string)


def _new_telegram_client(session_obj, api_id: int, api_hash: str):
    from telethon import TelegramClient

    return TelegramClient(session_obj, api_id=api_id, api_hash=api_hash)


def _telegram_password_required_error_type():
    from telethon.errors import SessionPasswordNeededError

    return SessionPasswordNeededError


def _save_telegram_session(session_obj) -> str:
    save = getattr(session_obj, "save", None)
    if callable(save):
        return str(save())
    from telethon.sessions import StringSession

    return str(StringSession.save(session_obj))


async def _telegram_import_send_code(*, api_id: int, api_hash: str, phone: str) -> tuple[str, str]:
    session_obj = _new_telegram_string_session()
    client = _new_telegram_client(session_obj, api_id, api_hash)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        return _save_telegram_session(session_obj), str(sent.phone_code_hash)
    finally:
        await client.disconnect()


async def _telegram_import_sign_in(
    *,
    session_string: str,
    api_id: int,
    api_hash: str,
    phone: str,
    phone_code_hash: str,
    code: str | None,
    password: str | None,
) -> tuple[str, dict[str, Any], bool]:
    session_obj = _new_telegram_string_session(session_string)
    client = _new_telegram_client(session_obj, api_id, api_hash)
    await client.connect()
    try:
        try:
            if password:
                await client.sign_in(password=password)
            else:
                await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except _telegram_password_required_error_type():
            return _save_telegram_session(session_obj), {}, True
        me = await client.get_me()
        account = {
            "id": str(getattr(me, "id", "") or ""),
            "username": getattr(me, "username", None),
            "phone": getattr(me, "phone", None),
            "first_name": getattr(me, "first_name", None),
            "last_name": getattr(me, "last_name", None),
        }
        return _save_telegram_session(session_obj), account, False
    finally:
        await client.disconnect()


def _sync_ai_gateway_provider_from_installation_secret(session: Session, *, tenant_id: str) -> None:
    config, secret = _installation_secret_payload(
        session,
        tenant_id=tenant_id,
        category="ai_gateway",
    )
    base_url = str(config.get("base_url") or secret.get("base_url") or "").strip()
    model_name = str(config.get("default_model") or secret.get("default_model") or "").strip()
    if not base_url or not model_name:
        return
    api_key = str(secret.get("api_key") or "").strip() or None
    capabilities = {
        key: value
        for key, value in {
            "image_model": config.get("image_model") or secret.get("image_model"),
            "image_size": config.get("image_size") or secret.get("image_size"),
            "embedding_model": config.get("embedding_model") or secret.get("embedding_model"),
        }.items()
        if value is not None and str(value).strip()
    }
    row = session.scalar(
        select(LlmProviderConfigOrm)
        .where(
            LlmProviderConfigOrm.tenant_id == tenant_id,
            LlmProviderConfigOrm.is_default.is_(True),
        )
        .limit(1)
    )
    now = datetime.now(UTC)
    if row is None:
        row = LlmProviderConfigOrm(
            id=str(uuid4()),
            tenant_id=tenant_id,
            provider_type="openai_compatible",
            label="AI Gateway",
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            capabilities_json=_json_dumps_or_none(capabilities),
            auth_config_json=None,
            is_default=True,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return
    row.provider_type = "openai_compatible"
    row.label = row.label or "AI Gateway"
    row.base_url = base_url
    row.api_key = api_key
    row.model_name = model_name
    row.capabilities_json = _json_dumps_or_none(capabilities)
    row.updated_at = now
    session.flush()


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


def _capability_flag_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _attach_telegram_bot_credential_if_available(
    session: Session,
    *,
    tenant_id: str,
    channel: ChannelOrm,
) -> None:
    if channel.platform != "telegram":
        return
    capabilities = _json_loads_or_empty(channel.capabilities_json)
    can_write = capabilities.get("can_write")
    if can_write is None:
        can_write = channel.kind in {"both", "destination", "target"}
    if not _capability_flag_enabled(can_write):
        return
    if _credential_for_channel(session, tenant_id=tenant_id, channel_id=channel.id) is not None:
        return
    _config, secret = _installation_secret_payload(
        session,
        tenant_id=tenant_id,
        category="telegram_bot",
    )
    bot_token = str(secret.get("bot_token") or "").strip()
    if not bot_token:
        return
    now = datetime.now(UTC)
    session.add(
        ChannelCredentialOrm(
            id=str(uuid4()),
            tenant_id=tenant_id,
            channel_id=channel.id,
            auth_type="telegram_bot",
            encrypted_secret=encrypt_credential_secret(
                _json_dumps_or_none({"bot_token": bot_token})
            ),
            status="active",
            created_at=now,
            updated_at=now,
        )
    )


def _validate_telegram_bot_target_access(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
) -> list[str]:
    _config, secret = _installation_secret_payload(
        session,
        tenant_id=tenant_id,
        category="telegram_bot",
    )
    bot_token = str(secret.get("bot_token") or "").strip()
    if not bot_token:
        return ["connections.validation.telegram.bot_not_configured"]

    publisher = TelegramPublisher()
    try:
        me_response = publisher._request_bot_api("GET", f"{TG_BOT_API}{bot_token}/getMe")
        me_response.raise_for_status()
        me = me_response.json()
        bot_id = ((me.get("result") or {}) if isinstance(me, dict) else {}).get("id")
        if bot_id is None:
            return ["connections.validation.telegram.admin_check_failed"]

        member_response = publisher._request_bot_api(
            "GET",
            f"{TG_BOT_API}{bot_token}/getChatMember",
            params={"chat_id": _chat_id_from_channel(channel_id), "user_id": bot_id},
        )
        member_response.raise_for_status()
        member = member_response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            return ["connections.validation.telegram.bot_not_configured"]
        return ["connections.validation.telegram.target_bot_admin_required"]
    except (httpx.RequestError, ValueError):
        return ["connections.validation.telegram.admin_check_failed"]

    result = (member.get("result") or {}) if isinstance(member, dict) else {}
    status = str(result.get("status") or "").lower()
    if status not in {"administrator", "creator"}:
        return ["connections.validation.telegram.target_bot_admin_required"]
    if status == "administrator" and result.get("can_post_messages") is not True:
        return ["connections.validation.telegram.target_bot_admin_required"]
    return []


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
    if target.platform == "postbridge":
        raise HTTPException(status_code=400, detail="postbridge channel cannot be a bridge target")
    if not _channel_can_read(source):
        raise HTTPException(status_code=400, detail="source channel cannot be read")
    if not _channel_can_write(target):
        raise HTTPException(status_code=400, detail="target channel cannot publish")
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


def _platform_preview_post_dict(body: PlatformPreviewRequest) -> dict[str, Any]:
    content = (body.content_md if body.content_md is not None else body.content) or ""
    text_parts = [
        (body.title or "").strip(),
        content.strip(),
        (body.summary or "").strip(),
        (body.link_url or "").strip(),
        (body.cta or "").strip(),
    ]
    text = "\n\n".join(part for part in text_parts if part)
    return {
        "id": body.content_item_id,
        "title": (body.title or "").strip(),
        "text": text,
        "summary": (body.summary or "").strip(),
        "link_url": (body.link_url or "").strip(),
        "cta": (body.cta or "").strip(),
    }


def _channel_can_write(row: ChannelOrm) -> bool:
    capabilities = _json_loads_or_empty(row.capabilities_json)
    return _bool_capability(capabilities.get("can_write"), row.kind in {"destination", "target", "both"})


def _channel_can_read(row: ChannelOrm) -> bool:
    capabilities = _json_loads_or_empty(row.capabilities_json)
    return _bool_capability(capabilities.get("can_read"), row.kind in {"source", "both"})


def _selfhost_platform_preview_items(
    session: Session,
    *,
    tenant_id: str,
    body: PlatformPreviewRequest,
) -> list[dict[str, Any]]:
    source_channel = aliased(ChannelOrm)
    target_channel = aliased(ChannelOrm)
    rows = session.execute(
        select(BridgeOrm, source_channel, target_channel)
        .join(source_channel, BridgeOrm.source_channel_id == source_channel.id)
        .join(target_channel, BridgeOrm.target_channel_id == target_channel.id)
        .where(
            BridgeOrm.tenant_id == tenant_id,
            BridgeOrm.saas_user_id == LOCAL_ADMIN_USER_ID,
            BridgeOrm.status == "active",
            BridgeOrm.mode == "live_sync",
            source_channel.tenant_id == tenant_id,
            source_channel.platform == "postbridge",
            target_channel.tenant_id == tenant_id,
        )
        .order_by(target_channel.platform.asc(), target_channel.title.asc())
    ).all()
    post = _platform_preview_post_dict(body)
    items_by_group: dict[str, dict[str, Any]] = {}
    for bridge, _source, target in rows:
        if not _channel_can_write(target):
            continue
        platform = target.platform
        settings = _json_loads_or_empty(bridge.settings_json)
        settings_fingerprint = hashlib.sha256(
            json.dumps(settings, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        group_key = f"{platform}:{settings_fingerprint}"
        item = items_by_group.get(group_key)
        if item is None:
            adapted_text = adapt_post_dict_for_platform(post, platform)
            limit = RULE_POST_TEXT_LIMITS.get(platform)
            item = {
                "id": group_key,
                "platform": platform,
                "targets": [],
                "text": adapted_text,
                "limit": limit,
                "adapted_length": len(adapted_text),
                "adaptation_mode": settings.get("adaptation_mode", "rule_only"),
                "adaptation_status": "ready",
                "fallback_used": False,
                "truncated": bool(limit is not None and len(adapted_text) > limit),
            }
            items_by_group[group_key] = item
        item["targets"].append(
            {
                "id": target.id,
                "title": target.title,
                "platform_channel_id": target.external_id,
                "bridge_id": bridge.id,
            }
        )
    return list(items_by_group.values())


def _append_link_url(text: str, link_url: str | None) -> str:
    cleaned = (text or "").strip()
    link = (link_url or "").strip()
    if not link or link in cleaned:
        return cleaned
    return f"{cleaned}\n\n{link}".strip()


def _compose_selfhost_source_text(data: dict[str, Any]) -> str:
    title = str(data.get("title") or "").strip()
    content = str(data.get("content_plain") or data.get("content_md") or "").strip()
    if title and content and title not in content.splitlines()[:1]:
        return f"{title}\n\n{content}"
    return content or title


def _infer_postbridge_live_sync_source_channel_id(session: Session, *, tenant_id: str) -> str | None:
    source = aliased(ChannelOrm)
    default_source_id = session.scalar(
        select(source.id)
        .join(BridgeOrm, BridgeOrm.source_channel_id == source.id)
        .where(
            source.tenant_id == tenant_id,
            source.platform == "postbridge",
            source.external_id == "postbridge-local",
            BridgeOrm.tenant_id == tenant_id,
            BridgeOrm.status == "active",
            BridgeOrm.mode == "live_sync",
        )
        .limit(1)
    )
    if default_source_id:
        return default_source_id
    return session.scalar(
        select(source.id)
        .join(BridgeOrm, BridgeOrm.source_channel_id == source.id)
        .where(
            source.tenant_id == tenant_id,
            source.platform == "postbridge",
            BridgeOrm.tenant_id == tenant_id,
            BridgeOrm.status == "active",
            BridgeOrm.mode == "live_sync",
        )
        .order_by(source.created_at.asc())
        .limit(1)
    )


def _selfhost_immediate_live_sync_jobs(
    session: Session,
    *,
    row: ContentItemOrm,
    source_channel_id: str | None,
) -> list[dict[str, Any]]:
    src_uuid = source_channel_id or _infer_postbridge_live_sync_source_channel_id(
        session,
        tenant_id=row.tenant_id,
    )
    if not src_uuid:
        return []
    source_ch = session.get(ChannelOrm, src_uuid)
    if source_ch is None or source_ch.tenant_id != row.tenant_id or source_ch.platform != "postbridge":
        return []
    data = content_item_to_api_dict(row)
    text_base = _compose_selfhost_source_text(data)
    out_media_url = row.media_url or data.get("cover_image_url")
    out_media_urls = list(row.media_urls) if row.media_urls else None
    if out_media_url and (not out_media_urls or out_media_url not in out_media_urls):
        out_media_urls = [out_media_url] + [url for url in (out_media_urls or []) if url != out_media_url]
    source_post = {
        "text": text_base,
        "title": data.get("title"),
        "summary": data.get("summary"),
        "cta": data.get("cta"),
        "link_url": data.get("link_url"),
    }
    base_post = {
        "source_post_id": row.id,
        "media_url": out_media_url,
        "media_urls": out_media_urls,
    }
    jobs: list[dict[str, Any]] = []
    bridges = session.scalars(
        select(BridgeOrm).where(
            BridgeOrm.tenant_id == row.tenant_id,
            BridgeOrm.source_channel_id == source_ch.id,
            BridgeOrm.status == "active",
            BridgeOrm.mode == "live_sync",
        )
    ).all()
    for bridge in bridges:
        target = session.get(ChannelOrm, bridge.target_channel_id)
        if target is None or target.tenant_id != row.tenant_id or not _channel_can_write(target):
            continue
        adaptation = adapt_post_for_bridge(
            session,
            tenant_id=row.tenant_id,
            post=source_post,
            platform=target.platform,
            bridge_settings=bridge.settings_json,
            target_channel_id=target.id,
            content_item_id=row.id,
        )
        if adaptation.status == "needs_review":
            continue
        link_url = data.get("link_url")
        post_text = _append_link_url(adaptation.text, link_url) if target.platform == "rss" else adaptation.text
        post = {**base_post, "text": post_text}
        if link_url:
            post["link_url"] = link_url
        jobs.append(
            {
                "source_channel": source_ch.external_id or source_ch.id,
                "target_channel": target.external_id or target.id,
                "post": post,
                "workspace_id": data.get("saas_workspace_id") or "",
                "target_platform": target.platform,
                "core_tenant_id": row.tenant_id,
                "target_core_channel_id": target.id,
            }
        )
    return jobs


def _enqueue_selfhost_live_sync_jobs(jobs: list[dict[str, Any]]) -> None:
    queued_count = 0
    for job in jobs:
        try:
            queue_live_sync_publish(
                source_channel=job["source_channel"],
                target_channel=job["target_channel"],
                post=job["post"],
                workspace_id=job["workspace_id"],
                target_platform=job["target_platform"],
                core_tenant_id=job["core_tenant_id"],
                target_core_channel_id=job["target_core_channel_id"],
                producer="postbridge_editor",
            )
            queued_count += 1
        except Exception as exc:
            raise LiveSyncQueueError(queued_count) from exc


def _content_item_snapshot(row: ContentItemOrm) -> dict[str, Any]:
    return {
        "author_user_id": row.author_user_id,
        "source_type": row.source_type,
        "title": row.title,
        "body_markdown": row.body_markdown,
        "body_structured_json": row.body_structured_json,
        "language": row.language,
        "status": row.status,
        "media_url": row.media_url,
        "media_urls": list(row.media_urls) if row.media_urls else None,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _restore_content_item_snapshot(row: ContentItemOrm, snapshot: dict[str, Any]) -> None:
    row.author_user_id = snapshot["author_user_id"]
    row.source_type = snapshot["source_type"]
    row.title = snapshot["title"]
    row.body_markdown = snapshot["body_markdown"]
    row.body_structured_json = snapshot["body_structured_json"]
    row.language = snapshot["language"]
    row.status = snapshot["status"]
    row.media_url = snapshot["media_url"]
    row.media_urls = snapshot["media_urls"]
    row.created_at = snapshot["created_at"]
    row.updated_at = snapshot["updated_at"]


def _revert_new_content_item_publish(row: ContentItemOrm) -> None:
    data = _json_loads_or_empty(row.body_structured_json)
    extra = data.get("postbridge_extra") if isinstance(data.get("postbridge_extra"), dict) else {}
    extra.pop("published_at", None)
    if not extra.get("scheduled_publish_at"):
        extra.pop("live_sync_source_core_channel_id", None)
    if extra:
        data["postbridge_extra"] = extra
    else:
        data.pop("postbridge_extra", None)
    row.body_structured_json = _json_dumps_or_none(data) if data else None
    row.status = "draft"
    row.updated_at = datetime.now(UTC)


def _enqueue_selfhost_live_sync_jobs_or_revert(
    session: Session,
    *,
    row: ContentItemOrm,
    jobs: list[dict[str, Any]],
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    try:
        _enqueue_selfhost_live_sync_jobs(jobs)
    except LiveSyncQueueError as exc:
        if exc.queued_count == 0:
            if snapshot is None:
                _revert_new_content_item_publish(row)
            else:
                _restore_content_item_snapshot(row, snapshot)
            session.commit()
            raise HTTPException(status_code=503, detail="live sync job queueing failed") from exc
        logger.warning(
            "live sync job queueing partially failed after %s queued jobs; keeping content published to avoid duplicate retries",
            exc.queued_count,
        )
        return {
            "code": "live_sync_partial_queue_failure",
            "message": "Some live sync targets were not queued. Check channels and retry missing targets manually.",
            "queued_count": exc.queued_count,
            "failed_count": max(0, len(jobs) - exc.queued_count),
        }
    return None


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


def _release_latest_api_url(release_source: str) -> str | None:
    value = (release_source or "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        if parsed.netloc == "api.github.com" and parsed.path.endswith("/releases/latest"):
            return value
        if parsed.netloc in {"github.com", "www.github.com"}:
            parts = [part for part in parsed.path.strip("/").split("/") if part]
            if len(parts) >= 2:
                return f"https://api.github.com/repos/{parts[0]}/{parts[1]}/releases/latest"
        return None
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        owner, repo = value.split("/", 1)
        return f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    return None


def _require_ai_enabled(session: Session, tenant_id: str) -> None:
    if get_settings().ai_gateway_enabled:
        return
    config, secret = _installation_secret_payload(
        session,
        tenant_id=tenant_id,
        category="ai_gateway",
    )
    if config.get("base_url") or secret.get("base_url"):
        return
    raise HTTPException(status_code=422, detail="AI gateway is disabled")


def _ai_gateway_client_for_tenant(session: Session, tenant_id: str):
    settings = get_settings()
    if settings.ai_gateway_enabled:
        return get_ai_gateway_client(settings)
    config, secret = _installation_secret_payload(
        session,
        tenant_id=tenant_id,
        category="ai_gateway",
    )
    base_url = str(config.get("base_url") or secret.get("base_url") or "").strip()
    if not base_url:
        raise HTTPException(status_code=422, detail="AI gateway is disabled")
    return HttpAiGatewayClient(
        base_url=base_url,
        api_key=str(secret.get("api_key") or "").strip() or None,
        timeout_seconds=float(settings.ai_gateway_timeout_seconds),
        default_model=str(config.get("default_model") or secret.get("default_model") or "").strip() or None,
    )


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
    source_platform = body.source_platform.strip().lower()
    target_platform = body.target_platform.strip().lower()
    if target_platform == "postbridge":
        raise HTTPException(status_code=400, detail="postbridge channel cannot be a bridge target")
    source = _find_selfhost_channel(
        session,
        tenant_id=tenant.id,
        platform=source_platform,
        channel_ref=body.source_channel_id,
    )
    if source is None:
        raise HTTPException(status_code=404, detail="source channel not found")
    target = _find_selfhost_channel(
        session,
        tenant_id=tenant.id,
        platform=target_platform,
        channel_ref=body.target_channel_id,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="target channel not found")
    if not _channel_can_read(source):
        raise HTTPException(status_code=400, detail="source channel cannot be read")
    if not _channel_can_write(target):
        raise HTTPException(status_code=400, detail="target channel cannot publish")

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


@router.get("/installation-secrets", include_in_schema=False)
def list_installation_secrets(session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Return install-wide integration secret metadata without plaintext values."""
    tenant = _require_selfhost_tenant(session)
    rows = session.scalars(
        select(InstallationSecretOrm).where(InstallationSecretOrm.tenant_id == tenant.id)
    ).all()
    by_category = {row.category: row for row in rows}
    return {
        "items": [
            _installation_secret_public_dict(by_category[category])
            if category in by_category
            else _empty_installation_secret_public_dict(category)
            for category in sorted(INSTALLATION_SECRET_CATEGORIES)
        ]
    }


@router.put("/installation-secrets/{category}", include_in_schema=False)
def upsert_installation_secret(
    category: str,
    body: InstallationSecretUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Store one install-wide integration secret encrypted at rest."""
    tenant = _require_selfhost_tenant(session)
    normalized = _require_installation_secret_category(category)
    row = _upsert_installation_secret_row(
        session,
        tenant_id=tenant.id,
        category=normalized,
        secret=body.secret,
        config=body.config,
        status=body.status,
    )
    session.commit()
    session.refresh(row)
    return _installation_secret_public_dict(row)


@router.delete("/installation-secrets/{category}", status_code=204, include_in_schema=False)
def delete_installation_secret(category: str, session: Session = Depends(get_db_session)) -> Response:
    """Remove one install-wide integration secret."""
    tenant = _require_selfhost_tenant(session)
    normalized = _require_installation_secret_category(category)
    row = session.scalar(
        select(InstallationSecretOrm).where(
            InstallationSecretOrm.tenant_id == tenant.id,
            InstallationSecretOrm.category == normalized,
        )
    )
    if row is not None:
        session.delete(row)
        session.commit()
    return Response(status_code=204)


@router.post("/telegram-import/start", include_in_schema=False)
def start_telegram_import_connection(
    body: TelegramImportStartRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Start a short Telethon login flow for self-host Telegram historical imports."""
    tenant = _require_selfhost_tenant(session)
    _cleanup_telegram_import_flow(session, tenant_id=tenant.id)
    try:
        api_id = int(body.api_id.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Telegram API ID must be numeric") from exc
    api_hash = body.api_hash.strip()
    phone = body.phone.strip()
    if not api_hash or not phone:
        raise HTTPException(status_code=422, detail="Telegram API hash and phone are required")
    try:
        session_seed, phone_code_hash = asyncio.run(
            _telegram_import_send_code(api_id=api_id, api_hash=api_hash, phone=phone)
        )
    except Exception as exc:
        logger.warning("telegram import code request failed", exc_info=exc)
        raise HTTPException(status_code=502, detail="Telegram login code request failed") from exc
    flow_id = secrets.token_urlsafe(24)
    _upsert_installation_secret_row(
        session,
        tenant_id=tenant.id,
        category=TELEGRAM_IMPORT_FLOW_CATEGORY,
        secret={
            "flow_id": flow_id,
            "api_id": str(api_id),
            "api_hash": api_hash,
            "phone": phone,
            "phone_code_hash": phone_code_hash,
            "session_string": session_seed,
            "expires_at": time.time() + TELEGRAM_IMPORT_FLOW_TTL_SECONDS,
        },
        config={},
        status="pending",
    )
    session.commit()
    return {
        "flow_id": flow_id,
        "status": "code_sent",
        "expires_in": TELEGRAM_IMPORT_FLOW_TTL_SECONDS,
    }


@router.post("/telegram-import/complete", include_in_schema=False)
def complete_telegram_import_connection(
    body: TelegramImportCompleteRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Complete Telethon login and store the resulting StringSession encrypted."""
    tenant = _require_selfhost_tenant(session)
    _cleanup_telegram_import_flow(session, tenant_id=tenant.id)
    flow_row, flow = _load_telegram_import_flow(session, tenant_id=tenant.id, flow_id=body.flow_id)
    code = (body.code or "").strip() or None
    password = (body.password or "").strip() or None
    if not code and not password:
        raise HTTPException(status_code=422, detail="Telegram code or password is required")
    try:
        session_string, account, password_required = asyncio.run(
            _telegram_import_sign_in(
                session_string=str(flow["session_string"]),
                api_id=int(flow["api_id"]),
                api_hash=str(flow["api_hash"]),
                phone=str(flow["phone"]),
                phone_code_hash=str(flow["phone_code_hash"]),
                code=code,
                password=password,
            )
        )
    except Exception as exc:
        logger.warning("telegram import login failed", exc_info=exc)
        raise HTTPException(status_code=502, detail="Telegram login failed") from exc
    if password_required:
        flow.update(
            {
                "session_string": session_string,
                "expires_at": time.time() + TELEGRAM_IMPORT_FLOW_TTL_SECONDS,
            }
        )
        flow_row.encrypted_secret = encrypt_credential_secret(_json_dumps_or_none(flow))
        flow_row.updated_at = datetime.now(UTC)
        session.commit()
        return {
            "flow_id": body.flow_id,
            "status": "password_required",
            "expires_in": TELEGRAM_IMPORT_FLOW_TTL_SECONDS,
        }
    row = _upsert_installation_secret_row(
        session,
        tenant_id=tenant.id,
        category="telegram_import",
        secret={
            "api_id": str(flow["api_id"]),
            "api_hash": str(flow["api_hash"]),
            "session_string": session_string,
        },
        config={"account": account},
        status="configured",
    )
    session.delete(flow_row)
    session.commit()
    session.refresh(row)
    return {
        "status": "configured",
        "account": account,
        "secret": _installation_secret_public_dict(row),
    }


@router.post("/gitsell-device/start", include_in_schema=False)
def start_gitsell_device_flow(body: GitsellDeviceStartRequest) -> dict[str, Any]:
    """Start GitSell OAuth device flow for self-host AI Gateway onboarding."""
    gateway = _gitsell_gateway_config(body.locale)
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                f"{gateway['origin']}/api/oauth/device_authorization",
                json={
                    "client_id": "1c-agent",
                    "scope": "agent:read agent:write profile:read",
                    "instance_id": body.instance_id,
                    "instance_label": body.instance_label or "Postbridge Self-host",
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"GitSell device flow request failed: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=_gitsell_error_message(response))
    data = response.json()
    return {
        "status": "pending",
        "device_code": data.get("device_code"),
        "user_code": data.get("user_code"),
        "verification_uri": data.get("verification_uri"),
        "verification_uri_complete": data.get("verification_uri_complete"),
        "expires_in": data.get("expires_in"),
        "interval": data.get("interval") or 3,
        "ai_gateway": {
            "base_url": gateway["base_url"],
            "default_model": gateway["default_model"],
            "image_model": gateway["image_model"],
            "image_size": gateway["image_size"],
        },
    }


@router.post("/gitsell-device/poll", include_in_schema=False)
def poll_gitsell_device_flow(body: GitsellDevicePollRequest) -> dict[str, Any]:
    """Poll GitSell OAuth device flow and return the AI Proxy token when approved."""
    gateway = _gitsell_gateway_config(body.locale)
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                f"{gateway['origin']}/api/oauth/token",
                data={
                    "client_id": "1c-agent",
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": body.device_code,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"GitSell token request failed: {exc}") from exc
    data = response.json()
    if response.status_code == 400 and isinstance(data, dict):
        error = data.get("error")
        if error in {"authorization_pending", "slow_down"}:
            return {"status": "pending", "error": error, "interval": 5 if error == "slow_down" else 3}
        if error in {"expired_token", "access_denied"}:
            return {"status": error}
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=_gitsell_error_message(response))
    ai_proxy_token = data.get("ai_proxy_token") if isinstance(data, dict) else None
    if not ai_proxy_token:
        raise HTTPException(status_code=502, detail="GitSell did not return an AI Proxy token")
    return {
        "status": "approved",
        "ai_gateway": {
            "base_url": gateway["base_url"],
            "default_model": gateway["default_model"],
            "image_model": gateway["image_model"],
            "image_size": gateway["image_size"],
            "api_key": ai_proxy_token,
            "token_name": data.get("ai_proxy_token_name"),
            "token_id": data.get("ai_proxy_token_id"),
        },
    }


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
        "version": {
            "current": normalize_version_tag(settings.postbridge_version),
            "release_repository": settings.postbridge_release_repository,
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


@router.get("/version-check", include_in_schema=False)
def get_app_version_check() -> dict[str, Any]:
    """Return the current Core version and the latest public release tag."""
    settings = get_settings()
    current = normalize_version_tag(settings.postbridge_version)
    latest_api_url = _release_latest_api_url(settings.postbridge_release_repository)
    response: dict[str, Any] = {
        "current_version": current,
        "latest_version": None,
        "update_available": False,
        "release_url": None,
        "check_status": "unknown",
        "update_command": None,
    }
    if latest_api_url is None:
        response["check_status"] = "release_source_unavailable"
        return response
    try:
        github_response = httpx.get(
            latest_api_url,
            timeout=5.0,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "postbridge-core-selfhost",
            },
        )
    except httpx.HTTPError:
        response["check_status"] = "unreachable"
        return response
    if github_response.status_code == 404:
        response["check_status"] = "not_found"
        return response
    if github_response.status_code >= 400:
        response["check_status"] = "error"
        return response
    try:
        data = github_response.json()
    except ValueError:
        response["check_status"] = "error"
        return response
    latest_tag = data.get("tag_name") if isinstance(data, dict) else None
    if not latest_tag:
        response["check_status"] = "error"
        return response
    latest = normalize_version_tag(latest_tag)
    response["latest_version"] = latest
    response["release_url"] = data.get("html_url")
    response["update_available"] = is_newer_version(latest, current)
    response["check_status"] = "ok"
    if response["update_available"]:
        response["update_command"] = build_release_update_command(
            image=settings.postbridge_container_image,
            version=latest,
        )
    return response


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
def create_platform_previews(
    body: PlatformPreviewRequest | None = None,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Build publish previews from active self-host bridges."""
    tenant = _require_selfhost_tenant(session)
    body = body or PlatformPreviewRequest()
    return {"items": _selfhost_platform_preview_items(session, tenant_id=tenant.id, body=body)}


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
def get_app_session(
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
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
            "setup_required": True,
            "authenticated": False,
            "user": None,
            "tenant": None,
        }
    admin_row = _local_admin_secret_row(session, tenant.id)
    if admin_row is None:
        return {
            "app_mode": "selfhost",
            "bootstrapped": True,
            "setup_required": True,
            "authenticated": False,
            "user": None,
            "tenant": _tenant_public_dict(tenant),
        }
    scheme, _, token = (authorization or "").partition(" ")
    token_payload = _verify_local_session_token(token.strip()) if scheme.lower() == "bearer" else None
    if token_payload is None:
        return {
            "app_mode": "selfhost",
            "bootstrapped": True,
            "setup_required": False,
            "authenticated": False,
            "user": None,
            "tenant": _tenant_public_dict(tenant),
        }
    admin = _decode_local_admin_secret(admin_row)
    return {
        "app_mode": "selfhost",
        "bootstrapped": True,
        "setup_required": False,
        "authenticated": True,
        "user": _local_admin_public_dict(str(admin.get("username") or token_payload.get("username") or "admin")),
        "tenant": _tenant_public_dict(tenant),
    }


@public_router.post("/bootstrap", include_in_schema=False)
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
    _require_bootstrap_crypto()
    tenant = _selfhost_tenant(session)
    admin_row = _local_admin_secret_row(session, tenant.id) if tenant is not None else None
    if tenant is not None and admin_row is not None:
        admin = _decode_local_admin_secret(admin_row)
        username = str(admin.get("username") or "admin")
        password_hash = str(admin.get("password_hash") or "")
        login_password = body.current_admin_password or body.admin_password
        authenticated = bool(login_password and _verify_password(login_password, password_hash))
        return {
            "app_mode": "selfhost",
            "bootstrapped": True,
            "setup_required": False,
            "authenticated": authenticated,
            "token": _make_local_session_token(username) if authenticated else None,
            "user": _local_admin_public_dict(username) if authenticated else None,
            "tenant": _tenant_public_dict(tenant),
        }
    if tenant is None:
        tenant = TenantOrm(
            id=settings.postbridge_selfhost_tenant_id,
            name=body.tenant_name or "Postbridge Self-host",
        )
        session.add(tenant)
        session.flush()
    admin_password = body.admin_password
    if not admin_password:
        if settings.app_env == "test":
            admin_password = "postbridge-test-admin"
        else:
            raise HTTPException(status_code=422, detail="new admin password is required")
    username = body.admin_username.strip()
    _upsert_installation_secret_row(
        session,
        tenant_id=tenant.id,
        category=LOCAL_ADMIN_SECRET_CATEGORY,
        secret={
            "username": username,
            "password_hash": _hash_password(admin_password),
        },
        config={},
        status="configured",
    )
    should_sync_ai_gateway_provider = False
    for category, payload in (body.installation_secrets or {}).items():
        normalized = _require_installation_secret_category(category)
        if not payload.secret and not payload.config:
            continue
        _upsert_installation_secret_row(
            session,
            tenant_id=tenant.id,
            category=normalized,
            secret=payload.secret,
            config=payload.config,
            status=payload.status,
        )
        if normalized == "ai_gateway":
            should_sync_ai_gateway_provider = True
    session.commit()
    session.refresh(tenant)
    if should_sync_ai_gateway_provider:
        _sync_ai_gateway_provider_from_installation_secret(session, tenant_id=tenant.id)
        session.commit()
    _ensure_selfhost_welcome_content(
        session,
        tenant_id=tenant.id,
        locale=body.locale or settings.postbridge_default_locale,
    )
    session.commit()
    session.refresh(tenant)
    token = _make_local_session_token(username)
    return {
        "app_mode": "selfhost",
        "bootstrapped": True,
        "setup_required": False,
        "authenticated": True,
        "token": token,
        "user": _local_admin_public_dict(username),
        "tenant": _tenant_public_dict(tenant),
    }


@router.post("/auth/login", include_in_schema=False)
def local_admin_login(
    body: LocalLoginRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Authenticate the local self-host admin."""
    tenant = _selfhost_tenant(session)
    if tenant is None:
        raise HTTPException(status_code=409, detail="self-host tenant is not bootstrapped")
    admin = _decode_local_admin_secret(_local_admin_secret_row(session, tenant.id))
    username = str(admin.get("username") or "")
    password_hash = str(admin.get("password_hash") or "")
    if not username or not password_hash:
        raise HTTPException(status_code=403, detail="self-host admin is not configured")
    if body.username.strip() != username or not _verify_password(body.password, password_hash):
        raise HTTPException(status_code=401, detail="invalid username or password")
    token = _make_local_session_token(username)
    return {
        "ok": True,
        "token": token,
        "user": _local_admin_public_dict(username),
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
    tenant = _require_selfhost_tenant(session)
    platform = body.platform.strip().lower()
    role = body.role.strip().lower()
    try:
        normalized = (
            _normalize_rss_target_feed_id(body.platform_channel_id)
            if platform == "rss" and role in {"target", "destination", "publish"}
            else _normalize_registry_channel_id(platform, body.platform_channel_id)
        )
    except HTTPException as exc:
        return {
            "ok": False,
            "display": "",
            "platform_channel_id": body.platform_channel_id.strip(),
            "role": body.role,
            "errors": [str(exc.detail)],
        }
    errors: list[str] = []
    if platform == "telegram" and role in {"target", "destination", "publish"}:
        errors = _validate_telegram_bot_target_access(
            session,
            tenant_id=tenant.id,
            channel_id=normalized,
        )
    if platform == "rss" and role in {"source", "read"}:
        errors = _validate_rss_source_url(normalized)
    if errors:
        return {
            "ok": False,
            "display": normalized,
            "platform_channel_id": normalized,
            "role": body.role,
            "errors": errors,
        }
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
    platform = body.platform.strip().lower()
    can_write = bool(body.can_write)
    kind = (
        body.kind.strip().lower()
        if body.kind
        else ("both" if body.can_read and body.can_write else "source" if body.can_read else "destination")
    )
    can_read = bool(body.can_read) or kind in {"source", "both"}
    can_write = bool(body.can_write) or kind in {"destination", "target", "both"}
    if platform == "postbridge" and (can_write or kind in {"destination", "target", "both"}):
        raise HTTPException(status_code=400, detail="postbridge channel cannot be a target")
    credentials_channel = session.get(ChannelOrm, body.credentials_ref) if body.credentials_ref else None
    if body.credentials_ref and (credentials_channel is None or credentials_channel.tenant_id != tenant.id):
        raise HTTPException(status_code=404, detail="credentials_ref channel not found")
    external_id = body.external_id or body.platform_channel_id or (
        credentials_channel.external_id if credentials_channel is not None else None
    )
    if platform == "rss":
        requested_read = can_read or kind in {"source", "both"}
        requested_write = can_write or kind in {"destination", "target", "both"}
        if requested_read and requested_write:
            raise HTTPException(status_code=400, detail="RSS channel cannot be both source and target")
        if requested_write:
            external_id = _normalize_rss_target_feed_id(external_id)
            kind = "destination"
            can_read = False
            can_write = True
        elif requested_read:
            external_id = _normalize_registry_channel_id("rss", external_id or "")
            errors = _validate_rss_source_url(external_id)
            if errors:
                raise HTTPException(status_code=422, detail=errors[0])
            kind = "source"
            can_read = True
            can_write = False
        else:
            raise HTTPException(status_code=400, detail="RSS channel must be source or target")
    if platform == "telegram" and (can_read or can_write or kind in {"source", "destination", "target", "both"}):
        external_id = _normalize_registry_channel_id("telegram", external_id or "")
    if platform == "telegram" and (can_write or kind in {"destination", "target", "both"}):
        errors = _validate_telegram_bot_target_access(
            session,
            tenant_id=tenant.id,
            channel_id=external_id,
        )
        if errors:
            raise HTTPException(status_code=422, detail=errors[0])
    if platform in {"vk", "linkedin"} and (can_read or can_write):
        if credentials_channel is None:
            platform_label = "LinkedIn" if platform == "linkedin" else "VK"
            raise HTTPException(status_code=422, detail=f"Connect {platform_label} credentials first.")
        if credentials_channel.platform != platform:
            raise HTTPException(status_code=422, detail=f"Choose connected {platform} credentials for this channel.")
    if credentials_channel is not None and credentials_channel.tenant_id == tenant.id:
        credentials_channel.platform = platform
        credentials_channel.kind = kind
        credentials_channel.title = body.title
        credentials_channel.external_id = external_id
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
                **({"can_read": can_read} if body.can_read is not None or platform == "rss" else {}),
                **({"can_write": can_write} if body.can_write is not None or platform == "rss" else {}),
            }
        )
        credentials_channel.updated_at = datetime.now(UTC)
        _attach_telegram_bot_credential_if_available(
            session,
            tenant_id=tenant.id,
            channel=credentials_channel,
        )
        session.commit()
        session.refresh(credentials_channel)
        return _channel_public_dict(credentials_channel)
    row = ChannelOrm(
        id=str(uuid4()),
        tenant_id=tenant.id,
        platform=platform,
        kind=kind,
        title=body.title,
        external_id=external_id,
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
                **({"can_read": can_read} if body.can_read is not None or platform == "rss" else {}),
                **({"can_write": can_write} if body.can_write is not None or platform == "rss" else {}),
            }
        ),
    )
    session.add(row)
    session.flush()
    _attach_telegram_bot_credential_if_available(
        session,
        tenant_id=tenant.id,
        channel=row,
    )
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
    tenant, row = _require_selfhost_channel(session, channel_id)
    session.execute(
        delete(BridgeOrm).where(
            BridgeOrm.tenant_id == tenant.id,
            (BridgeOrm.source_channel_id == row.id) | (BridgeOrm.target_channel_id == row.id),
        )
    )
    for batch_run in session.scalars(
        select(BatchImportRunOrm).where(
            BatchImportRunOrm.tenant_id == tenant.id,
            (BatchImportRunOrm.source_core_channel_id == row.id)
            | (BatchImportRunOrm.target_core_channel_id == row.id),
        )
    ):
        if batch_run.source_core_channel_id == row.id:
            batch_run.source_core_channel_id = None
            batch_run.source_channel = f"deleted:{row.id}"
        if batch_run.target_core_channel_id == row.id:
            batch_run.target_core_channel_id = None
            batch_run.target_channel = f"deleted:{row.id}"
    session.execute(
        delete(PublicationTargetOrm).where(
            PublicationTargetOrm.tenant_id == tenant.id,
            PublicationTargetOrm.channel_id == row.id,
        )
    )
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
    if body.status == "published":
        live_sync_jobs = _selfhost_immediate_live_sync_jobs(
            session,
            row=row,
            source_channel_id=body.live_sync_source_core_channel_id,
        )
        session.commit()
    else:
        live_sync_jobs = []
    live_sync_warning = _enqueue_selfhost_live_sync_jobs_or_revert(session, row=row, jobs=live_sync_jobs)
    payload = _content_item_public_dict(row)
    if live_sync_warning:
        payload["live_sync_warning"] = live_sync_warning
    return payload


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
    snapshot = _content_item_snapshot(row)
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
    if body.status == "published":
        live_sync_jobs = _selfhost_immediate_live_sync_jobs(
            session,
            row=row,
            source_channel_id=(
                source_channel_id
                if source_channel_id is not POSTBRIDGE_SCHEDULE_UNSET
                else None
            ),
        )
        session.commit()
    else:
        live_sync_jobs = []
    live_sync_warning = _enqueue_selfhost_live_sync_jobs_or_revert(session, row=row, jobs=live_sync_jobs, snapshot=snapshot)
    payload = _content_item_public_dict(row)
    if live_sync_warning:
        payload["live_sync_warning"] = live_sync_warning
    return payload


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
    _require_ai_enabled(session, tenant.id)
    if body.content_item_id:
        row = get_postbridge_content_item(
            session,
            tenant_id=tenant.id,
            content_id=body.content_item_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="content item not found")
    correlation_id = getattr(request.state, "correlation_id", None) or "selfhost-app"
    client = _ai_gateway_client_for_tenant(session, tenant.id)
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
    _require_ai_enabled(session, tenant.id)
    row = get_postbridge_content_item(session, tenant_id=tenant.id, content_id=content_id)
    if row is None:
        raise HTTPException(status_code=404, detail="content item not found")
    client = _ai_gateway_client_for_tenant(session, tenant.id)
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
    _require_ai_enabled(session, tenant.id)
    row = get_postbridge_content_item(session, tenant_id=tenant.id, content_id=content_id)
    if row is None:
        raise HTTPException(status_code=404, detail="content item not found")
    client = _ai_gateway_client_for_tenant(session, tenant.id)
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
    _sync_ai_gateway_provider_from_installation_secret(session, tenant_id=tenant.id)
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
    _sync_ai_gateway_provider_from_installation_secret(session, tenant_id=tenant.id)
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
    _sync_ai_gateway_provider_from_installation_secret(session, tenant_id=tenant.id)
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
    _sync_ai_gateway_provider_from_installation_secret(session, tenant_id=tenant.id)
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
    tenant = _require_selfhost_tenant(session)
    _require_ai_enabled(session, tenant.id)
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
