"""Small helpers for decoding JSON channel credentials."""

from __future__ import annotations

import json
from typing import Any

from postbridge.infrastructure.crypto.credentials import decode_channel_credential_raw

_MAX_EXPIRES_AT = 253402300799
_MAX_EXPIRES_AT_STR = str(_MAX_EXPIRES_AT)


def parse_optional_expires_at(value: Any) -> int | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw.isdigit():
        return None
    if len(raw) > len(_MAX_EXPIRES_AT_STR):
        return None
    if len(raw) == len(_MAX_EXPIRES_AT_STR) and raw > _MAX_EXPIRES_AT_STR:
        return None
    return int(raw)


def decode_json_object(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    raw = decode_channel_credential_raw(row)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def optional_str(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if not isinstance(value, str):
        return None
    clean = value.strip()
    return clean or None
