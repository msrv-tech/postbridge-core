"""Декодирование Zen channel_credentials (fetch)."""

from __future__ import annotations

import json
from typing import Any

from postbridge.api.schemas import ZenCredentials
from postbridge.infrastructure.crypto.credentials import decode_channel_credential_raw


def decode_fetch_credentials(row: Any) -> ZenCredentials | None:
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
    rss_url = data.get("rss_url")
    token = data.get("token")
    return ZenCredentials(
        rss_url=rss_url if isinstance(rss_url, str) else None,
        token=token if isinstance(token, str) else None,
    )
