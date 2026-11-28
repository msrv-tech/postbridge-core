import re
from urllib.parse import urlsplit

from postbridge.config import get_settings
from postbridge.integrations.analytics import AnalyticsUnavailable, bearer, counts, observed, require
from postbridge.integrations.instagram.publisher import INSTAGRAM_GRAPH_BASE
from postbridge.integrations.meta_analytics import graph_payload


def read_many(client, credentials, ids, channel):
    require(credentials, "permission_required")
    version = credentials.graph_api_version or get_settings().meta_graph_api_version
    require(re.fullmatch(r"v[0-9]+[.][0-9]+", version or ""), "endpoint_not_enabled")
    headers = bearer(credentials.access_token)
    results = {}
    for post_id in ids:
        require(post_id.isascii() and post_id.isdigit())
        base = f"{INSTAGRAM_GRAPH_BASE}/{version}/{post_id}"
        public = graph_payload(client.get(base, headers=headers, params={
            "fields": "id,like_count,comments_count,permalink",
        }))
        metrics = counts(public, {"likes": "like_count", "comments": "comments_count"})
        partial = None
        try:
            result = graph_payload(client.get(base + "/insights", headers=headers, params={"metric": "reach,views,saved,shares"}))
            fields = {"reach": "reach", "views": "views", "saved": "saves", "shares": "shares"}
            for item in result.get("data", []):
                name = item.get("name")
                if name in fields:
                    values = item.get("values") or []
                    value = item.get("total_value") or (values[-1] if values else {})
                    metrics.update(counts(value, {fields[name]: "value"}))
        except AnalyticsUnavailable as error:
            if error.reason not in {"permission_required", "provider_rejected", "post_not_accessible"}:
                raise
            partial = error.reason
        url = public.get("permalink")
        parsed = urlsplit(url or "")
        if parsed.scheme != "https" or parsed.netloc not in {"www.instagram.com", "instagram.com"}:
            url = None
        results[post_id] = observed(metrics, partial=partial, url=url)
    return results
