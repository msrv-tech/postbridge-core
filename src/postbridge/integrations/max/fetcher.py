"""Fetcher для чтения постов из чатов MAX API."""

from __future__ import annotations

import asyncio
import re

import httpx
import requests

from postbridge.api.schemas import MaxCredentials
from postbridge.config import Settings, get_settings
from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.domain.models import PostPayload


def _is_numeric_chat_id(value: str) -> bool:
    return bool(re.match(r"^-?\d+$", value.strip()))


class MaxFetcher:
    """Клиент для импорта постов из чатов MAX (GET /messages)."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    async def fetch_posts(
        self,
        source_channel: str,
        limit: int,
        credentials: MaxCredentials | None = None,
        *,
        tenant_id: str | None = None,
    ) -> list[PostPayload]:
        """Забирает посты из чата MAX. Возвращает список в хронологическом порядке."""
        _ = tenant_id
        creds = credentials or self._credentials_from_env()
        if not creds.base_url or not creds.token:
            raise ConfigurationError(
                "Missing MAX_API_BASE_URL or MAX_API_TOKEN for MAX import."
            )

        try:
            return await asyncio.to_thread(
                self._fetch_sync,
                creds,
                source_channel,
                limit,
            )
        except ExternalApiError:
            raise
        except Exception as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_MAX_FETCH_ERROR",
                message="MAX API fetch failed",
                source="max",
                retryable=True,
                details={
                    "source_channel": source_channel,
                    "limit": limit,
                    "reason": str(exc),
                },
            ) from exc

    def _fetch_sync(
        self,
        creds: MaxCredentials,
        source_channel: str,
        limit: int,
    ) -> list[PostPayload]:
        chat_id = self._resolve_chat_id(creds, source_channel)
        url = f"{creds.base_url.rstrip('/')}/messages"
        headers = {"Authorization": creds.token}
        params = {"chat_id": chat_id, "count": min(limit, 100)}

        with httpx.Client(timeout=30) as client:
            response = client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()

        messages = data.get("messages") or []
        posts: list[PostPayload] = []
        for msg in messages:
            body_obj = msg.get("body")
            if isinstance(body_obj, dict):
                text = (body_obj.get("text") or "").strip()
            else:
                text = ""
            if not text:
                atts = body_obj.get("attachments", []) if isinstance(body_obj, dict) else []
                if not atts:
                    continue
            msg_id = msg.get("id") or msg.get("message_id") or (body_obj.get("mid") if isinstance(body_obj, dict) else None)
            if msg_id is None:
                continue
            media_url = None
            if body_obj:
                atts = body_obj.get("attachments") or []
                for a in atts:
                    if isinstance(a, dict) and a.get("type") == "image":
                        payload = a.get("payload") or {}
                        if isinstance(payload, dict) and payload.get("url"):
                            media_url = payload.get("url")
                            break
            posts.append(
                PostPayload(
                    source_post_id=str(msg_id),
                    text=text or "",
                    media_url=media_url,
                )
            )
            if len(posts) >= limit:
                break

        return list(reversed(posts))

    def _resolve_chat_id(self, creds: MaxCredentials, source_channel: str) -> int:
        if _is_numeric_chat_id(source_channel):
            return int(source_channel.strip())
        chat_id = self._find_chat_by_username(creds, source_channel.strip())
        if chat_id is not None:
            return chat_id
        raise ConfigurationError(
            f"MAX chat not found for source '{source_channel}'. "
            "Ensure the bot is added to the chat, or use numeric chat_id."
        )

    def _find_chat_by_username(self, creds: MaxCredentials, username: str) -> int | None:
        url = f"{creds.base_url.rstrip('/')}/chats"
        headers = {"Authorization": creds.token}
        marker = None
        username_lower = username.lower()

        while True:
            params = {"count": 100}
            if marker is not None:
                params["marker"] = marker
            try:
                resp = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.settings.max_api_timeout_seconds,
                )
                resp.raise_for_status()
            except requests.RequestException:
                return None
            data = resp.json()
            chats = data.get("chats") or []
            for chat in chats:
                cid = chat.get("chat_id")
                link = (chat.get("link") or "").lower()
                title = (chat.get("title") or "").lower()
                if (
                    username_lower in link
                    or username_lower in title
                    or (link.endswith(f"/{username_lower}") or f"/{username_lower}" in link)
                ):
                    return cid
            marker = data.get("marker")
            if marker is None:
                break
        return None

    def _credentials_from_env(self) -> MaxCredentials:
        return MaxCredentials(
            base_url=self.settings.max_api_base_url or "",
            token=self.settings.max_api_token or "",
        )
