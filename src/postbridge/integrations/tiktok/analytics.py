import re
from urllib.parse import urlsplit

from postbridge.integrations.analytics import AnalyticsUnavailable, bearer, counts, observed, payload, require
from postbridge.integrations.tiktok.api import API


def _body(response):
    data = payload(response)
    code = (data.get("error") or {}).get("code")
    if code in {"scope_not_authorized", "access_token_invalid", "scope_permission_missed"}:
        raise AnalyticsUnavailable("permission_required")
    if code and code != "ok":
        raise AnalyticsUnavailable("provider_rejected")
    return data.get("data", {})


def read_many(client, credentials, ids, channel):
    require(credentials, "permission_required")
    headers = bearer(credentials.access_token)
    remote_ids = {}
    for value in ids:
        if re.fullmatch(r"[0-9]{1,30}", value):
            remote_ids[value] = value
        elif re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value):
            state = _body(client.post(f"{API}/post/publish/status/fetch/", headers=headers, json={"publish_id": value}))
            published = state.get("publicaly_available_post_id") or []
            if len(published) == 1 and re.fullmatch(r"[0-9]{1,30}", str(published[0])):
                remote_ids[value] = str(published[0])
    if not remote_ids:
        return {}
    data = _body(client.post(f"{API}/video/query/", headers=headers,
        params={"fields": "id,share_url,view_count,like_count,comment_count,share_count"},
        json={"filters": {"video_ids": list(dict.fromkeys(remote_ids.values()))}}))
    videos = {str(post["id"]): post for post in data.get("videos", [])}
    results = {}
    for posted_id, video_id in remote_ids.items():
        post = videos.get(video_id)
        if post:
            url = post.get("share_url")
            parsed = urlsplit(url or "")
            if parsed.scheme != "https" or parsed.netloc not in {"tiktok.com", "www.tiktok.com"}:
                url = None
            results[posted_id] = observed(counts(post, {"views": "view_count", "likes": "like_count",
                                                        "comments": "comment_count", "shares": "share_count"}), url=url)
    return results
