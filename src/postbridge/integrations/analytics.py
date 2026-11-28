"""Common result handling for read-only publication analytics adapters."""

from dataclasses import dataclass
from datetime import timedelta
import re

import httpx


@dataclass(frozen=True)
class Observation:
    metrics: dict[str, int]
    status: str = "available"
    detail: str | None = None
    url: str | None = None


class AnalyticsUnavailable(Exception):
    def __init__(self, reason: str, *, retry_after: timedelta | None = None):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


def require(condition, reason="not_available"):
    if not condition:
        raise AnalyticsUnavailable(reason)


def payload(response: httpx.Response):
    if response.status_code == 429:
        raw = response.headers.get("retry-after", "")
        delay = min(int(raw), 86400) if raw.isascii() and raw.isdigit() and len(raw) <= 9 else 3600
        raise AnalyticsUnavailable("rate_limited", retry_after=timedelta(seconds=max(delay, 60)))
    if response.status_code in (401, 403):
        raise AnalyticsUnavailable("permission_required")
    if response.status_code in (404, 410):
        raise AnalyticsUnavailable("post_not_accessible")
    if response.status_code >= 500:
        raise AnalyticsUnavailable("temporarily_unavailable")
    if not response.is_success:
        raise AnalyticsUnavailable("provider_rejected")
    value = response.json()
    if not isinstance(value, dict):
        raise AnalyticsUnavailable("invalid_response")
    return value


def counts(data, fields):
    metrics = {}
    if isinstance(data, dict):
        for key, field in fields.items():
            value = data.get(field)
            if isinstance(value, (int, str)) and not isinstance(value, bool) and re.fullmatch(r"[0-9]{1,20}", str(value)):
                count = int(value)
                if count < 2**63:
                    metrics[key] = count
    return metrics


def observed(metrics, *, partial=None, url=None):
    return (Observation(metrics, "partial" if partial else "available", partial, url)
            if metrics else Observation({}, "unavailable", partial or "no_metrics", url))


def bearer(token):
    require(token, "permission_required")
    return {"Authorization": f"Bearer {token}"}
