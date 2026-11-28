import re
from urllib.parse import quote

from postbridge.config import get_settings
from postbridge.integrations.analytics import bearer, counts, observed, payload, require
from postbridge.integrations.linkedin.publisher import LINKEDIN_API_BASE


def read_many(client, credentials, ids, channel):
    require(credentials, "permission_required")
    require((credentials.author_urn or channel).startswith("urn:li:person:"), "permission_required")
    version = credentials.api_version or get_settings().linkedin_api_version
    require(re.fullmatch(r"[0-9]{6}", version or ""), "endpoint_not_enabled")
    headers = {**bearer(credentials.access_token), "LinkedIn-Version": version, "X-Restli-Protocol-Version": "2.0.0"}
    results = {}
    for post_id in ids:
        match = re.fullmatch(r"urn:li:(share|ugcPost):[0-9]+", post_id)
        if not match:
            continue
        entity_type = "ugc" if match[1] == "ugcPost" else "share"
        endpoint = f"{LINKEDIN_API_BASE}/rest/memberCreatorPostAnalytics?q=entity&entity=({entity_type}:{quote(post_id, safe='')})"
        metrics = {}
        for key, native in {"impressions": "IMPRESSION", "reach": "MEMBERS_REACHED", "reactions": "REACTION",
                            "comments": "COMMENT", "reposts": "RESHARE"}.items():
            data = payload(client.get(f"{endpoint}&queryType={native}&aggregation=TOTAL", headers=headers))
            elements = data.get("elements", [])
            if len(elements) == 1:
                metrics.update(counts(elements[0], {key: "count"}))
        results[post_id] = observed(metrics)
    return results
