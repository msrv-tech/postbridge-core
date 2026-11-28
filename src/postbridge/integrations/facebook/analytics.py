import re

from postbridge.config import get_settings
from postbridge.integrations.analytics import AnalyticsUnavailable, bearer, counts, observed, require
from postbridge.integrations.facebook.publisher import FACEBOOK_GRAPH_BASE
from postbridge.integrations.meta_analytics import graph_payload


def read_many(client, credentials, ids, channel):
    require(credentials, "permission_required")
    version = credentials.graph_api_version or get_settings().meta_graph_api_version
    require(re.fullmatch(r"v[0-9]+[.][0-9]+", version or ""), "endpoint_not_enabled")
    headers = bearer(credentials.page_access_token)
    results = {}
    for post_id in ids:
        require(re.fullmatch(r"[0-9]+(?:_[0-9]+)?", post_id))
        base = f"{FACEBOOK_GRAPH_BASE}/{version}/{post_id}"
        try:
            public = graph_payload(client.get(base, headers=headers, params={
                "fields": "id,reactions.limit(0).summary(true),comments.limit(0).summary(true),shares",
            }))
        except AnalyticsUnavailable as error:
            if error.reason != "provider_rejected":
                raise
            # Older receipts can identify a public photo object, which lacks post reactions and shares.
            public = graph_payload(client.get(base, headers=headers, params={
                "fields": "id,likes.limit(0).summary(true),comments.limit(0).summary(true)",
            }))
        basic = {key: (public.get(key) or {}).get("summary", {}).get("total_count") for key in ("comments", "reactions")}
        basic["likes"] = (public.get("likes") or {}).get("summary", {}).get("total_count")
        basic["shares"] = (public.get("shares") or {}).get("count")
        metrics = counts(basic, {key: key for key in basic})
        partial = None
        try:
            result = graph_payload(client.get(base + "/insights", headers=headers, params={"metric": "post_media_view"}))
            value = next((metric.get("values", []) for metric in result.get("data", [])
                          if metric.get("name") == "post_media_view"), [])
            if value:
                metrics.update(counts(value[-1], {"views": "value"}))
        except AnalyticsUnavailable as error:
            if error.reason not in {"permission_required", "provider_rejected", "post_not_accessible"}:
                raise
            partial = error.reason
        results[post_id] = observed(metrics, partial=partial)
    return results
