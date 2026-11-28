from postbridge.integrations.analytics import counts, observed, payload, require


def read_many(client, credentials, ids, channel):
    wanted = [uri for uri in ids if uri.startswith("at://") and "/app.bsky.feed.post/" in uri]
    if not wanted:
        return {}
    posts = payload(client.get("https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts", params={"uris": wanted}))
    return {post["uri"]: observed(counts(post, {"likes": "likeCount", "comments": "replyCount",
                                               "reposts": "repostCount", "quotes": "quoteCount"}))
            for post in posts.get("posts", []) if post.get("uri") in wanted
            and channel.casefold() in {post.get("author", {}).get(key, "").casefold() for key in ("did", "handle")}}


def read_audience(client, credentials, channel):
    require(channel)
    profile = payload(client.get("https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile", params={"actor": channel}))
    require(channel.casefold() in {profile.get(key, "").casefold() for key in ("did", "handle")}, "post_not_accessible")
    return observed(counts(profile, {"followers": "followersCount"}))
