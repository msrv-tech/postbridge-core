"""Декодирование Telegram channel_credentials (fetch + publish)."""

from __future__ import annotations

import json
from typing import Any

from postbridge.api.schemas import TelegramCredentials
from postbridge.infrastructure.crypto.credentials import decode_channel_credential_raw


def decode_channel_credentials(row: Any) -> TelegramCredentials | None:
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
    api_id = data.get("api_id") if isinstance(data.get("api_id"), str) else ""
    api_hash = data.get("api_hash") if isinstance(data.get("api_hash"), str) else ""
    ss = data.get("session_string")
    bt = data.get("bot_token")
    bt_s = bt.strip() if isinstance(bt, str) and bt.strip() else None
    if bt_s and not (api_id and api_hash):
        return TelegramCredentials(
            api_id="",
            api_hash="",
            session_string=None,
            bot_token=bt_s,
        )
    if not (api_id and api_hash):
        return None
    return TelegramCredentials(
        api_id=api_id,
        api_hash=api_hash,
        session_string=ss if isinstance(ss, str) else None,
        bot_token=bt_s,
    )
