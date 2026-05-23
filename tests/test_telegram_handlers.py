"""Unit tests for Telegram bot handler behavior (thin adapter layer)."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from typing import Any

import pytest

from postbridge.botkit.models import LiveSyncTarget, PendingChannel
from postbridge.botkit.platforms.telegram import handlers as tg_handlers
from postbridge.i18n import get_i18n


def _run(coro):
    return asyncio.run(coro)


@dataclass
class _FakeUser:
    id: int
    username: str | None = None


@dataclass
class _FakeChat:
    id: int
    title: str | None = None


@dataclass
class _FakeFile:
    file_path: str


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.files: dict[str, bytes] = {}
        self.raise_on_send = False

    async def get_file(self, file_id: str) -> _FakeFile:
        return _FakeFile(file_path=f"/fake/{file_id}")

    async def download_file(self, file_path: str):
        file_id = file_path.rsplit("/", 1)[-1]
        if file_id not in self.files:
            return None
        return io.BytesIO(self.files[file_id])

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> None:
        if self.raise_on_send:
            raise RuntimeError("send failed")
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})


@dataclass
class _FakePhotoSize:
    file_id: str


@dataclass
class _FakeVideo:
    file_id: str
    file_name: str | None = None


@dataclass
class _FakeDocument:
    file_id: str
    file_name: str | None = None


class _FakeMessage:
    def __init__(
        self,
        *,
        text: str | None = None,
        caption: str | None = None,
        chat: _FakeChat | None = None,
        from_user: _FakeUser | None = None,
        message_id: int = 1,
        bot: _FakeBot | None = None,
        photo: list[_FakePhotoSize] | None = None,
        video: _FakeVideo | None = None,
        document: _FakeDocument | None = None,
        media_group_id: str | None = None,
    ) -> None:
        self.text = text
        self.caption = caption
        self.chat = chat
        self.from_user = from_user
        self.message_id = message_id
        self.bot = bot or _FakeBot()
        self.photo = photo or []
        self.video = video
        self.document = document
        if media_group_id is not None:
            self.media_group_id = media_group_id

        self.answered: list[dict[str, Any]] = []

    async def answer(self, text: str, reply_markup=None) -> None:
        self.answered.append({"text": text, "reply_markup": reply_markup})


class _FakeCallbackQuery:
    def __init__(self, *, from_user: _FakeUser | None, message: _FakeMessage | None) -> None:
        self.from_user = from_user
        self.message = message
        self.answered: list[dict[str, Any]] = []

    async def answer(self, text: str, *, show_alert: bool = False) -> None:
        self.answered.append({"text": text, "show_alert": show_alert})


@dataclass
class _FakeChatMember:
    status: str


class _FakeChatMemberUpdated:
    def __init__(
        self,
        *,
        chat: _FakeChat,
        from_user: _FakeUser | None,
        old_status: str,
        new_status: str,
        bot: _FakeBot | None = None,
    ) -> None:
        self.chat = chat
        self.from_user = from_user
        self.old_chat_member = _FakeChatMember(old_status)
        self.new_chat_member = _FakeChatMember(new_status)
        self.bot = bot or _FakeBot()


class _FakeStorage:
    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []

    async def upload_from_bytes(self, data: bytes, key: str) -> str:
        self.uploads.append({"len": len(data), "key": key})
        return f"https://cdn.example/{key}"


class _FakeBackend:
    def __init__(self, *, name: str = "saas") -> None:
        self.name = name
        self._complete_web_link: dict[str, Any] = {"ok": True}
        self.complete_web_link_calls: list[dict[str, Any]] = []
        self.resolve_workspace_calls: list[dict[str, Any]] = []
        self._workspace_id: str | None = None
        self._pending: PendingChannel | None = None
        self._has_channel = False
        self._registered_pending = PendingChannel(title="t", chat_id=1, workspace_id="w1")
        self._storage: _FakeStorage | None = None
        self._target: LiveSyncTarget | None = None

    async def complete_web_link(self, session_token: str, telegram_user_id: int, telegram_username: str | None):
        self.complete_web_link_calls.append(
            {"token": session_token, "user_id": telegram_user_id, "username": telegram_username}
        )
        return dict(self._complete_web_link)

    async def resolve_workspace_id(self, telegram_user_id: int, telegram_username: str | None):
        self.resolve_workspace_calls.append({"user_id": telegram_user_id, "username": telegram_username})
        return self._workspace_id

    async def has_attached_channel(self, telegram_user_id: int, telegram_username: str | None) -> bool:
        return self._has_channel

    async def pending_channel(self, telegram_user_id: int, telegram_username: str | None):
        return self._pending

    async def register_channel(
        self,
        *,
        telegram_user_id: int,
        telegram_username: str | None,
        telegram_chat_id: int,
        title: str,
        added_by_telegram_user_id: int,
    ):
        return self._registered_pending

    def site_base_url(self) -> str:
        return "https://site.example"

    def migrate_url(self, workspace_id: str, chat_id: int) -> str:
        return f"https://dash.example/migrate/{workspace_id}/{chat_id}"

    def dashboard_url(self, workspace_id: str | None = None) -> str:
        return f"https://dash.example/{workspace_id or ''}".rstrip("/")

    def media_storage(self):
        return self._storage

    async def live_sync_target(self, chat_id: int):
        return self._target


def test_cmd_start_web_empty_token_shows_invalid_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTBRIDGE_DEFAULT_LOCALE", "en")
    backend = _FakeBackend()
    message = _FakeMessage(
        text="/start web_",
        from_user=_FakeUser(10, "u"),
        chat=_FakeChat(1, "c"),
    )
    _run(tg_handlers._handle_cmd_start(message, backend=backend, i18n=get_i18n()))
    assert backend.complete_web_link_calls == []
    assert len(message.answered) == 1


@pytest.mark.parametrize(
    "result",
    [
        {"ok": False, "reason": "invalid_or_expired"},
        {"ok": False, "message": "oops"},
        {"ok": False},
        {"ok": True},
    ],
)
def test_cmd_start_web_link_outcomes(monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]) -> None:
    monkeypatch.setenv("POSTBRIDGE_DEFAULT_LOCALE", "en")
    backend = _FakeBackend()
    backend._complete_web_link = result
    message = _FakeMessage(
        text="/start web_tok123",
        from_user=_FakeUser(10, "u"),
        chat=_FakeChat(1, "c"),
    )
    _run(tg_handlers._handle_cmd_start(message, backend=backend, i18n=get_i18n()))
    assert backend.complete_web_link_calls == [{"token": "tok123", "user_id": 10, "username": "u"}]
    assert len(message.answered) == 1
    if result.get("ok"):
        assert message.answered[0]["reply_markup"] is not None


def test_my_chat_member_register_failure_attempts_dm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTBRIDGE_DEFAULT_LOCALE", "en")
    backend = _FakeBackend()

    async def fail_register(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(backend, "register_channel", fail_register)
    bot = _FakeBot()
    update = _FakeChatMemberUpdated(
        chat=_FakeChat(99, "t"),
        from_user=_FakeUser(1, "u"),
        old_status="left",
        new_status="member",
        bot=bot,
    )
    _run(tg_handlers._handle_my_chat_member(update, backend=backend, i18n=get_i18n()))
    assert len(bot.sent) == 1
    assert bot.sent[0]["chat_id"] == 1


def test_my_chat_member_attached_message_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTBRIDGE_DEFAULT_LOCALE", "en")
    backend = _FakeBackend(name="core_db")
    bot = _FakeBot()
    update = _FakeChatMemberUpdated(
        chat=_FakeChat(99, "t"),
        from_user=_FakeUser(1, "u"),
        old_status="left",
        new_status="administrator",
        bot=bot,
    )
    _run(tg_handlers._handle_my_chat_member(update, backend=backend, i18n=get_i18n()))
    assert len(bot.sent) == 1
    assert bot.sent[0]["reply_markup"] is not None


def test_channel_post_publishes_single_with_media_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _FakeBackend()
    backend._storage = _FakeStorage()
    backend._target = LiveSyncTarget(
        target_channel_id="tgt",
        workspace_id="w1",
        target_platform="vk",
        core_tenant_id="ct",
        target_core_channel_id="cc",
    )

    published: list[dict[str, Any]] = []

    def fake_publish(**kwargs):
        published.append(kwargs)

    monkeypatch.setattr(tg_handlers, "queue_live_sync_publish", fake_publish)

    bot = _FakeBot()
    bot.files["f1"] = b"data"
    message = _FakeMessage(
        chat=_FakeChat(10, "c"),
        from_user=_FakeUser(1, "u"),
        message_id=55,
        bot=bot,
        caption="hello",
        document=_FakeDocument(file_id="f1", file_name="a b?.png"),
    )
    _run(tg_handlers._handle_channel_post(message, backend=backend))

    assert len(backend._storage.uploads) == 1
    assert len(published) == 1
    assert published[0]["post"]["media_url"].startswith("https://cdn.example/live-sync/")


def test_channel_post_media_group_first_schedules_buffered_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _FakeBackend()
    backend._target = LiveSyncTarget(
        target_channel_id="tgt",
        workspace_id="w1",
        target_platform="vk",
        core_tenant_id="ct",
        target_core_channel_id="cc",
    )
    scheduled: list[dict[str, Any]] = []

    def fake_add_to_media_group(**kwargs):
        return True

    def fake_apply_async(*, countdown: int, args: tuple[Any, ...], kwargs: dict[str, Any]):
        scheduled.append({"countdown": countdown, "args": args, "kwargs": kwargs})

    monkeypatch.setattr(tg_handlers, "add_to_media_group", fake_add_to_media_group)
    monkeypatch.setattr(tg_handlers.publish_live_sync_media_group, "apply_async", fake_apply_async)

    published: list[dict[str, Any]] = []

    def fake_publish(**kwargs):
        published.append(kwargs)

    monkeypatch.setattr(tg_handlers, "queue_live_sync_publish", fake_publish)

    message = _FakeMessage(
        chat=_FakeChat(10, "c"),
        from_user=_FakeUser(1, "u"),
        message_id=55,
        text="hello",
        media_group_id="mg1",
    )
    _run(tg_handlers._handle_channel_post(message, backend=backend))
    assert scheduled and not published


def test_channel_post_media_group_buffer_failure_falls_back_to_single(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _FakeBackend()
    backend._target = LiveSyncTarget(
        target_channel_id="tgt",
        workspace_id="w1",
        target_platform="vk",
        core_tenant_id="ct",
        target_core_channel_id="cc",
    )

    def fail_add_to_media_group(**kwargs):
        raise RuntimeError("nope")

    monkeypatch.setattr(tg_handlers, "add_to_media_group", fail_add_to_media_group)

    published: list[dict[str, Any]] = []

    def fake_publish(**kwargs):
        published.append(kwargs)

    monkeypatch.setattr(tg_handlers, "queue_live_sync_publish", fake_publish)

    message = _FakeMessage(
        chat=_FakeChat(10, "c"),
        from_user=_FakeUser(1, "u"),
        message_id=55,
        text="hello",
        media_group_id="mg1",
    )
    _run(tg_handlers._handle_channel_post(message, backend=backend))
    assert len(published) == 1


def test_edited_channel_post_media_group_sets_media_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _FakeBackend()
    backend._target = LiveSyncTarget(
        target_channel_id="tgt",
        workspace_id="w1",
        target_platform="vk",
        core_tenant_id="ct",
        target_core_channel_id="cc",
    )

    edited: list[dict[str, Any]] = []

    def fake_edit(**kwargs):
        edited.append(kwargs)

    monkeypatch.setattr(tg_handlers, "queue_live_sync_edit", fake_edit)
    message = _FakeMessage(
        chat=_FakeChat(10, "c"),
        from_user=_FakeUser(1, "u"),
        message_id=55,
        text="hello",
        media_group_id="mg1",
    )
    _run(tg_handlers._handle_edited_channel_post(message, backend=backend))
    assert edited and edited[0]["post"]["source_post_id"] == "mg:mg1"
    assert "media_urls" in edited[0]["post"]


def test_unknown_callback_fallback_answers_and_sends_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTBRIDGE_DEFAULT_LOCALE", "en")
    backend = _FakeBackend()
    backend._workspace_id = "w1"
    msg = _FakeMessage(from_user=_FakeUser(10, "u"), chat=_FakeChat(1, "c"))
    callback = _FakeCallbackQuery(from_user=_FakeUser(10, "u"), message=msg)
    _run(tg_handlers._handle_unknown_callback_fallback(callback, backend=backend, i18n=get_i18n()))
    assert callback.answered
    assert msg.answered and msg.answered[0]["reply_markup"] is not None
