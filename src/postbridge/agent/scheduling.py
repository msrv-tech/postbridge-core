from __future__ import annotations

from datetime import UTC, datetime, timedelta

try:
    from croniter import croniter
except ImportError:  # pragma: no cover - local fallback before dependency install
    croniter = None


def next_run_at_from_cron(schedule_cron: str | None, *, base: datetime | None = None) -> datetime | None:
    if not schedule_cron:
        return None
    current = base or datetime.now(UTC)
    if croniter is None:
        return current + timedelta(days=1)
    return croniter(schedule_cron, current).get_next(datetime)
