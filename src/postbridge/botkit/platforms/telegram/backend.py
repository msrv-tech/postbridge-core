"""Backend adapters for the thin Telegram bot."""

from __future__ import annotations

from sqlalchemy.orm import Session

from postbridge.botkit.interfaces import BotBackend
from postbridge.botkit.local_storage import get_local_storage
from postbridge.botkit.media_factory import get_media_storage_provider
from postbridge.botkit.models import LiveSyncTarget, PendingChannel
from postbridge.botkit.saas_http import (
    complete_telegram_web_link as saas_complete_telegram_web_link,
    ensure_user as saas_ensure_user,
    has_telegram_channel as saas_has_telegram_channel,
    pending_channel as saas_pending_channel,
    register_telegram_channel as saas_register_telegram_channel,
    saas_base_url,
    web_app_base_url,
)
from postbridge.config import get_settings
from postbridge.db import SESSION_LOCAL
from postbridge.services.onprem_live_sync_resolve import resolve_onprem_live_sync_from_bridges

class BaseBackend(BotBackend):
    """Common URL helpers shared by Telegram backends."""

    def migrate_url(self, workspace_id: str, chat_id: int) -> str:
        base = self.site_base_url()
        if not base:
            return "https://example.com"
        return f"{base}/workspaces/{workspace_id}/migrate?channel_id={chat_id}"

    def dashboard_url(self, workspace_id: str | None = None) -> str:
        base = self.site_base_url()
        if not base:
            return "https://example.com"
        if workspace_id:
            return f"{base}/dashboard?workspace={workspace_id}"
        return f"{base}/dashboard"


class SaasBackend(BaseBackend):
    name = "saas"

    async def complete_web_link(self, session_token: str, telegram_user_id: int, telegram_username: str | None) -> dict:
        return await saas_complete_telegram_web_link(
            session_token=session_token,
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
        )

    async def _canonical_identity(self, telegram_user_id: int, telegram_username: str | None) -> tuple[str, str] | None:
        """Resolve one server-owned actor/workspace pair, without a derived fallback."""
        try:
            identity = await saas_ensure_user(telegram_user_id, telegram_username)
            if not isinstance(identity, tuple) or len(identity) != 2:
                return None
            if not all(isinstance(value, str) and value and value == value.strip() for value in identity):
                return None
            return identity
        except Exception:
            return None

    @staticmethod
    def _matches_identity(row: dict, identity: tuple[str, str]) -> bool:
        user_id, workspace_id = identity
        return row.get("user_id") == user_id and (
            "workspace_id" not in row or row["workspace_id"] == workspace_id
        )

    async def resolve_workspace_id(self, telegram_user_id: int, telegram_username: str | None) -> str | None:
        identity = await self._canonical_identity(telegram_user_id, telegram_username)
        return identity[1] if identity else None

    async def has_attached_channel(self, telegram_user_id: int, telegram_username: str | None) -> bool:
        identity = await self._canonical_identity(telegram_user_id, telegram_username)
        if identity is None:
            return False
        return await saas_has_telegram_channel(identity[0])

    async def pending_channel(self, telegram_user_id: int, telegram_username: str | None) -> PendingChannel | None:
        identity = await self._canonical_identity(telegram_user_id, telegram_username)
        if identity is None:
            return None
        row = await saas_pending_channel(identity[0])
        if not row or not self._matches_identity(row, identity):
            return None
        return PendingChannel(
            title=str(row["title"]),
            chat_id=int(row["platform_channel_id"]),
            workspace_id=identity[1],
        )

    async def register_channel(
        self,
        *,
        telegram_user_id: int,
        telegram_username: str | None,
        telegram_chat_id: int,
        title: str,
        added_by_telegram_user_id: int,
    ) -> PendingChannel:
        identity = await self._canonical_identity(telegram_user_id, telegram_username)
        if identity is None:
            raise RuntimeError("Canonical Telegram identity is unavailable")
        row = await saas_register_telegram_channel(
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            telegram_chat_id=telegram_chat_id,
            title=title,
            added_by_telegram_user_id=added_by_telegram_user_id,
        )
        if not self._matches_identity(row, identity):
            raise RuntimeError("Telegram registration identity changed")
        return PendingChannel(
            title=str(row["title"]),
            chat_id=int(row["platform_channel_id"]),
            workspace_id=identity[1],
        )

    def site_base_url(self) -> str:
        return (
            (web_app_base_url() or "").strip()
            or (get_settings().magic_link_base_url or "").strip()
            or (get_settings().bot_webhook_base_url or "").strip()
        ).rstrip("/")

    def media_storage(self):
        return get_media_storage_provider()

    async def live_sync_target(self, chat_id: int) -> LiveSyncTarget | None:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{saas_base_url()}/internal/bot/live-sync-target/{chat_id}",
                headers={"X-Bot-Secret": get_settings().saas_bot_secret or ""},
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
            return LiveSyncTarget(
                target_channel_id=str(data["target_channel_id"]),
                workspace_id=str(data.get("workspace_id", "")),
                target_platform=str(data.get("target_platform", "max")),
                core_tenant_id=str(data.get("core_tenant_id") or ""),
                target_core_channel_id=str(data.get("target_core_channel_id") or ""),
            )


class CoreDbBackend(BaseBackend):
    name = "core_db"

    async def complete_web_link(self, session_token: str, telegram_user_id: int, telegram_username: str | None) -> dict:
        return {"ok": False, "reason": "unsupported", "message": "Web auth is not available in self-hosted mode."}

    async def resolve_workspace_id(self, telegram_user_id: int, telegram_username: str | None) -> str | None:
        _ = telegram_user_id, telegram_username
        return None

    async def has_attached_channel(self, telegram_user_id: int, telegram_username: str | None) -> bool:
        _ = telegram_user_id, telegram_username
        return False

    async def pending_channel(self, telegram_user_id: int, telegram_username: str | None) -> PendingChannel | None:
        _ = telegram_user_id, telegram_username
        return None

    async def register_channel(
        self,
        *,
        telegram_user_id: int,
        telegram_username: str | None,
        telegram_chat_id: int,
        title: str,
        added_by_telegram_user_id: int,
    ) -> PendingChannel:
        _ = telegram_user_id, telegram_username, added_by_telegram_user_id
        return PendingChannel(title=title, chat_id=telegram_chat_id, workspace_id=None)

    def site_base_url(self) -> str:
        base = (get_settings().web_app_base_url or "").strip()
        if base:
            return base.rstrip("/")
        return (get_settings().core_base_url or "").strip().rstrip("/")

    def media_storage(self):
        return get_local_storage()

    async def live_sync_target(self, chat_id: int) -> LiveSyncTarget | None:
        session: Session = SESSION_LOCAL()
        try:
            ctx = resolve_onprem_live_sync_from_bridges(session, chat_id)
        finally:
            session.close()
        if ctx is None:
            return None
        return LiveSyncTarget(
            target_channel_id=ctx.target_channel_external_id,
            workspace_id=ctx.workspace_id,
            target_platform=ctx.target_platform,
            core_tenant_id=ctx.tenant_id,
            target_core_channel_id=ctx.target_core_channel_id,
        )


def get_backend() -> BotBackend:
    backend = (get_settings().bot_backend or "saas").strip().lower()
    if backend == "core_db":
        return CoreDbBackend()
    return SaasBackend()
