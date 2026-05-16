"""Декодирование MAX channel_credentials (fetch + publish)."""

from __future__ import annotations

import json
from typing import Any

from postbridge.api.schemas import MaxCredentials
from postbridge.infrastructure.crypto.credentials import decode_channel_credential_raw


def decode_channel_credentials(row: Any) -> MaxCredentials | None:
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
    base_url = data.get("base_url")
    token = data.get("token")
    if isinstance(base_url, str) and isinstance(token, str) and base_url and token:
        return MaxCredentials(base_url=base_url, token=token)
    return None
