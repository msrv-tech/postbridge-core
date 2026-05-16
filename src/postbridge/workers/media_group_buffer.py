"""Буфер альбомов Telegram (Redis) для live-sync."""

from __future__ import annotations

import json
import logging
from typing import Any

from postbridge.config import get_settings

logger = logging.getLogger(__name__)

MG_BUFFER_TTL = 30
MG_SCHEDULED_TTL = 10
MG_DELAY_SECONDS = 1.0


def _get_redis():
    import redis

    return redis.from_url(get_settings().redis_url, decode_responses=True)


def add_to_media_group(
    chat_id: int | str,
    media_group_id: str,
    msg_id: int,
    text: str,
    media_url: str | None,
) -> bool:
    try:
        r = _get_redis()
        key = f"live_sync_mg:{chat_id}:{media_group_id}"
        scheduled_key = f"live_sync_mg_scheduled:{chat_id}:{media_group_id}"
        item = json.dumps({"msg_id": msg_id, "text": text, "media_url": media_url}, ensure_ascii=False)
        r.rpush(key, item)
        r.expire(key, MG_BUFFER_TTL)
        return bool(r.set(scheduled_key, "1", nx=True, ex=MG_SCHEDULED_TTL))
    except Exception as e:
        logger.warning("media_group_buffer add failed: %s", e)
        return False


def pop_media_group(chat_id: int | str, media_group_id: str) -> list[dict[str, Any]] | None:
    try:
        r = _get_redis()
        key = f"live_sync_mg:{chat_id}:{media_group_id}"
        scheduled_key = f"live_sync_mg_scheduled:{chat_id}:{media_group_id}"
        items = r.lrange(key, 0, -1)
        r.delete(key)
        r.delete(scheduled_key)
        if not items:
            return None
        return [json.loads(i) for i in items]
    except Exception as e:
        logger.warning("media_group_buffer pop failed: %s", e)
        return None
