"""Продуктовые возможности платформы (источник/приёмник, live-sync, AI, креды fetch).

live_sync_source_supported: источник может отдавать посты в мост в реальном времени
(сейчас: postbridge, telegram).

historical_migration_source_supported: платформа может быть источником batch_import
(SyncService / get_fetcher).

historical_migration_target_supported: платформа может быть назначением публикации после
импорта (publisher в Core; у postbridge нет publisher).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PlatformCapabilities:
    supports_source: bool
    supports_target: bool
    live_sync_publish_supported: bool
    live_sync_source_supported: bool
    historical_migration_source_supported: bool
    historical_migration_target_supported: bool
    ai_adapt_supported: bool
    fetch_credentials_required: bool
    rule_post_text_limit: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
