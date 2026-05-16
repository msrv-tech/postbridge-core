"""Decode LinkedIn channel_credentials."""

from __future__ import annotations

import json
from typing import Any

from postbridge.api.schemas import LinkedInCredentials
from postbridge.infrastructure.crypto.credentials import decode_channel_credential_raw

_MAX_EXPIRES_AT = 253402300799  # 9999-12-31T23:59:59Z
_MAX_EXPIRES_AT_STR = str(_MAX_EXPIRES_AT)


def _parse_optional_expires_at(value: Any) -> int | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw.isdigit():
        return None
    if len(raw) > len(_MAX_EXPIRES_AT_STR):
        return None
    if len(raw) == len(_MAX_EXPIRES_AT_STR) and raw > _MAX_EXPIRES_AT_STR:
        return None
    parsed = int(raw)
    return parsed


def decode_channel_credentials(row: Any) -> LinkedInCredentials | None:
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
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        return None
    author_urn = data.get("author_urn")
    api_version = data.get("api_version")
    expires_at = data.get("expires_at")
    clean_author = (
        author_urn.strip()
        if isinstance(author_urn, str) and author_urn.strip()
        else None
    )
    clean_version = (
        api_version.strip()
        if isinstance(api_version, str) and api_version.strip()
        else None
    )
    clean_expires_at = _parse_optional_expires_at(expires_at)
    return LinkedInCredentials(
        access_token=access_token.strip(),
        author_urn=clean_author,
        api_version=clean_version,
        expires_at=clean_expires_at,
    )
