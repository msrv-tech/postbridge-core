from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from croniter import croniter
except ImportError:  # pragma: no cover - local fallback before dependency install
    croniter = None


def next_run_at_from_cron(schedule_cron: str | None, *, base: datetime | None = None, timezone: str | None = None) -> datetime | None:
    if not schedule_cron:
        return None
    current = base or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    if croniter is None:
        return current + timedelta(days=1)
    try:
        zone = ZoneInfo(timezone or "UTC")
    except ZoneInfoNotFoundError:
        # Historic task rows accepted arbitrary timezone strings; retain their UTC fallback.
        zone = ZoneInfo("UTC")
    return croniter(schedule_cron, current.astimezone(zone)).get_next(datetime).astimezone(UTC)
