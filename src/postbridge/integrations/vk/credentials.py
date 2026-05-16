"""Декодирование VK channel_credentials (fetch + publish)."""

from __future__ import annotations

import json
from typing import Any

from postbridge.api.schemas import VKCredentials
from postbridge.infrastructure.crypto.credentials import decode_channel_credential_raw


def decode_channel_credentials(row: Any) -> VKCredentials | None:
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
    access = data.get("access_token")
    if isinstance(access, str) and access:
        uat = data.get("user_access_token")
        return VKCredentials(
            access_token=access,
            user_access_token=uat if isinstance(uat, str) else None,
        )
    return None
