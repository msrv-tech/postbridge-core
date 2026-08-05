"""Decode Bluesky channel_credentials."""

from __future__ import annotations

from typing import Any

from postbridge.api.schemas import BlueskyCredentials
from postbridge.integrations._credential_json import decode_json_object, optional_str


def decode_channel_credentials(row: Any) -> BlueskyCredentials | None:
    data = decode_json_object(row)
    if data is None:
        return None
    identifier = optional_str(data, "identifier") or optional_str(data, "handle")
    app_password = optional_str(data, "app_password") or optional_str(data, "password")
    if identifier is None or app_password is None:
        return None
    return BlueskyCredentials(
        identifier=identifier,
        app_password=app_password,
        service_url=optional_str(data, "service_url"),
    )
