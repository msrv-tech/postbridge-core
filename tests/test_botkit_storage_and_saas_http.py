from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from postbridge.botkit import media_factory, media_local, media_s3, saas_http


class _Response:
    def __init__(self, *, json_data: object | None = None, content: bytes = b"payload") -> None:
        self._json_data = json_data
        self.content = content
        self.raised = False

    def raise_for_status(self) -> None:
        self.raised = True

    def json(self) -> object:
        return self._json_data


class _AsyncClient:
    calls: list[tuple[str, str, dict]]

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.calls = []

    async def __aenter__(self) -> "_AsyncClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, **kwargs: object) -> _Response:
        self.calls.append(("get", url, kwargs))
        if url.endswith("/pending-channel"):
            return _Response(json_data={"channel": {"id": "channel"}})
        if url.endswith("/has-telegram-channel"):
            return _Response(json_data={"has": True})
        if url.endswith("/workspace"):
            return _Response(json_data={"workspace_id": "workspace"})
        return _Response(content=b"downloaded")

    async def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append(("post", url, kwargs))
        if url.endswith("/ensure-user"):
            return _Response(json_data={"user_id": "user", "workspace_id": "workspace"})
        return _Response(json_data={"ok": True})


def test_saas_http_config_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        saas_http,
        "get_settings",
        lambda: SimpleNamespace(
            saas_base_url=" https://saas.test/ ",
            web_app_base_url="",
            magic_link_base_url="https://magic.test",
            bot_webhook_base_url="https://hook.test",
            saas_bot_secret="secret",
        ),
    )

    assert saas_http.saas_base_url() == "https://saas.test"
    assert saas_http.web_app_base_url() == "https://magic.test"
    assert saas_http.bot_headers() == {
        "Content-Type": "application/json",
        "X-Bot-Secret": "secret",
    }

    monkeypatch.setattr(saas_http, "get_settings", lambda: SimpleNamespace(saas_base_url=" "))
    with pytest.raises(RuntimeError):
        saas_http.saas_base_url()


def test_saas_http_endpoint_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        saas_http,
        "get_settings",
        lambda: SimpleNamespace(
            saas_base_url="https://saas.test",
            saas_bot_secret="secret",
        ),
    )
    monkeypatch.setattr(saas_http.httpx, "AsyncClient", _AsyncClient)

    assert asyncio.run(saas_http.ensure_user(42, "alice")) == ("user", "workspace")
    assert asyncio.run(saas_http.pending_channel("user")) == {"id": "channel"}
    assert asyncio.run(saas_http.has_telegram_channel("user")) is True
    assert asyncio.run(saas_http.user_workspace("user")) == "workspace"
    assert asyncio.run(
        saas_http.register_telegram_channel(
            telegram_user_id=42,
            telegram_username="alice",
            telegram_chat_id=-100,
            title="News",
            added_by_telegram_user_id=42,
        )
    ) == {"ok": True}
    assert asyncio.run(
        saas_http.complete_telegram_web_link(
            session_token="token",
            telegram_user_id=42,
            telegram_username="alice",
        )
    ) == {"ok": True}


def test_media_factory_selects_configured_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    media_factory.get_media_storage_provider.cache_clear()
    monkeypatch.setattr(media_factory, "get_settings", lambda: SimpleNamespace(media_storage_type="local"))
    assert isinstance(media_factory.get_media_storage_provider(), media_local.LocalStorageProvider)

    media_factory.get_media_storage_provider.cache_clear()
    monkeypatch.setattr(media_factory, "get_settings", lambda: SimpleNamespace(media_storage_type="s3"))
    assert isinstance(media_factory.get_media_storage_provider(), media_s3.S3StorageProvider)

    media_factory.get_media_storage_provider.cache_clear()
    monkeypatch.setattr(media_factory, "get_settings", lambda: SimpleNamespace(media_storage_type=""))
    assert media_factory.get_media_storage_provider() is None


def test_local_storage_uploads_and_deletes(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        media_local,
        "get_settings",
        lambda: SimpleNamespace(media_storage_path=str(tmp_path), media_base_url="https://cdn.test/media"),
    )
    provider = media_local.LocalStorageProvider()

    url = asyncio.run(provider.upload_from_bytes(b"data", "folder/file name.txt"))

    assert url == "https://cdn.test/media/folder/file%20name.txt"
    assert (tmp_path / "folder/file name.txt").read_bytes() == b"data"

    asyncio.run(provider.delete_object("folder/file name.txt"))
    assert not (tmp_path / "folder/file name.txt").exists()


def test_s3_storage_upload_and_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        media_s3,
        "get_settings",
        lambda: SimpleNamespace(
            s3_bucket="bucket",
            s3_public_base_url="https://cdn.test/",
        ),
    )
    calls: list[tuple[str, dict]] = []

    class Client:
        def put_object(self, **kwargs: object) -> None:
            calls.append(("put", kwargs))

        def delete_object(self, **kwargs: object) -> None:
            calls.append(("delete", kwargs))

    provider = media_s3.S3StorageProvider()
    monkeypatch.setattr(provider, "_get_client", lambda: Client())

    assert asyncio.run(provider.upload_from_bytes(b"data", "folder/file.txt")) == "https://cdn.test/folder/file.txt"
    asyncio.run(provider.delete_object("folder/file.txt"))

    assert calls == [
        ("put", {"Bucket": "bucket", "Key": "folder/file.txt", "Body": b"data"}),
        ("delete", {"Bucket": "bucket", "Key": "folder/file.txt"}),
    ]


def test_s3_storage_requires_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media_s3, "get_settings", lambda: SimpleNamespace(s3_bucket=""))
    with pytest.raises(ValueError):
        asyncio.run(media_s3.S3StorageProvider().upload_from_bytes(b"data", "key"))
