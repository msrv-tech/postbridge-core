"""HTTP client to SaaS internal bot endpoints."""

from __future__ import annotations

from typing import Any

import httpx

from postbridge.config import get_settings

HTTP_TIMEOUT = 30.0


def saas_base_url() -> str:
    url = get_settings().saas_base_url
    if not url or not url.strip():
        raise RuntimeError("SAAS_BASE_URL is required for Telegram bot backend=saas")
    return url.rstrip("/")


def web_app_base_url() -> str:
    settings = get_settings()
    return (
        (settings.web_app_base_url or "").strip()
        or (settings.magic_link_base_url or "").strip()
        or (settings.bot_webhook_base_url or "").strip()
        or "http://localhost:5173"
    )


def bot_headers() -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    secret = get_settings().saas_bot_secret
    if secret:
        headers["X-Bot-Secret"] = secret
    return headers


async def ensure_user(telegram_user_id: int, username: str | None) -> tuple[str, str]:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(
            f"{saas_base_url()}/internal/bot/ensure-user",
            json={
                "telegram_user_id": telegram_user_id,
                "telegram_username": username,
            },
            headers=bot_headers(),
        )
        response.raise_for_status()
        data = response.json()
        return data["user_id"], data["workspace_id"]


async def pending_channel(user_id: str) -> dict | None:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.get(
            f"{saas_base_url()}/internal/bot/pending-channel",
            params={"user_id": user_id},
            headers=bot_headers(),
        )
        response.raise_for_status()
        data = response.json()
        channel = data.get("channel")
        return channel if isinstance(channel, dict) else None


async def has_telegram_channel(saas_user_id: str) -> bool:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.get(
            f"{saas_base_url()}/internal/bot/user/{saas_user_id}/has-telegram-channel",
            headers=bot_headers(),
        )
        response.raise_for_status()
        return bool(response.json().get("has"))


async def user_workspace(saas_user_id: str) -> str:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.get(
            f"{saas_base_url()}/internal/bot/user/{saas_user_id}/workspace",
            headers=bot_headers(),
        )
        response.raise_for_status()
        return str(response.json().get("workspace_id") or "")


async def register_telegram_channel(
    *,
    telegram_user_id: int,
    telegram_username: str | None,
    telegram_chat_id: int,
    title: str,
    added_by_telegram_user_id: int,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(
            f"{saas_base_url()}/internal/bot/register-telegram-channel",
            headers=bot_headers(),
            json={
                "telegram_user_id": telegram_user_id,
                "telegram_username": telegram_username,
                "telegram_chat_id": telegram_chat_id,
                "title": title,
                "added_by_telegram_user_id": added_by_telegram_user_id,
            },
        )
        response.raise_for_status()
        return response.json()


async def complete_telegram_web_link(
    *,
    session_token: str,
    telegram_user_id: int,
    telegram_username: str | None,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(
            f"{saas_base_url()}/internal/bot/complete-telegram-web-link",
            headers=bot_headers(),
            json={
                "session_token": session_token,
                "telegram_user_id": telegram_user_id,
                "telegram_username": telegram_username,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}
