"""Decode Facebook channel_credentials."""

from __future__ import annotations

from typing import Any

from postbridge.api.schemas import FacebookCredentials
from postbridge.integrations._credential_json import (
    decode_json_object,
    optional_str,
    parse_optional_expires_at,
)


def decode_channel_credentials(row: Any) -> FacebookCredentials | None:
    data = decode_json_object(row)
    if data is None:
        return None
    token = optional_str(data, "page_access_token") or optional_str(data, "access_token")
    if token is None:
        return None
    return FacebookCredentials(
        page_access_token=token,
        page_id=optional_str(data, "page_id"),
        graph_api_version=optional_str(data, "graph_api_version"),
        expires_at=parse_optional_expires_at(data.get("expires_at")),
    )
