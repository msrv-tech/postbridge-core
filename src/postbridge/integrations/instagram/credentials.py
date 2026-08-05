"""Decode Instagram channel_credentials."""

from __future__ import annotations

from typing import Any

from postbridge.api.schemas import InstagramCredentials
from postbridge.integrations._credential_json import (
    decode_json_object,
    optional_str,
    parse_optional_expires_at,
)


def decode_channel_credentials(row: Any) -> InstagramCredentials | None:
    data = decode_json_object(row)
    if data is None:
        return None
    token = optional_str(data, "access_token")
    if token is None:
        return None
    return InstagramCredentials(
        access_token=token,
        instagram_user_id=optional_str(data, "instagram_user_id"),
        graph_api_version=optional_str(data, "graph_api_version"),
        expires_at=parse_optional_expires_at(data.get("expires_at")),
    )
