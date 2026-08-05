"""Decode X channel_credentials."""

from __future__ import annotations

from typing import Any

from postbridge.api.schemas import XCredentials
from postbridge.integrations._credential_json import (
    decode_json_object,
    optional_str,
    parse_optional_expires_at,
)


def decode_channel_credentials(row: Any) -> XCredentials | None:
    data = decode_json_object(row)
    if data is None:
        return None
    token = optional_str(data, "access_token")
    if token is None:
        return None
    return XCredentials(
        access_token=token,
        expires_at=parse_optional_expires_at(data.get("expires_at")),
    )
