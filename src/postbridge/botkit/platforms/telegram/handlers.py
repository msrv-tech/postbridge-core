"""Thin Telegram bot handlers."""

from __future__ import annotations

import logging
import re
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, or_f
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup, Message

from postbridge.config import get_settings
from postbridge.botkit.interfaces import BotBackend
from postbridge.i18n import get_i18n
from postbridge.services.live_sync_queue import queue_live_sync_edit, queue_live_sync_publish
from postbridge.workers.live_sync_tasks import publish_live_sync_media_group
from postbridge.workers.media_group_buffer import MG_DELAY_SECONDS, add_to_media_group

logger = logging.getLogger(__name__)


def build_router(backend: BotBackend) -> Router:
    router = Router()
    i18n = get_i18n()

    def _extract_media_file_id(message: Message) -> tuple[str, str, str | None]:
        if message.photo:
            return message.photo[-1].file_id, "jpg", None
        if message.video:
            fn = message.video.file_name or ""
            ext = fn.split(".")[-1] if fn else "mp4"
            return message.video.file_id, ext or "mp4", fn or None
        if message.document:
            fn = message.document.file_name or ""
            ext = fn.split(".")[-1] if fn else "bin"
            return message.document.file_id, ext or "bin", fn or None
        return "", "", None

    def _sanitize_filename(name: str, max_len: int = 120) -> str:
        s = re.sub(r"[^\w\-\.\s]", "_", name)
        s = re.sub(r"\s+", "_", s).strip("._")
        return (s or "file")[:max_len]

    async def _download_telegram_file(bot, file_id: str) -> bytes | None:
        try:
            file = await bot.get_file(file_id)
            bio = await bot.download_file(file.file_path)
            return bio.read() if bio else None
        except Exception:
            return None

    def _telegram_bot_link() -> str:
        username = get_settings().telegram_bot_username
        return f"https://t.me/{username}" if username else get_settings().core_base_url

    def _resolve_locale() -> str:
        return i18n.resolve_locale(explicit=get_settings().postbridge_default_locale).locale

    def _open_web_keyboard(label: str, url: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=label, url=url)]]
        )

    async def _send_web_handoff(
        message: Message,
        *,
        text: str,
        button_label: str,
        workspace_id: str | None = None,
        url: str | None = None,
    ) -> None:
        target_url = url or backend.dashboard_url(workspace_id)
        await message.answer(
            text,
            reply_markup=_open_web_keyboard(button_label, target_url),
        )

    async def _send_start_view(message: Message) -> None:
        user = message.from_user
        if not user:
            return
        locale = _resolve_locale()

        workspace_id = await backend.resolve_workspace_id(user.id, user.username)
        pending = await backend.pending_channel(user.id, user.username)
        if pending:
            await _send_web_handoff(
                message,
                text=i18n.translate(
                    "bot.start.pending_connected",
                    locale=locale,
                    params={"title": pending.title},
                ),
                button_label=i18n.translate("bot.button.continue_web", locale=locale),
                workspace_id=pending.workspace_id,
                url=backend.migrate_url(pending.workspace_id or "", pending.chat_id)
                if pending.workspace_id
                else backend.dashboard_url(workspace_id),
            )
            return

        has_channel = await backend.has_attached_channel(user.id, user.username)
        if has_channel:
            await _send_web_handoff(
                message,
                text=i18n.translate("bot.start.already_connected", locale=locale),
                button_label=i18n.translate("bot.button.open_web", locale=locale),
                workspace_id=workspace_id,
            )
            return

        text = i18n.translate(
            "bot.start.saas_intro",
            locale=locale,
            params={"bot_link": _telegram_bot_link()},
        )
        if backend.name == "core_db":
            text = i18n.translate(
                "bot.start.core_db_intro",
                locale=locale,
                params={"bot_link": _telegram_bot_link()},
            )
        await _send_web_handoff(
            message,
            text=text,
            button_label=i18n.translate("bot.button.open_web", locale=locale),
            workspace_id=workspace_id,
        )

    @router.message(or_f(CommandStart(), Command("help")))
    async def cmd_start(message: Message) -> None:
        text = (message.text or "").strip()
        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            if len(parts) > 1:
                arg0 = parts[1].strip()
                if arg0.startswith("web_"):
                    user = message.from_user
                    if not user:
                        return
                    locale = _resolve_locale()
                    token = arg0.removeprefix("web_").strip()
                    if not token:
                        await message.answer(i18n.translate("bot.auth.invalid_link", locale=locale))
                        return
                    data = await backend.complete_web_link(token, user.id, user.username)
                    if not data.get("ok"):
                        reason = data.get("reason")
                        if reason == "invalid_or_expired":
                            await message.answer(
                                i18n.translate("bot.auth.invalid_or_expired_link", locale=locale)
                            )
                        elif data.get("message"):
                            await message.answer(
                                i18n.translate(
                                    "bot.auth.failed_with_message",
                                    locale=locale,
                                    params={"message": data["message"]},
                                )
                            )
                        else:
                            await message.answer(
                                i18n.translate("bot.auth.failed_generic", locale=locale)
                            )
                        return
                    await _send_web_handoff(
                        message,
                        text=i18n.translate("bot.auth.connected", locale=locale),
                        button_label=i18n.translate("bot.button.open_web", locale=locale),
                    )
                    return
        await _send_start_view(message)

    @router.my_chat_member(F.chat.type.in_({"channel", "supergroup"}))
    async def on_my_chat_member(update: ChatMemberUpdated) -> None:
        chat = update.chat
        from_user = update.from_user
        if update.new_chat_member.status not in ("administrator", "member"):
            return
        if update.old_chat_member.status in ("administrator", "member"):
            return
        if not from_user:
            return
        locale = _resolve_locale()

        title = chat.title or "channel"
        try:
            pending = await backend.register_channel(
                telegram_user_id=from_user.id,
                telegram_username=from_user.username,
                telegram_chat_id=chat.id,
                title=title,
                added_by_telegram_user_id=from_user.id,
            )
        except Exception as e:
            logger.exception("channel registration failed: %s", e)
            try:
                await update.bot.send_message(
                    from_user.id,
                    i18n.translate(
                        "bot.channel.register_failed",
                        locale=locale,
                        params={"error": str(e)},
                    ),
                )
            except Exception:
                pass
            return

        target_url = (
            backend.migrate_url(pending.workspace_id, pending.chat_id)
            if pending.workspace_id
            else backend.dashboard_url()
        )
        msg = i18n.translate(
            "bot.channel.connected",
            locale=locale,
            params={"title": title},
        )
        if backend.name == "core_db":
            msg = i18n.translate(
                "bot.channel.attached",
                locale=locale,
                params={"title": title},
            )
        try:
            await update.bot.send_message(
                from_user.id,
                msg,
                reply_markup=_open_web_keyboard(
                    i18n.translate("bot.button.continue_web", locale=locale),
                    target_url,
                ),
            )
        except Exception:
            logger.warning("cannot send attach confirmation to user_id=%s", from_user.id)

    @router.message(or_f(Command("add"), Command("sync"), Command("plan"), Command("max"), Command("vk"), Command("rss")))
    async def cmd_open_web(message: Message) -> None:
        workspace_id = None
        locale = _resolve_locale()
        if message.from_user:
            workspace_id = await backend.resolve_workspace_id(
                message.from_user.id,
                message.from_user.username,
            )
        await _send_web_handoff(
            message,
            text=i18n.translate("bot.command.moved_to_web", locale=locale),
            button_label=i18n.translate("bot.button.open_web", locale=locale),
            workspace_id=workspace_id,
        )

    @router.message(F.text & ~F.text.startswith("/"))
    async def on_text_fallback(message: Message) -> None:
        workspace_id = None
        locale = _resolve_locale()
        if message.from_user:
            workspace_id = await backend.resolve_workspace_id(
                message.from_user.id,
                message.from_user.username,
            )
        await _send_web_handoff(
            message,
            text=i18n.translate("bot.fallback.text", locale=locale),
            button_label=i18n.translate("bot.button.open_web", locale=locale),
            workspace_id=workspace_id,
        )

    @router.message()
    async def on_message_fallback(message: Message) -> None:
        workspace_id = None
        locale = _resolve_locale()
        if message.from_user:
            workspace_id = await backend.resolve_workspace_id(
                message.from_user.id,
                message.from_user.username,
            )
        await _send_web_handoff(
            message,
            text=i18n.translate("bot.fallback.message", locale=locale),
            button_label=i18n.translate("bot.button.open_web", locale=locale),
            workspace_id=workspace_id,
        )

    @router.channel_post()
    async def on_channel_post(message: Message) -> None:
        chat_id = message.chat.id if message.chat else None
        if chat_id is None:
            return
        target_data = await backend.live_sync_target(chat_id)
        if target_data is None:
            return
        target = target_data.target_channel_id
        workspace_id = target_data.workspace_id
        target_platform = target_data.target_platform
        core_tenant_id = target_data.core_tenant_id
        target_core_channel_id = target_data.target_core_channel_id
        if not core_tenant_id or not target_core_channel_id:
            return
        text = message.text or message.caption or ""
        has_media = bool(message.photo or message.video or message.document)
        if not text.strip() and not has_media:
            return
        media_url: str | None = None
        storage = backend.media_storage()
        if storage:
            file_id, ext, orig_name = _extract_media_file_id(message)
            if file_id:
                safe_name = _sanitize_filename(orig_name) if orig_name else f"file.{ext}"
                key = f"live-sync/{workspace_id or 'selfhost'}/{message.message_id}_{file_id[:16]}_{safe_name}"
                file_bytes = await _download_telegram_file(message.bot, file_id)
                if file_bytes:
                    media_url = await storage.upload_from_bytes(file_bytes, key)

        media_group_id = getattr(message, "media_group_id", None)
        if media_group_id:
            try:
                is_first = add_to_media_group(
                    chat_id=chat_id,
                    media_group_id=media_group_id,
                    msg_id=message.message_id,
                    text=text,
                    media_url=media_url,
                )
                if is_first:
                    publish_live_sync_media_group.apply_async(
                        countdown=MG_DELAY_SECONDS,
                        args=(str(chat_id), target, workspace_id, media_group_id),
                        kwargs={
                            "target_platform": target_platform,
                            "core_tenant_id": core_tenant_id,
                            "target_core_channel_id": target_core_channel_id,
                            "producer": f"telegram_{backend.name}",
                        },
                    )
                return
            except Exception as e:
                logger.warning("media_group buffer failed, publishing as single: %s", e)

        post = {
            "source_post_id": str(message.message_id),
            "text": text,
            "media_url": media_url,
        }
        queue_live_sync_publish(
            source_channel=str(chat_id),
            target_channel=target,
            post=post,
            workspace_id=workspace_id,
            target_platform=target_platform,
            core_tenant_id=core_tenant_id,
            target_core_channel_id=target_core_channel_id,
            producer=f"telegram_{backend.name}",
        )

    @router.edited_channel_post()
    async def on_edited_channel_post(message: Message) -> None:
        chat_id = message.chat.id if message.chat else None
        if chat_id is None:
            return
        target_data = await backend.live_sync_target(chat_id)
        if target_data is None:
            return
        target = target_data.target_channel_id
        workspace_id = target_data.workspace_id
        target_platform = target_data.target_platform
        core_tenant_id = target_data.core_tenant_id
        target_core_channel_id = target_data.target_core_channel_id
        if not core_tenant_id or not target_core_channel_id:
            return
        text = message.text or message.caption or ""
        has_media = bool(message.photo or message.video or message.document)
        if not text.strip() and not has_media:
            return
        media_url: str | None = None
        storage = backend.media_storage()
        if storage:
            file_id, ext, orig_name = _extract_media_file_id(message)
            if file_id:
                safe_name = _sanitize_filename(orig_name) if orig_name else f"file.{ext}"
                key = f"live-sync/{workspace_id or 'selfhost'}/{message.message_id}_{file_id[:16]}_{safe_name}"
                file_bytes = await _download_telegram_file(message.bot, file_id)
                if file_bytes:
                    media_url = await storage.upload_from_bytes(file_bytes, key)
        media_group_id = getattr(message, "media_group_id", None)
        source_post_id = f"mg:{media_group_id}" if media_group_id else str(message.message_id)
        post: dict[str, Any] = {
            "source_post_id": source_post_id,
            "text": text,
            "media_url": media_url,
        }
        if media_group_id:
            post["media_urls"] = [media_url] if media_url else []
        queue_live_sync_edit(
            source_channel=str(chat_id),
            target_channel=target,
            post=post,
            target_platform=target_platform,
            workspace_id=workspace_id,
            core_tenant_id=core_tenant_id,
            target_core_channel_id=target_core_channel_id,
            producer=f"telegram_{backend.name}",
        )

    @router.callback_query()
    async def on_unknown_callback_fallback(callback: CallbackQuery) -> None:
        workspace_id = None
        locale = _resolve_locale()
        if callback.from_user:
            workspace_id = await backend.resolve_workspace_id(
                callback.from_user.id,
                callback.from_user.username,
            )
        await callback.answer(i18n.translate("bot.fallback.callback", locale=locale), show_alert=False)
        if callback.message:
            await _send_web_handoff(
                callback.message,
                text=i18n.translate("bot.fallback.callback_flow", locale=locale),
                button_label=i18n.translate("bot.button.open_web", locale=locale),
                workspace_id=workspace_id,
            )

    return router
