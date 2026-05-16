"""Декодирование RSS channel_credentials (fetch)."""

from __future__ import annotations

import json
from typing import Any

from postbridge.api.schemas import RssCredentials
from postbridge.infrastructure.crypto.credentials import decode_channel_credential_raw


def decode_fetch_credentials(row: Any) -> RssCredentials | None:
    if row is None:
        return None
    raw = decode_channel_credential_raw(row)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    url = data.get("rss_url")
    if isinstance(url, str) and url.strip():
        return RssCredentials(rss_url=url.strip())
    return None
