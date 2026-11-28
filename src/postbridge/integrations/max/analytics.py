from urllib.parse import urlsplit

from postbridge.config import get_settings
from postbridge.integrations.analytics import counts, observed, payload, require


def _base_url(credentials):
    require(credentials, "permission_required")
    base = credentials.base_url.rstrip("/")
    allowed = {"https://platform-api.max.ru", "https://platform-api2.max.ru"}
    configured = (get_settings().max_api_base_url or "").rstrip("/")
    if configured and urlsplit(configured).scheme == "https":
        allowed.add(configured)
    require(base in allowed, "endpoint_not_enabled")
    return base


def read_many(client, credentials, ids, channel):
    base = _base_url(credentials)
    raw = payload(client.get(f"{base}/messages", headers={"Authorization": credentials.token},
                             params={"message_ids": ",".join(ids)}))
    return {post["body"]["mid"]: observed(counts(post.get("stat"), {"views": "views", "shares": "shares"}))
            for post in raw.get("messages", []) if post.get("body", {}).get("mid")
            and (not channel.lstrip("-").isdigit() or str(post.get("recipient", {}).get("chat_id")) == channel)}


def read_audience(client, credentials, channel):
    base = _base_url(credentials)
    require(channel.lstrip("-").isdigit())
    chat = payload(client.get(f"{base}/chats/{channel}", headers={"Authorization": credentials.token}))
    require(str(chat.get("chat_id")) == channel, "post_not_accessible")
    return observed(counts(chat, {"members": "participants_count"}))
