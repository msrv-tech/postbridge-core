from postbridge.integrations.analytics import bearer, counts, observed, payload, require
from postbridge.integrations.x.publisher import X_API_BASE


def read_many(client, credentials, ids, channel):
    require(credentials, "permission_required")
    ids = [value for value in ids if value.isascii() and value.isdigit()]
    if not ids:
        return {}
    data = payload(client.get(f"{X_API_BASE}/2/tweets", headers=bearer(credentials.access_token), params={
        "ids": ",".join(ids), "tweet.fields": "public_metrics",
    }))
    return {post["id"]: observed(counts(post.get("public_metrics"), {"impressions": "impression_count",
        "likes": "like_count", "comments": "reply_count", "reposts": "retweet_count", "quotes": "quote_count",
        "bookmarks": "bookmark_count"})) for post in data.get("data", []) if post.get("id") in ids}
