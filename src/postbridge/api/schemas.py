from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TelegramCredentials(BaseModel):
    """Креды для Telegram: Telethon (исторический перенос) и/или Bot API (публикация)."""

    api_id: str = ""
    api_hash: str = ""
    session_string: str | None = None
    bot_token: str | None = None  # для публикации через Bot API (Telethon только для платных переносов)


class MaxCredentials(BaseModel):
    """Креды для MAX API (base_url, token)."""

    base_url: str
    token: str


class VKCredentials(BaseModel):
    """Креды для VK API.

    access_token — токен сообщества (wall.post и т.д.).
    user_access_token — опционально: wall.get для стены группы (иначе error 27 с group token),
    photos.getWallUploadServer/saveWallPhoto (групповой токен не поддерживает).
    """

    access_token: str
    user_access_token: str | None = None


class LinkedInCredentials(BaseModel):
    """Credentials for LinkedIn Posts API."""

    access_token: str
    author_urn: str | None = None
    api_version: str | None = None
    expires_at: int | None = None


class FacebookCredentials(BaseModel):
    """Credentials for Facebook Pages publishing."""

    page_access_token: str
    page_id: str | None = None
    graph_api_version: str | None = None
    expires_at: int | None = None


class InstagramCredentials(BaseModel):
    """Credentials for Instagram Business content publishing."""

    access_token: str
    instagram_user_id: str | None = None
    graph_api_version: str | None = None
    expires_at: int | None = None


class XCredentials(BaseModel):
    """Credentials for X API v2 publishing."""

    access_token: str
    expires_at: int | None = None


class BlueskyCredentials(BaseModel):
    """Credentials for Bluesky/AT Protocol publishing."""

    identifier: str
    app_password: str
    service_url: str | None = None


class MastodonCredentials(BaseModel):
    """Credentials for Mastodon publishing."""

    access_token: str
    instance_url: str | None = None
    visibility: str | None = None


class ZenCredentials(BaseModel):
    """Legacy RSS credentials."""

    rss_url: str | None = None
    token: str | None = None


class RssCredentials(BaseModel):
    """Креды для generic RSS (URL ленты)."""

    rss_url: str | None = None


class CreateBatchImportRunRequest(BaseModel):
    """Тело запроса на создание batch import run (internal service API)."""

    source_channel: str = Field(min_length=1, max_length=256)
    target_channel: str = Field(min_length=1, max_length=256)
    requested_limit: int = Field(default=20, ge=0, le=1000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    source_platform: str | None = Field(default=None, max_length=32)
    target_platform: str | None = Field(default=None, max_length=32)
    source_core_channel_id: str = Field(min_length=36, max_length=36)
    target_core_channel_id: str = Field(min_length=36, max_length=36)


class ErrorEnvelope(BaseModel):
    """Формат ошибки в API-ответе."""

    code: str
    message: str
    details: dict[str, Any]
    source: str
    retryable: bool
    correlation_id: str


class JobMetrics(BaseModel):
    """Метрики job: длительность, retry_count."""

    duration_ms: int | None
    retry_count: int = 0


class BatchImportRunResponse(BaseModel):
    """Ответ internal API для batch import run."""

    id: str
    idempotency_key: str | None = None
    source_channel: str
    target_channel: str
    source_core_channel_id: str | None = None
    target_core_channel_id: str | None = None
    status: str
    requested_limit: int
    processed_posts: int
    fetched_posts_count: int | None = None
    correlation_id: str
    error: ErrorEnvelope | None
    metrics: JobMetrics
    created_at: datetime
    updated_at: datetime
