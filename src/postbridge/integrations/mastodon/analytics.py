import os
from urllib.parse import urlsplit

from postbridge.integrations.analytics import counts, observed, payload, require


def _identity(credentials, channel):
    instance = channel.removeprefix("@").partition("@")[2]
    if credentials and credentials.instance_url:
        configured = urlsplit(credentials.instance_url)
        require(configured.scheme == "https" and configured.netloc == configured.hostname
                and configured.path in ("", "/") and not configured.query and not configured.fragment,
                "endpoint_not_enabled")
        require(not instance or instance.casefold() == configured.hostname, "post_not_accessible")
        instance = configured.hostname
    endpoints = os.getenv("POSTBRIDGE_MASTODON_ANALYTICS_HOSTS", "mastodon.social").lower().split(",")
    require(instance.lower() in {host.strip() for host in endpoints if host.strip()}, "endpoint_not_enabled")
    owner = channel.removeprefix("@").partition("@")[0]
    return instance, owner


def read_many(client, credentials, ids, channel):
    instance, owner = _identity(credentials, channel)
    results = {}
    for post_id in ids:
        require(post_id.isascii() and post_id.isdigit())
        post = payload(client.get(f"https://{instance}/api/v1/statuses/{post_id}"))
        if post.get("id") == post_id and post.get("account", {}).get("acct", "").casefold() == owner.casefold():
            results[post_id] = observed(counts(post, {"likes": "favourites_count", "reposts": "reblogs_count",
                                                       "comments": "replies_count", "quotes": "quotes_count"}))
    return results


def read_audience(client, credentials, channel):
    instance, owner = _identity(credentials, channel)
    account = payload(client.get(f"https://{instance}/api/v1/accounts/lookup", params={"acct": owner}))
    require(account.get("acct", "").casefold() == owner.casefold(), "post_not_accessible")
    return observed(counts(account, {"followers": "followers_count"}))
