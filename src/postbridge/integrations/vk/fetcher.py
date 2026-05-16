"""Fetcher для чтения постов из групп VK."""

from __future__ import annotations

import asyncio
from urllib.parse import urlencode

import httpx

from postbridge.api.schemas import VKCredentials
from postbridge.config import Settings, get_settings
from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.domain.models import PostPayload

VK_API_BASE = "https://api.vk.com/method"
VK_API_VERSION = "5.199"


def _parse_group_id(source_channel: str) -> int:
    """Преобразует source_channel в owner_id (отрицательный для групп)."""
    s = source_channel.strip()
    if s.startswith("vk/"):
        s = s[3:]
    if s.startswith("-"):
        return int(s)
    try:
        return -int(s)
    except ValueError:
        raise ConfigurationError(
            f"Invalid VK group id: {source_channel}. Use numeric id or vk/123456."
        ) from None


class VKFetcher:
    """Клиент для импорта постов из групп VK (wall.get)."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    async def fetch_posts(
        self,
        source_channel: str,
        limit: int,
        credentials: VKCredentials | None = None,
        *,
        tenant_id: str | None = None,
    ) -> list[PostPayload]:
        """Забирает посты из стены группы. Возвращает список в хронологическом порядке."""
        _ = tenant_id
        creds = credentials or self._credentials_from_env()
        # wall.get по стене группы с токеном сообщества даёт error 27 (group auth).
        # Чтение стены — через пользовательский токен, если передан.
        read_token = (creds.user_access_token or "").strip() or (creds.access_token or "").strip()
        if not read_token:
            raise ConfigurationError(
                "Missing VK access_token (and optional user_access_token for wall.get) for VK import."
            )

        owner_id = _parse_group_id(source_channel)
        params = {
            "owner_id": owner_id,
            "count": min(limit, 100),
            "offset": 0,
            "access_token": read_token,
            "v": VK_API_VERSION,
        }

        try:
            return await asyncio.to_thread(
                self._fetch_sync,
                params,
                source_channel,
                limit,
            )
        except ExternalApiError:
            raise
        except Exception as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_VK_FETCH_ERROR",
                message="VK API request failed",
                source="vk",
                retryable=True,
                details={
                    "source_channel": source_channel,
                    "limit": limit,
                    "reason": str(exc),
                },
            ) from exc

    def _fetch_sync(
        self,
        params: dict,
        source_channel: str,
        limit: int,
    ) -> list[PostPayload]:
        """Синхронный вызов VK API (для asyncio.to_thread)."""
        url = f"{VK_API_BASE}/wall.get"
        with httpx.Client(timeout=30) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        if "error" in data:
            err = data["error"]
            code = err.get("error_code", 0)
            msg = err.get("error_msg", "Unknown VK error")
            raise ExternalApiError(
                code="EXTERNAL_API_VK_FETCH_ERROR",
                message=f"VK API error: {msg}",
                source="vk",
                retryable=code in (1, 6, 9, 10, 14),
                details={
                    "source_channel": source_channel,
                    "vk_error_code": code,
                    "vk_error_msg": msg,
                },
            )

        items = data.get("response", {}).get("items", [])
        posts: list[PostPayload] = []
        for item in items:
            post_id = item.get("id")
            text = item.get("text", "").strip()
            if not text and not item.get("attachments"):
                continue
            attachments = item.get("attachments", [])
            media_url = None
            if attachments:
                photo = next(
                    (a for a in attachments if a.get("type") == "photo"),
                    None,
                )
                if photo and "photo" in photo:
                    sizes = photo["photo"].get("sizes", [])
                    if sizes:
                        largest = max(
                            sizes,
                            key=lambda s: s.get("width", 0) * s.get("height", 0),
                        )
                        media_url = largest.get("url")
            posts.append(
                PostPayload(
                    source_post_id=str(post_id),
                    text=text or "",
                    media_url=media_url,
                )
            )
            if len(posts) >= limit:
                break

        return list(reversed(posts))

    def _credentials_from_env(self) -> VKCredentials:
        return VKCredentials(
            access_token=self.settings.vk_access_token or "",
            user_access_token=(self.settings.vk_user_access_token or None),
        )
