"""Unified Telegram bot runtime."""

from __future__ import annotations

import asyncio
import logging
import traceback

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
    MenuButtonWebApp,
    Update,
    WebAppInfo,
)
from fastapi import APIRouter, Request, Response

from postbridge.config import get_settings
from postbridge.integrations.telegram.proxy_config import aiogram_proxy_url
from postbridge.i18n import get_i18n

from . import get_platform_adapter
from .backend import get_backend

logger = logging.getLogger(__name__)

_bot: Bot | None = None
_dp: Dispatcher | None = None
_SUPPORTED_COMMAND_LANGUAGES: tuple[str | None, ...] = (None, "en", "ru")


def _get_bot() -> Bot:
    global _bot
    token = get_settings().telegram_bot_token
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN required")
    if _bot is None:
        settings = get_settings()
        proxy = aiogram_proxy_url(settings.telegram_proxy_url)
        session = AiohttpSession(proxy=proxy) if proxy else None
        kwargs: dict = {
            "token": token,
            "default": DefaultBotProperties(parse_mode=ParseMode.HTML),
        }
        if session is not None:
            kwargs["session"] = session
        _bot = Bot(**kwargs)
    return _bot


def _get_dispatcher() -> Dispatcher:
    global _dp
    if _dp is None:
        _dp = Dispatcher()
        _dp.include_router(get_platform_adapter().build_router(get_backend()))
    return _dp


async def _configure_bot_ui() -> None:
    bot = _get_bot()
    settings = get_settings()
    i18n = get_i18n()

    def _command_locale(language_code: str | None) -> str:
        _ = language_code
        return i18n.resolve_locale(explicit=settings.postbridge_default_locale).locale

    def _commands_for_locale(locale: str) -> list[BotCommand]:
        return [
            BotCommand(
                command="start",
                description=i18n.translate("bot.command.start", locale=locale),
            ),
            BotCommand(
                command="help",
                description=i18n.translate("bot.command.help", locale=locale),
            ),
        ]

    scopes = (
        BotCommandScopeDefault(),
        BotCommandScopeAllPrivateChats(),
    )
    for scope in scopes:
        for language_code in _SUPPORTED_COMMAND_LANGUAGES:
            try:
                await bot.delete_my_commands(scope=scope, language_code=language_code)
            except Exception:
                logger.warning(
                    "Failed to delete Telegram commands for scope=%s language=%s",
                    type(scope).__name__,
                    language_code or "<default>",
                    exc_info=True,
                )
            await bot.set_my_commands(
                commands=_commands_for_locale(_command_locale(language_code)),
                scope=scope,
                language_code=language_code,
            )
    web_app_url = get_backend().site_base_url()
    if web_app_url.startswith("https://"):
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Open", web_app=WebAppInfo(url=web_app_url)),
        )
    else:
        logger.warning("Menu button skipped: web base URL must be HTTPS")
    if settings.bot_mode == "webhook":
        base = (settings.bot_webhook_base_url or "").rstrip("/")
        if not base:
            logger.warning("BOT_WEBHOOK_BASE_URL not set, skipping Telegram webhook registration")
            return
        await bot.set_webhook(
            f"{base}{settings.bot_webhook_path}",
            secret_token=settings.bot_webhook_secret,
            allowed_updates=[
                "message",
                "callback_query",
                "my_chat_member",
                "channel_post",
                "edited_channel_post",
            ],
        )


def setup_telegram_bot_webhook(app) -> None:
    settings = get_settings()
    if settings.bot_mode != "webhook":
        return
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not set, Telegram bot webhook disabled")
        return

    router = APIRouter()

    @router.post(settings.bot_webhook_path, include_in_schema=False)
    async def telegram_webhook(request: Request) -> Response:
        sec = settings.bot_webhook_secret
        if sec and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != sec:
            return Response(status_code=403)
        try:
            body = await request.json()
            update = Update.model_validate(body)
            await _get_dispatcher().feed_webhook_update(_get_bot(), update)
            return Response(status_code=200)
        except Exception:
            logger.exception("Telegram webhook handler failed\n%s", traceback.format_exc())
            return Response(status_code=500)

    app.include_router(router)

    @app.on_event("startup")
    async def _bot_startup() -> None:
        await _configure_bot_ui()


def main() -> None:
    settings = get_settings()
    if settings.bot_mode == "webhook":
        raise SystemExit(
            "BOT_MODE=webhook: run the Core API process instead of the polling bot entrypoint."
        )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bot = _get_bot()
    dp = _get_dispatcher()

    async def _run() -> None:
        await _configure_bot_ui()
        await dp.start_polling(bot)

    asyncio.run(_run())
