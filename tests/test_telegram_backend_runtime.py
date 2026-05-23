"""Unit tests for Telegram backend/runtime helpers.

These tests focus on high-signal branches in the thin adapter layer, without
performing network calls or requiring a real Telegram bot.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import sys
from typing import Any

import pytest

from postbridge.botkit.platforms.telegram import backend as tg_backend
from postbridge.botkit.platforms.telegram import runtime as tg_runtime


def _run(coro):
    return asyncio.run(coro)


@dataclass
class _Settings:
    telegram_bot_token: str | None = None
    telegram_proxy_url: str | None = None
    postbridge_default_locale: str = "en"
    bot_mode: str = "polling"
    bot_webhook_base_url: str | None = None
    bot_webhook_path: str = "/bot/webhook"
    bot_webhook_secret: str | None = None
    bot_backend: str | None = None
    web_app_base_url: str | None = None
    core_base_url: str | None = None
    magic_link_base_url: str | None = None
    saas_bot_secret: str | None = None


class _FakeSession:
    def __init__(self, *, proxy: str | None):
        self.proxy = proxy


class _FakeBot:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.deleted: list[dict[str, Any]] = []
        self.set_commands: list[dict[str, Any]] = []
        self.menu_buttons: list[dict[str, Any]] = []
        self.webhooks: list[dict[str, Any]] = []
        self.raise_on_delete = False

    async def delete_my_commands(self, *, scope, language_code=None) -> None:
        if self.raise_on_delete:
            self.raise_on_delete = False
            raise RuntimeError("delete failed")
        self.deleted.append({"scope": type(scope).__name__, "language_code": language_code})

    async def set_my_commands(self, *, commands, scope, language_code=None) -> None:
        self.set_commands.append(
            {
                "scope": type(scope).__name__,
                "language_code": language_code,
                "commands": [(c.command, c.description) for c in commands],
            }
        )

    async def set_chat_menu_button(self, *, menu_button) -> None:
        self.menu_buttons.append({"text": getattr(menu_button, "text", None)})

    async def set_webhook(self, url: str, *, secret_token=None, allowed_updates=None) -> None:
        self.webhooks.append({"url": url, "secret_token": secret_token, "allowed_updates": list(allowed_updates or [])})


class _FakeDispatcher:
    def __init__(self) -> None:
        self.routers: list[Any] = []

    def include_router(self, router) -> None:
        self.routers.append(router)


class _FakePlatformAdapter:
    def __init__(self, router) -> None:
        self._router = router
        self.build_router_calls: list[Any] = []

    def build_router(self, backend):
        self.build_router_calls.append(backend)
        return self._router


def test_base_backend_url_helpers_use_example_fallback() -> None:
    class _B(tg_backend.BaseBackend):
        def site_base_url(self) -> str:
            return ""

        def media_storage(self):
            return None

        async def complete_web_link(self, session_token: str, telegram_user_id: int, telegram_username: str | None) -> dict:
            raise NotImplementedError

        async def resolve_workspace_id(self, telegram_user_id: int, telegram_username: str | None) -> str | None:
            raise NotImplementedError

        async def has_attached_channel(self, telegram_user_id: int, telegram_username: str | None) -> bool:
            raise NotImplementedError

        async def pending_channel(self, telegram_user_id: int, telegram_username: str | None):
            raise NotImplementedError

        async def register_channel(self, **kwargs):
            raise NotImplementedError

        async def live_sync_target(self, chat_id: int):
            raise NotImplementedError

    b = _B()
    assert b.migrate_url("w1", 123) == "https://example.com"
    assert b.dashboard_url() == "https://example.com"


def test_base_backend_url_helpers_format_with_workspace_and_chat() -> None:
    class _B(tg_backend.BaseBackend):
        def site_base_url(self) -> str:
            return "https://site.example"

        def media_storage(self):
            return None

        async def complete_web_link(self, session_token: str, telegram_user_id: int, telegram_username: str | None) -> dict:
            raise NotImplementedError

        async def resolve_workspace_id(self, telegram_user_id: int, telegram_username: str | None) -> str | None:
            raise NotImplementedError

        async def has_attached_channel(self, telegram_user_id: int, telegram_username: str | None) -> bool:
            raise NotImplementedError

        async def pending_channel(self, telegram_user_id: int, telegram_username: str | None):
            raise NotImplementedError

        async def register_channel(self, **kwargs):
            raise NotImplementedError

        async def live_sync_target(self, chat_id: int):
            raise NotImplementedError

    b = _B()
    assert b.migrate_url("w2", 5) == "https://site.example/workspaces/w2/migrate?channel_id=5"
    assert b.dashboard_url("w2") == "https://site.example/dashboard?workspace=w2"
    assert b.dashboard_url() == "https://site.example/dashboard"


def test_saas_backend_site_base_url_prefers_web_app_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _Settings(magic_link_base_url="https://magic.example", bot_webhook_base_url="https://hook.example")
    monkeypatch.setattr(tg_backend, "get_settings", lambda: settings)
    monkeypatch.setattr(tg_backend, "web_app_base_url", lambda: " https://web.example/ ")
    b = tg_backend.SaasBackend()
    assert b.site_base_url() == "https://web.example"


def test_core_db_backend_site_base_url_prefers_settings_web_app_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _Settings(web_app_base_url=" https://self.example/ ", core_base_url="https://core.example/")
    monkeypatch.setattr(tg_backend, "get_settings", lambda: settings)
    b = tg_backend.CoreDbBackend()
    assert b.site_base_url() == "https://self.example"


def test_get_backend_switches_between_saas_and_core_db(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _Settings(bot_backend="core_db")
    monkeypatch.setattr(tg_backend, "get_settings", lambda: settings)
    assert tg_backend.get_backend().name == "core_db"
    settings.bot_backend = "saas"
    assert tg_backend.get_backend().name == "saas"


def test_runtime_get_bot_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    tg_runtime._bot = None
    monkeypatch.setattr(tg_runtime, "get_settings", lambda: _Settings(telegram_bot_token=None))
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        tg_runtime._get_bot()


def test_runtime_get_bot_builds_session_when_proxy_present(monkeypatch: pytest.MonkeyPatch) -> None:
    tg_runtime._bot = None
    settings = _Settings(telegram_bot_token="tok", telegram_proxy_url="socks5://proxy")
    monkeypatch.setattr(tg_runtime, "get_settings", lambda: settings)
    monkeypatch.setattr(tg_runtime, "aiogram_proxy_url", lambda url: "socks5h://resolved" if url else None)
    monkeypatch.setattr(tg_runtime, "AiohttpSession", _FakeSession)
    monkeypatch.setattr(tg_runtime, "Bot", _FakeBot)
    bot = tg_runtime._get_bot()
    assert isinstance(bot, _FakeBot)
    assert bot.kwargs["token"] == "tok"
    assert isinstance(bot.kwargs["session"], _FakeSession)
    assert bot.kwargs["session"].proxy == "socks5h://resolved"


def test_runtime_get_dispatcher_includes_platform_router_once(monkeypatch: pytest.MonkeyPatch) -> None:
    tg_runtime._dp = None
    sentinel_backend = object()
    sentinel_router = object()
    adapter = _FakePlatformAdapter(router=sentinel_router)
    monkeypatch.setattr(tg_runtime, "Dispatcher", _FakeDispatcher)
    monkeypatch.setattr(tg_runtime, "get_backend", lambda: sentinel_backend)
    monkeypatch.setattr(tg_runtime, "get_platform_adapter", lambda: adapter)
    dp = tg_runtime._get_dispatcher()
    assert isinstance(dp, _FakeDispatcher)
    assert dp.routers == [sentinel_router]
    assert adapter.build_router_calls == [sentinel_backend]
    assert tg_runtime._get_dispatcher() is dp


def test_configure_bot_ui_sets_commands_and_handles_delete_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _Settings(telegram_bot_token="tok", bot_mode="polling", postbridge_default_locale="en")
    bot = _FakeBot(token="tok")
    bot.raise_on_delete = True
    monkeypatch.setattr(tg_runtime, "_get_bot", lambda: bot)
    monkeypatch.setattr(tg_runtime, "get_settings", lambda: settings)

    class _I18N:
        def resolve_locale(self, *, explicit: str):
            class _L:
                locale = explicit

            return _L()

        def translate(self, key: str, *, locale: str) -> str:
            return f"{key}:{locale}"

    monkeypatch.setattr(tg_runtime, "get_i18n", lambda: _I18N())

    class _B:
        def site_base_url(self) -> str:
            return "https://web.example"

    monkeypatch.setattr(tg_runtime, "get_backend", lambda: _B())

    _run(tg_runtime._configure_bot_ui())
    assert bot.set_commands
    assert bot.menu_buttons
    assert bot.webhooks == []


def test_configure_bot_ui_webhook_registers_only_with_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = _FakeBot(token="tok")
    monkeypatch.setattr(tg_runtime, "_get_bot", lambda: bot)

    class _I18N:
        def resolve_locale(self, *, explicit: str):
            class _L:
                locale = explicit

            return _L()

        def translate(self, key: str, *, locale: str) -> str:
            return f"{key}:{locale}"

    monkeypatch.setattr(tg_runtime, "get_i18n", lambda: _I18N())

    class _B:
        def site_base_url(self) -> str:
            return "http://insecure.example"

    monkeypatch.setattr(tg_runtime, "get_backend", lambda: _B())

    settings = _Settings(
        telegram_bot_token="tok",
        bot_mode="webhook",
        bot_webhook_base_url=None,
        bot_webhook_path="/bot/hook",
        bot_webhook_secret="sec",
    )
    monkeypatch.setattr(tg_runtime, "get_settings", lambda: settings)
    _run(tg_runtime._configure_bot_ui())
    assert bot.webhooks == []

    settings.bot_webhook_base_url = "https://api.example/"
    _run(tg_runtime._configure_bot_ui())
    assert bot.webhooks[-1]["url"] == "https://api.example/bot/hook"


def test_saas_backend_live_sync_target_returns_none_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _Settings(saas_bot_secret="sec")
    monkeypatch.setattr(tg_backend, "get_settings", lambda: settings)
    monkeypatch.setattr(tg_backend, "saas_base_url", lambda: "https://saas.example")

    class _Resp:
        status_code = 404

        def raise_for_status(self) -> None:
            raise AssertionError("raise_for_status should not be called for 404")

    class _Client:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str, *, headers: dict[str, str]):
            assert url == "https://saas.example/internal/bot/live-sync-target/123"
            assert headers["X-Bot-Secret"] == "sec"
            return _Resp()

    fake_httpx = type("httpx", (), {"AsyncClient": _Client})
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    b = tg_backend.SaasBackend()
    assert _run(b.live_sync_target(123)) is None


def test_saas_backend_live_sync_target_maps_success_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _Settings(saas_bot_secret="sec")
    monkeypatch.setattr(tg_backend, "get_settings", lambda: settings)
    monkeypatch.setattr(tg_backend, "saas_base_url", lambda: "https://saas.example")

    payload = {
        "target_channel_id": 42,
        "workspace_id": "ws1",
        "target_platform": "max",
        "core_tenant_id": "t1",
        "target_core_channel_id": "c1",
    }

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return payload

    class _Client:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str, *, headers: dict[str, str]):
            assert headers["X-Bot-Secret"] == "sec"
            return _Resp()

    fake_httpx = type("httpx", (), {"AsyncClient": _Client})
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    b = tg_backend.SaasBackend()
    target = _run(b.live_sync_target(1))
    assert target is not None
    assert target.target_channel_id == "42"
    assert target.workspace_id == "ws1"
    assert target.target_platform == "max"
    assert target.core_tenant_id == "t1"
    assert target.target_core_channel_id == "c1"


def test_setup_telegram_bot_webhook_skips_when_not_webhook_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _Settings(bot_mode="polling", telegram_bot_token="tok")
    monkeypatch.setattr(tg_runtime, "get_settings", lambda: settings)
    app = object()
    tg_runtime.setup_telegram_bot_webhook(app)


def test_setup_telegram_bot_webhook_skips_when_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _Settings(bot_mode="webhook", telegram_bot_token=None)
    monkeypatch.setattr(tg_runtime, "get_settings", lambda: settings)
    app = object()
    tg_runtime.setup_telegram_bot_webhook(app)


def test_setup_telegram_bot_webhook_handler_returns_403_200_500(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _Settings(
        bot_mode="webhook",
        telegram_bot_token="tok",
        bot_webhook_path="/bot/hook",
        bot_webhook_secret="sec",
    )
    monkeypatch.setattr(tg_runtime, "get_settings", lambda: settings)

    class _Router:
        def __init__(self) -> None:
            self.routes: list[dict[str, Any]] = []

        def post(self, path: str, *, include_in_schema: bool = False):
            def _decorator(fn):
                self.routes.append({"path": path, "fn": fn})
                return fn

            return _decorator

    class _App:
        def __init__(self) -> None:
            self.routers: list[Any] = []
            self.startups: list[Any] = []

        def include_router(self, router) -> None:
            self.routers.append(router)

        def on_event(self, event: str):
            def _decorator(fn):
                if event == "startup":
                    self.startups.append(fn)
                return fn

            return _decorator

    monkeypatch.setattr(tg_runtime, "APIRouter", _Router)

    configured: list[bool] = []

    async def _fake_configure() -> None:
        configured.append(True)

    monkeypatch.setattr(tg_runtime, "_configure_bot_ui", _fake_configure)

    sentinel_bot = object()
    sentinel_update = object()

    class _Update:
        @classmethod
        def model_validate(cls, body: Any):
            if body == {"boom": True}:
                raise ValueError("bad update")
            return sentinel_update

    monkeypatch.setattr(tg_runtime, "Update", _Update)
    monkeypatch.setattr(tg_runtime, "_get_bot", lambda: sentinel_bot)

    class _DP:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, Any]] = []
            self.raise_error = False

        async def feed_webhook_update(self, bot: Any, update: Any) -> None:
            if self.raise_error:
                raise RuntimeError("dispatcher failed")
            self.calls.append((bot, update))

    dp = _DP()
    monkeypatch.setattr(tg_runtime, "_get_dispatcher", lambda: dp)

    app = _App()
    tg_runtime.setup_telegram_bot_webhook(app)
    assert app.routers
    router = app.routers[0]
    handler = router.routes[0]["fn"]

    class _Req:
        def __init__(self, headers: dict[str, str], body: Any) -> None:
            self.headers = headers
            self._body = body

        async def json(self) -> Any:
            return self._body

    res = _run(handler(_Req(headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}, body={"ok": True})))
    assert res.status_code == 403

    res = _run(handler(_Req(headers={"X-Telegram-Bot-Api-Secret-Token": "sec"}, body={"ok": True})))
    assert res.status_code == 200
    assert dp.calls == [(sentinel_bot, sentinel_update)]

    dp.raise_error = True
    res = _run(handler(_Req(headers={"X-Telegram-Bot-Api-Secret-Token": "sec"}, body={"ok": True})))
    assert res.status_code == 500

    res = _run(handler(_Req(headers={"X-Telegram-Bot-Api-Secret-Token": "sec"}, body={"boom": True})))
    assert res.status_code == 500

    assert app.startups
    _run(app.startups[0]())
    assert configured == [True]


def test_runtime_main_exits_in_webhook_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tg_runtime, "get_settings", lambda: _Settings(bot_mode="webhook"))
    with pytest.raises(SystemExit, match="BOT_MODE=webhook"):
        tg_runtime.main()


def test_runtime_main_runs_polling_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _Settings(bot_mode="polling", telegram_bot_token="tok")
    monkeypatch.setattr(tg_runtime, "get_settings", lambda: settings)

    configured: list[bool] = []

    async def _fake_configure() -> None:
        configured.append(True)

    monkeypatch.setattr(tg_runtime, "_configure_bot_ui", _fake_configure)

    sentinel_bot = object()
    monkeypatch.setattr(tg_runtime, "_get_bot", lambda: sentinel_bot)

    polls: list[object] = []

    class _DP:
        async def start_polling(self, bot: object) -> None:
            polls.append(bot)

    monkeypatch.setattr(tg_runtime, "_get_dispatcher", lambda: _DP())
    monkeypatch.setattr(tg_runtime.logging, "basicConfig", lambda **kwargs: None)

    def _fake_asyncio_run(coro) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()

    monkeypatch.setattr(tg_runtime.asyncio, "run", _fake_asyncio_run)

    tg_runtime.main()
    assert configured == [True]
    assert polls == [sentinel_bot]


def test_core_db_backend_live_sync_target_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []

    class _Session:
        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(tg_backend, "SESSION_LOCAL", lambda: _Session())
    monkeypatch.setattr(tg_backend, "resolve_onprem_live_sync_from_bridges", lambda session, chat_id: None)
    backend = tg_backend.CoreDbBackend()
    assert _run(backend.live_sync_target(123)) is None
    assert closed == [True]


def test_core_db_backend_live_sync_target_maps_context(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Ctx:
        target_channel_external_id = "ext-1"
        workspace_id = "ws-1"
        target_platform = "max"
        tenant_id = "t-1"
        target_core_channel_id = "core-1"

    class _Session:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    session = _Session()
    monkeypatch.setattr(tg_backend, "SESSION_LOCAL", lambda: session)
    monkeypatch.setattr(tg_backend, "resolve_onprem_live_sync_from_bridges", lambda s, chat_id: _Ctx())
    backend = tg_backend.CoreDbBackend()
    target = _run(backend.live_sync_target(7))
    assert target is not None
    assert target.target_channel_id == "ext-1"
    assert target.workspace_id == "ws-1"
    assert target.target_platform == "max"
    assert target.core_tenant_id == "t-1"
    assert target.target_core_channel_id == "core-1"
    assert session.closed is True
