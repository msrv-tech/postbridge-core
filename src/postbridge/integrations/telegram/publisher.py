"""Publisher для публикации постов в Telegram-каналы через Bot API.

Используется только Bot API. Telethon — только для платных исторических переносов (TelegramFetcher).
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from postbridge.api.schemas import TelegramCredentials
from postbridge.config import Settings, get_settings
from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.domain.models import PostPayload
from postbridge.integrations.telegram.proxy_config import (
    httpx_proxy_arg,
    should_retry_telegram_bot_api_without_proxy,
)

TG_BOT_API = "https://api.telegram.org/bot"

logger = logging.getLogger(__name__)


def _chat_id_from_channel(target_channel: str) -> str:
    """Преобразует target_channel в chat_id для Bot API (@channel или -100...)."""
    s = target_channel.strip()
    if s.startswith("tg/"):
        s = s[3:]
    if s.startswith("@"):
        return s
    if s.lstrip("-").isdigit():
        return s
    return f"@{s}" if not s.startswith("@") else s


def _is_image_url(url: str) -> bool:
    path = (urlparse(url).path or "").lower()
    return any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"))


class TelegramPublisher:
    """Клиент для публикации постов в Telegram через Bot API."""

    # Через SOCKS первый TLS к api.telegram.org может быть заметно дольше 30 с
    _BOT_API_TIMEOUT = 120.0

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def _request_bot_api(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        """HTTP-запрос к Bot API с опциональным прокси и одним фолбэком на прямой."""
        proxy_url = httpx_proxy_arg(self.settings.telegram_proxy_url)
        timeout = httpx.Timeout(self._BOT_API_TIMEOUT)
        use_fallback = self.settings.telegram_proxy_fallback_direct

        def _once(proxy: str | None) -> httpx.Response:
            with httpx.Client(timeout=timeout, proxy=proxy) as client:
                return client.request(method, url, **kwargs)

        try:
            return _once(proxy_url)
        except Exception as exc:
            if (
                use_fallback
                and proxy_url is not None
                and should_retry_telegram_bot_api_without_proxy(exc)
            ):
                logger.warning(
                    "telegram_proxy_fallback_used reason=%s method=%s",
                    type(exc).__name__,
                    method,
                )
                return _once(None)
            raise

    def publish_post(
        self,
        target_channel: str,
        payload: PostPayload,
        credentials: TelegramCredentials | None = None,
    ) -> str | None:
        """Публикует пост в канал через Bot API. Возвращает message_id."""
        creds = credentials or self._credentials_from_env()
        token = creds.bot_token or ""
        if not token.strip():
            raise ConfigurationError(
                "Missing TELEGRAM_BOT_TOKEN for Telegram publishing. "
                "Use Bot API for publishing; Telethon is only for paid historical migrations."
            )

        chat_id = _chat_id_from_channel(target_channel)
        text = payload.text or ""

        try:
            if payload.media_url and _is_image_url(payload.media_url):
                return self._send_photo(token, chat_id, payload.media_url, text)
            if payload.media_url:
                return self._send_document(token, chat_id, payload.media_url, text)
            if payload.media_urls:
                first = payload.media_urls[0]
                if _is_image_url(first):
                    return self._send_photo(token, chat_id, first, text)
                return self._send_document(token, chat_id, first, text)
            return self._send_message(token, chat_id, text)
        except httpx.HTTPStatusError as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_TELEGRAM_PUBLISH_ERROR",
                message=f"Telegram Bot API error: {exc.response.text[:200]}",
                source="telegram",
                retryable=exc.response.status_code >= 500,
                details={
                    "target_channel": target_channel,
                    "status_code": exc.response.status_code,
                },
            ) from exc
        except httpx.RequestError as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_TELEGRAM_PUBLISH_ERROR",
                message="Telegram Bot API request failed",
                source="telegram",
                retryable=True,
                details={"target_channel": target_channel, "reason": str(exc)},
            ) from exc

    def _send_message(self, token: str, chat_id: str, text: str) -> str | None:
        url = f"{TG_BOT_API}{token}/sendMessage"
        r = self._request_bot_api(
            "POST", url, json={"chat_id": chat_id, "text": text or "."}
        )
        r.raise_for_status()
        data = r.json()
        result = data.get("result")
        if isinstance(result, dict) and "message_id" in result:
            return str(result["message_id"])
        return None

    def _send_photo(
        self, token: str, chat_id: str, photo_url: str, caption: str = ""
    ) -> str | None:
        url = f"{TG_BOT_API}{token}/sendPhoto"
        payload = {"chat_id": chat_id, "photo": photo_url}
        if caption:
            payload["caption"] = caption
        r = self._request_bot_api("POST", url, json=payload)
        r.raise_for_status()
        data = r.json()
        result = data.get("result")
        if isinstance(result, dict) and "message_id" in result:
            return str(result["message_id"])
        return None

    def _send_document(
        self, token: str, chat_id: str, document_url: str, caption: str = ""
    ) -> str | None:
        url = f"{TG_BOT_API}{token}/sendDocument"
        payload = {"chat_id": chat_id, "document": document_url}
        if caption:
            payload["caption"] = caption
        r = self._request_bot_api("POST", url, json=payload)
        r.raise_for_status()
        data = r.json()
        result = data.get("result")
        if isinstance(result, dict) and "message_id" in result:
            return str(result["message_id"])
        return None

    def edit_message(
        self,
        message_id: str,
        text: str = "",
        media_url: str | None = None,
        media_urls: list[str] | None = None,
        credentials: TelegramCredentials | None = None,
        target_channel: str | None = None,
    ) -> None:
        """Редактирует текст сообщения (editMessageText). Медиа не поддерживается — только текст."""
        if not target_channel:
            raise ConfigurationError(
                "target_channel (chat_id) required for Telegram editMessageText"
            )
        creds = credentials or self._credentials_from_env()
        token = creds.bot_token or ""
        if not token.strip():
            raise ConfigurationError(
                "Missing TELEGRAM_BOT_TOKEN for Telegram edit."
            )
        chat_id = _chat_id_from_channel(target_channel)
        url = f"{TG_BOT_API}{token}/editMessageText"
        payload = {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "text": text or ".",
        }
        r = self._request_bot_api("POST", url, json=payload)
        r.raise_for_status()
        # Telegram returns edited Message or True on success

    def _credentials_from_env(self) -> TelegramCredentials:
        return TelegramCredentials(
            api_id=self.settings.telegram_api_id or "",
            api_hash=self.settings.telegram_api_hash or "",
            session_string=self.settings.telegram_session_string,
            bot_token=self.settings.telegram_bot_token,
        )
