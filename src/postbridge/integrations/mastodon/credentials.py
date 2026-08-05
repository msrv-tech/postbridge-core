"""Decode Mastodon channel_credentials."""

from __future__ import annotations

from typing import Any

from postbridge.api.schemas import MastodonCredentials
from postbridge.integrations._credential_json import decode_json_object, optional_str


def decode_channel_credentials(row: Any) -> MastodonCredentials | None:
    data = decode_json_object(row)
    if data is None:
        return None
    token = optional_str(data, "access_token")
    instance_url = optional_str(data, "instance_url")
    if token is None:
        return None
    return MastodonCredentials(
        access_token=token,
        instance_url=instance_url,
        visibility=optional_str(data, "visibility"),
    )
