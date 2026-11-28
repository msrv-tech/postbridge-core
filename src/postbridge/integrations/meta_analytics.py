"""Graph Analytics calls can report token and permission errors over HTTP 400."""
from postbridge.integrations.analytics import AnalyticsUnavailable, payload


def graph_payload(response):
    if response.status_code == 400:
        data = response.json()
        error = data.get("error") or {}
        if error.get("code") in (10, 190, 200, 299):
            raise AnalyticsUnavailable("permission_required")
        if error.get("code") in (4, 17, 32, 613):
            raise AnalyticsUnavailable("rate_limited")
    return payload(response)
