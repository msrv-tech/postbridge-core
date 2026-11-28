import re

from postbridge.integrations.analytics import AnalyticsUnavailable, counts, observed, payload, require
from postbridge.integrations.vk.publisher import VK_API_BASE, VK_API_VERSION


def read_many(client, credentials, ids, channel):
    require(credentials, "permission_required")
    owner = channel.removeprefix("vk/")
    require(re.fullmatch(r"-?[0-9]+", owner), "post_not_accessible")
    refs = {f"{owner}_{value}" if value.isascii() and value.isdigit() else value: value for value in ids}
    refs = {key: value for key, value in refs.items() if re.fullmatch(re.escape(owner) + r"_[0-9]+", key)}
    if not refs:
        return {}
    data = payload(client.post(f"{VK_API_BASE}/wall.getById", data={
        "posts": ",".join(refs), "v": VK_API_VERSION,
        "access_token": credentials.user_access_token or credentials.access_token,
    }))
    if "error" in data:
        code = data["error"].get("error_code")
        if code in (5, 7, 15, 27):
            raise AnalyticsUnavailable("permission_required")
        if code in (6, 29):
            raise AnalyticsUnavailable("rate_limited")
        raise AnalyticsUnavailable("provider_rejected")
    posts = data.get("response", [])
    if isinstance(posts, dict):
        posts = posts.get("items", [])
    out = {}
    for post in posts:
        ref = f"{post.get('owner_id')}_{post.get('id')}"
        if ref in refs:
            out[refs[ref]] = observed(counts(
                {key: (post.get(key) or {}).get("count") for key in ("views", "likes", "comments", "reposts")},
                {key: key for key in ("views", "likes", "comments", "reposts")},
            ))
    return out
