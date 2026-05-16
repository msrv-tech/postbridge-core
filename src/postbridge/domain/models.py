from dataclasses import dataclass
from enum import StrEnum
from datetime import datetime
from typing import Any


class BatchImportRunStatus(StrEnum):
    """Статусы batch import run: pending, running, paused, completed, failed."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(slots=True)
class PostPayload:
    """Полезная нагрузка поста: ID, текст, опционально media_url или media_urls (альбом)."""

    source_post_id: str
    text: str
    media_url: str | None = None
    media_urls: list[str] | None = None  # для альбомов (media_group)


@dataclass(slots=True)
class BatchImportRun:
    """Доменная модель batch import run: миграция постов из source_channel в target_channel."""

    id: str
    tenant_id: str
    source_channel: str
    target_channel: str
    status: BatchImportRunStatus
    created_at: datetime
    updated_at: datetime
    requested_limit: int
    processed_posts: int = 0
    idempotency_key: str | None = None
    correlation_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_details: dict[str, Any] | None = None
    error_source: str | None = None
    error_retryable: bool | None = None
    retry_count: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    source_platform: str | None = None
    target_platform: str | None = None
    source_core_channel_id: str | None = None
    target_core_channel_id: str | None = None
