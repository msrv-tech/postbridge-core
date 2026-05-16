"""Парсинг TELEGRAM_PROXY_URL для httpx (Bot API) и Telethon (MTProto)."""

from __future__ import annotations

import asyncio
from urllib.parse import unquote, urlparse

import httpx
import socks

from postbridge.domain.errors import ConfigurationError


def httpx_proxy_arg(url: str | None) -> str | None:
    """URL для ``httpx.Client(proxy=...)`` или ``None`` (прямое соединение)."""
    if url is None:
        return None
    s = url.strip()
    return s if s else None


def aiogram_proxy_url(url: str | None) -> str | None:
    """
    URL для aiogram ``AiohttpSession(proxy=...)``.

    Пакет ``python-socks`` (через ``aiohttp-socks``) не принимает схему ``socks5h://`` в строке;
    у aiogram для SOCKS включается rdns на уровне коннектора, поэтому ``socks5h`` нормализуем в ``socks5``.
    """
    raw = httpx_proxy_arg(url)
    if raw is None:
        return None
    lower = raw.lower()
    if lower.startswith("socks5h://"):
        return "socks5://" + raw.split("://", 1)[1]
    return raw


def telethon_proxy_from_url(url: str | None) -> dict | None:
    """
    Словарь прокси для ``TelegramClient(..., proxy=...)`` (формат Telethon / PySocks).

    Поддержка: ``socks5``, ``socks5h``, ``http``.
    """
    raw = httpx_proxy_arg(url)
    if raw is None:
        return None
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname
    if not host:
        raise ConfigurationError(
            "TELEGRAM_PROXY_URL must include a host (e.g. socks5://127.0.0.1:1080)."
        )

    username = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None

    if scheme in ("socks5", "socks5h"):
        proxy_type = socks.SOCKS5
        rdns = scheme == "socks5h"
        port = parsed.port or 1080
    elif scheme == "http":
        proxy_type = socks.HTTP
        rdns = True
        port = parsed.port or 8080
    else:
        raise ConfigurationError(
            f"Unsupported TELEGRAM_PROXY_URL scheme {scheme!r}. "
            "Use socks5, socks5h, or http."
        )

    cfg: dict = {
        "proxy_type": proxy_type,
        "addr": host,
        "port": port,
        "rdns": rdns,
    }
    if username:
        cfg["username"] = username
    if password is not None:
        cfg["password"] = password
    return cfg


def should_retry_telegram_bot_api_without_proxy(exc: BaseException) -> bool:
    """
    Ошибки транспорта/прокси: одна повторная попытка без прокси (если включён фолбэк).

    Не считаем 4xx от Telegram API поводом для смены маршрута.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (502, 503, 504)
    if isinstance(exc, httpx.ProxyError):
        return True
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return True
    return False


def telethon_infra_error(exc: BaseException) -> bool:
    """Ошибка до/на уровне соединения (не ответ RPC Telegram)."""
    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
        return False
    try:
        from telethon import errors as tg_errors

        if isinstance(exc, tg_errors.RPCError):
            return False
    except Exception:
        pass
    if isinstance(
        exc,
        (
            OSError,
            TimeoutError,
            asyncio.TimeoutError,
            ConnectionError,
            BrokenPipeError,
        ),
    ):
        return True
    if exc.__cause__ is not None:
        return telethon_infra_error(exc.__cause__)
    return False
