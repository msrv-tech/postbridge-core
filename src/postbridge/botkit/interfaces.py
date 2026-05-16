"""Shared botkit interfaces."""

from __future__ import annotations

from typing import Any, Protocol

from aiogram import Router

from postbridge.botkit.models import LiveSyncTarget, PendingChannel


class BotBackend(Protocol):
    """Backend contract used by thin platform adapters."""

    name: str

    async def complete_web_link(
        self,
        session_token: str,
        telegram_user_id: int,
        telegram_username: str | None,
    ) -> dict[str, Any]: ...

    async def resolve_workspace_id(
        self,
        telegram_user_id: int,
        telegram_username: str | None,
    ) -> str | None: ...

    async def has_attached_channel(
        self,
        telegram_user_id: int,
        telegram_username: str | None,
    ) -> bool: ...

    async def pending_channel(
        self,
        telegram_user_id: int,
        telegram_username: str | None,
    ) -> PendingChannel | None: ...

    async def register_channel(
        self,
        *,
        telegram_user_id: int,
        telegram_username: str | None,
        telegram_chat_id: int,
        title: str,
        added_by_telegram_user_id: int,
    ) -> PendingChannel: ...

    def site_base_url(self) -> str: ...
    def migrate_url(self, workspace_id: str, chat_id: int) -> str: ...
    def dashboard_url(self, workspace_id: str | None = None) -> str: ...
    def media_storage(self): ...
    async def live_sync_target(self, chat_id: int) -> LiveSyncTarget | None: ...


class PlatformAdapter(Protocol):
    """Thin bot platform adapter."""

    name: str

    def build_router(self, backend: BotBackend) -> Router: ...
