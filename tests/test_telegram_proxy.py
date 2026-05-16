"""Тесты прокси и фолбэка для Telegram (proxy_config, publisher)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest
import socks
from telethon.errors.rpcerrorlist import FloodWaitError

from postbridge.config import get_settings
from postbridge.domain.errors import ConfigurationError
from postbridge.integrations.telegram.proxy_config import (
    aiogram_proxy_url,
    httpx_proxy_arg,
    should_retry_telegram_bot_api_without_proxy,
    telethon_infra_error,
    telethon_proxy_from_url,
)
from postbridge.integrations.telegram.publisher import TelegramPublisher


@pytest.fixture
def base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    monkeypatch.setenv("APP_ENV", "test")


def test_httpx_proxy_arg_none_and_strip(base_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_PROXY_URL", raising=False)
    assert httpx_proxy_arg(get_settings().telegram_proxy_url) is None
    assert httpx_proxy_arg("  socks5://h:1  ") == "socks5://h:1"
    assert httpx_proxy_arg("") is None
    assert httpx_proxy_arg("   ") is None


def test_aiogram_proxy_url_socks5h_to_socks5(base_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "socks5h://u:p@10.0.0.2:1082")
    assert aiogram_proxy_url(get_settings().telegram_proxy_url) == "socks5://u:p@10.0.0.2:1082"


def test_telethon_proxy_socks5(base_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "socks5://127.0.0.1:1080")
    cfg = telethon_proxy_from_url(get_settings().telegram_proxy_url)
    assert cfg is not None
    assert cfg["proxy_type"] == socks.SOCKS5
    assert cfg["addr"] == "127.0.0.1"
    assert cfg["port"] == 1080
    assert cfg["rdns"] is False


def test_telethon_proxy_socks5h_rdns(base_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "socks5h://proxy.example:1080")
    cfg = telethon_proxy_from_url(get_settings().telegram_proxy_url)
    assert cfg is not None
    assert cfg["rdns"] is True
    assert cfg["port"] == 1080


def test_telethon_proxy_http_auth(base_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "TELEGRAM_PROXY_URL", "http://user:p%40ss@10.0.0.1:8888"
    )
    cfg = telethon_proxy_from_url(get_settings().telegram_proxy_url)
    assert cfg is not None
    assert cfg["proxy_type"] == socks.HTTP
    assert cfg["addr"] == "10.0.0.1"
    assert cfg["port"] == 8888
    assert cfg["username"] == "user"
    assert cfg["password"] == "p@ss"


def test_telethon_proxy_bad_scheme(base_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "https://127.0.0.1:8080")
    with pytest.raises(ConfigurationError, match="Unsupported"):
        telethon_proxy_from_url(get_settings().telegram_proxy_url)


def test_should_retry_bot_api(base_env: None) -> None:
    assert should_retry_telegram_bot_api_without_proxy(httpx.ProxyError("x")) is True
    assert should_retry_telegram_bot_api_without_proxy(
        httpx.ConnectTimeout("t", request=MagicMock())
    ) is True
    req = MagicMock()
    resp = MagicMock()
    resp.status_code = 502
    err = httpx.HTTPStatusError("m", request=req, response=resp)
    assert should_retry_telegram_bot_api_without_proxy(err) is True
    resp.status_code = 401
    err2 = httpx.HTTPStatusError("m", request=req, response=resp)
    assert should_retry_telegram_bot_api_without_proxy(err2) is False


def test_telethon_infra_error(base_env: None) -> None:
    assert telethon_infra_error(OSError(61, "ECONNREFUSED")) is True
    assert telethon_infra_error(FloodWaitError(10)) is False
    inner = OSError("inner")
    outer = RuntimeError("wrap")
    outer.__cause__ = inner
    assert telethon_infra_error(outer) is True


def test_publisher_passes_proxy_to_httpx(
    base_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "socks5://127.0.0.1:1080")
    monkeypatch.setenv("TELEGRAM_PROXY_FALLBACK_DIRECT", "0")
    settings = get_settings()
    proxies: list[str | None] = []

    def client_ctor(*_a: object, **kwargs: object) -> MagicMock:
        proxies.append(kwargs.get("proxy"))  # type: ignore[arg-type]
        inst = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"result": {"message_id": 7}}
        resp.raise_for_status = MagicMock()
        inst.request.return_value = resp
        cm = MagicMock()
        cm.__enter__.return_value = inst
        cm.__exit__.return_value = None
        return cm

    with patch(
        "postbridge.integrations.telegram.publisher.httpx.Client",
        side_effect=client_ctor,
    ):
        pub = TelegramPublisher(settings=settings)
        mid = pub._send_message("tok", "@ch", "hello")
    assert mid == "7"
    assert proxies == ["socks5://127.0.0.1:1080"]


def test_publisher_fallback_second_request_without_proxy(
    base_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "socks5://127.0.0.1:1080")
    monkeypatch.setenv("TELEGRAM_PROXY_FALLBACK_DIRECT", "true")
    settings = get_settings()
    proxies: list[str | None] = []

    def client_ctor(*_a: object, **kwargs: object) -> MagicMock:
        proxies.append(kwargs.get("proxy"))  # type: ignore[arg-type]
        inst = MagicMock()
        idx = len(proxies)

        def do_request(*_ar: object, **_kr: object) -> MagicMock:
            if idx == 1:
                raise httpx.ProxyError("proxy down")
            resp = MagicMock()
            resp.json.return_value = {"result": {"message_id": 99}}
            resp.raise_for_status = MagicMock()
            return resp

        inst.request.side_effect = do_request
        cm = MagicMock()
        cm.__enter__.return_value = inst
        cm.__exit__.return_value = None
        return cm

    with patch(
        "postbridge.integrations.telegram.publisher.httpx.Client",
        side_effect=client_ctor,
    ):
        pub = TelegramPublisher(settings=settings)
        mid = pub._send_message("tok", "@ch", "hello")
    assert mid == "99"
    assert proxies == ["socks5://127.0.0.1:1080", None]


def test_publisher_no_fallback_on_proxy_error(
    base_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "socks5://127.0.0.1:1080")
    monkeypatch.setenv("TELEGRAM_PROXY_FALLBACK_DIRECT", "false")
    settings = get_settings()
    proxies: list[str | None] = []

    def client_ctor(*_a: object, **kwargs: object) -> MagicMock:
        proxies.append(kwargs.get("proxy"))  # type: ignore[arg-type]
        inst = MagicMock()
        inst.request.side_effect = httpx.ProxyError("fail")
        cm = MagicMock()
        cm.__enter__.return_value = inst
        cm.__exit__.return_value = None
        return cm

    with patch(
        "postbridge.integrations.telegram.publisher.httpx.Client",
        side_effect=client_ctor,
    ):
        pub = TelegramPublisher(settings=settings)
        with pytest.raises(httpx.ProxyError):
            pub._send_message("tok", "@ch", "hello")
    assert proxies == ["socks5://127.0.0.1:1080"]
