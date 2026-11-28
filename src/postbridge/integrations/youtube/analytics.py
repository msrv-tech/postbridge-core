from postbridge.integrations.analytics import bearer, counts, observed, payload, require
from postbridge.integrations.youtube.publisher import API, YouTubePublisher


def read_many(client, credentials, ids, channel):
    require(credentials and credentials.channel_id == channel, "permission_required")
    token = YouTubePublisher()._token(client, credentials)
    data = payload(client.get(f"{API}/videos", headers=bearer(token), params={
        "id": ",".join(ids), "part": "snippet,statistics",
        "fields": "items(id,snippet/channelId,statistics(viewCount,likeCount,commentCount))",
    }))
    return {item["id"]: observed(counts(item.get("statistics"), {
        "views": "viewCount", "likes": "likeCount", "comments": "commentCount",
    })) for item in data.get("items", []) if item.get("snippet", {}).get("channelId") == channel}


def read_audience(client, credentials, channel):
    require(credentials and credentials.channel_id == channel, "permission_required")
    token = YouTubePublisher()._token(client, credentials)
    value = payload(client.get(f"{API}/channels", headers=bearer(token), params={
        "id": channel, "part": "statistics", "fields": "items(id,statistics(subscriberCount,hiddenSubscriberCount))",
    }))
    stats = next((item.get("statistics", {}) for item in value.get("items", []) if item.get("id") == channel), {})
    return observed(counts(stats, {"subscribers": "subscriberCount"}) if not stats.get("hiddenSubscriberCount") else {})
