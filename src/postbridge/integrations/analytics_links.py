"""Construct stable provider permalinks for receipts that predate external_url."""

import re
from urllib.parse import quote


def publication_link(platform, channel, post_id):
    if platform == "youtube" and re.fullmatch(r"[A-Za-z0-9_-]{11}", post_id):
        return f"https://www.youtube.com/watch?v={post_id}"
    if platform == "x" and re.fullmatch(r"[0-9]+", post_id):
        return f"https://x.com/i/web/status/{post_id}"
    if platform == "vk":
        owner = channel.removeprefix("vk/")
        key = post_id if "_" in post_id else f"{owner}_{post_id}"
        if re.fullmatch(r"-?[0-9]+_[0-9]+", key):
            return f"https://vk.com/wall{key}"
    if platform == "linkedin" and re.fullmatch(r"urn:li:(ugcPost|share):[0-9]+", post_id):
        return f"https://www.linkedin.com/feed/update/{post_id}/"
    if platform == "bluesky":
        match = re.fullmatch(r"at://(did:[A-Za-z0-9:._%-]+)/app\.bsky\.feed\.post/([A-Za-z0-9._~-]+)", post_id)
        if match:
            return f"https://bsky.app/profile/{quote(match[1], safe=':')}/post/{match[2]}"
    if platform == "mastodon" and re.fullmatch(r"[0-9]+", post_id):
        match = re.fullmatch(r"@?([A-Za-z0-9_]+)@([a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+)", channel)
        if match:
            return f"https://{match[2]}/@{match[1]}/{post_id}"
    if platform == "telegram" and re.fullmatch(r"[0-9]+", post_id):
        identity = channel.removeprefix("tg/").removeprefix("@")
        if re.fullmatch(r"-100[0-9]+", identity):
            return f"https://t.me/c/{identity[4:]}/{post_id}"
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]+", identity):
            return f"https://t.me/{identity}/{post_id}"
    if platform == "facebook" and re.fullmatch(r"[0-9]+(?:_[0-9]+)?", post_id):
        return f"https://www.facebook.com/{post_id}"
    return None
