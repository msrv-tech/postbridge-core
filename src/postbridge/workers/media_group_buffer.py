"""Буфер альбомов Telegram (Redis) для live-sync."""

from __future__ import annotations

import json
import logging
from typing import Any

from postbridge.config import get_settings

logger = logging.getLogger(__name__)

MG_BUFFER_TTL = 30
MG_DELAY_SECONDS = 1.0


def _get_redis():
    import redis

    return redis.from_url(get_settings().redis_url, decode_responses=True)


def _buffer_keys(chat_id: int | str, media_group_id: str) -> tuple[str, str]:
    prefix = f"live_sync_mg:{chat_id}:{media_group_id}"
    return f"{prefix}:items", f"{prefix}:gen"


def add_to_media_group(
    chat_id: int | str,
    media_group_id: str,
    msg_id: int,
    text: str,
    media_url: str | None,
) -> int | None:
    """Append album item; return debounce generation for Celery scheduling."""
    try:
        r = _get_redis()
        items_key, gen_key = _buffer_keys(chat_id, media_group_id)
        generation = int(r.incr(gen_key))
        r.expire(gen_key, MG_BUFFER_TTL)
        item = json.dumps({"msg_id": msg_id, "text": text, "media_url": media_url}, ensure_ascii=False)
        r.rpush(items_key, item)
        r.expire(items_key, MG_BUFFER_TTL)
        return generation
    except Exception as e:
        logger.warning("media_group_buffer add failed: %s", e)
        return None


def get_media_group_generation(chat_id: int | str, media_group_id: str) -> int | None:
    try:
        r = _get_redis()
        _, gen_key = _buffer_keys(chat_id, media_group_id)
        value = r.get(gen_key)
        return int(value) if value is not None else None
    except Exception as e:
        logger.warning("media_group_buffer generation read failed: %s", e)
        return None


def pop_media_group(chat_id: int | str, media_group_id: str) -> list[dict[str, Any]] | None:
    try:
        r = _get_redis()
        items_key, gen_key = _buffer_keys(chat_id, media_group_id)
        items = r.lrange(items_key, 0, -1)
        r.delete(items_key)
        r.delete(gen_key)
        if not items:
            return None
        return [json.loads(i) for i in items]
    except Exception as e:
        logger.warning("media_group_buffer pop failed: %s", e)
        return None
