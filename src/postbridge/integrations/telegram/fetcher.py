"""Fetcher для чтения постов из Telegram-каналов."""

import logging

from telethon import TelegramClient
from telethon.sessions import StringSession

from postbridge.api.schemas import TelegramCredentials
from postbridge.config import Settings, get_settings
from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.domain.models import PostPayload
from postbridge.integrations.telegram.proxy_config import (
    telethon_infra_error,
    telethon_proxy_from_url,
)

logger = logging.getLogger(__name__)


class TelegramFetcher:
    """Клиент для импорта постов из Telegram-каналов."""

    def __init__(self, settings: Settings | None = None):
        """Инициализирует клиент с настройками (по умолчанию из env)."""
        self.settings = settings or get_settings()

    async def fetch_posts(
        self,
        source_channel: str,
        limit: int,
        credentials: TelegramCredentials | None = None,
        *,
        tenant_id: str | None = None,
    ) -> list[PostPayload]:
        """Забирает посты из канала (limit штук). Возвращает список в хронологическом порядке."""
        _ = tenant_id
        creds = credentials or self._credentials_from_env()
        if not creds.api_id or not creds.api_hash:
            raise ConfigurationError(
                "Missing TELEGRAM_API_ID or TELEGRAM_API_HASH for Telegram import."
            )

        try:
            api_id = int(creds.api_id)
            session = (
                StringSession(creds.session_string)
                if creds.session_string
                else self.settings.telegram_session_name
            )
            entity = source_channel
            if entity.startswith("tg/"):
                try:
                    entity = int(entity[3:])
                except ValueError:
                    entity = entity[3:]
            elif entity.lstrip("-").isdigit():
                entity = int(entity)

            proxy_cfg = telethon_proxy_from_url(self.settings.telegram_proxy_url)
            use_fallback = self.settings.telegram_proxy_fallback_direct

            async def _fetch_with_proxy(proxy: dict | None) -> list[PostPayload]:
                async with TelegramClient(
                    session,
                    api_id=api_id,
                    api_hash=creds.api_hash,
                    proxy=proxy,
                ) as client:
                    messages = []
                    async for message in client.iter_messages(entity, limit=limit):
                        if not message.message:
                            continue
                        messages.append(
                            PostPayload(
                                source_post_id=str(message.id),
                                text=message.message,
                                media_url=None,
                            )
                        )
                    return list(reversed(messages))

            try:
                return await _fetch_with_proxy(proxy_cfg)
            except Exception as first_exc:
                if (
                    use_fallback
                    and proxy_cfg is not None
                    and telethon_infra_error(first_exc)
                ):
                    logger.warning(
                        "telegram_proxy_fallback_used reason=%s source_channel=%s",
                        type(first_exc).__name__,
                        source_channel,
                    )
                    return await _fetch_with_proxy(None)
                raise first_exc
        except Exception as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_TELEGRAM_FETCH_ERROR",
                message="Telegram API request failed",
                source="telegram",
                retryable=True,
                details={
                    "source_channel": source_channel,
                    "limit": limit,
                    "reason": str(exc),
                },
            ) from exc

    def _credentials_from_env(self) -> TelegramCredentials:
        """Читает Telegram-креды из переменных окружения."""
        session_string = self.settings.telegram_session_string
        if session_string and not session_string.strip():
            session_string = None
        return TelegramCredentials(
            api_id=self.settings.telegram_api_id or "",
            api_hash=self.settings.telegram_api_hash or "",
            session_string=session_string,
        )
