"""Tests for core_db local media storage helper."""

import asyncio

import pytest

from postbridge.botkit.local_storage import CoreDbLocalStorageProvider, get_local_storage


def test_get_local_storage_returns_none_when_no_config(monkeypatch: pytest.MonkeyPatch):
    """get_local_storage() returns None in SaaS mode without an explicit media base."""
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "saas")
    monkeypatch.delenv("MEDIA_BASE_URL", raising=False)
    monkeypatch.delenv("MEDIA_STORAGE_PATH", raising=False)
    monkeypatch.delenv("CORE_BASE_URL", raising=False)

    assert get_local_storage() is None


def test_get_local_storage_returns_provider_when_media_base_url(monkeypatch: pytest.MonkeyPatch):
    """get_local_storage() returns provider when MEDIA_BASE_URL is set."""
    monkeypatch.delenv("MEDIA_STORAGE_PATH", raising=False)
    monkeypatch.delenv("CORE_BASE_URL", raising=False)
    monkeypatch.setenv("MEDIA_BASE_URL", "https://cdn.example/media")

    provider = get_local_storage()
    assert provider is not None
    assert provider.base_url == "https://cdn.example/media"


def test_get_local_storage_returns_provider_when_media_storage_path_and_core_base_url(
    monkeypatch: pytest.MonkeyPatch,
):
    """get_local_storage() returns provider when MEDIA_STORAGE_PATH and CORE_BASE_URL are set."""
    monkeypatch.delenv("MEDIA_BASE_URL", raising=False)
    monkeypatch.setenv("MEDIA_STORAGE_PATH", "/var/media")
    monkeypatch.setenv("CORE_BASE_URL", "http://localhost:8000")

    provider = get_local_storage()
    assert provider is not None
    assert provider.base_url == "http://localhost:8000/media"


def test_local_storage_upload_from_bytes(tmp_path):
    """CoreDbLocalStorageProvider.upload_from_bytes saves file and returns URL with key."""
    provider = CoreDbLocalStorageProvider(
        storage_path=str(tmp_path),
        base_url="http://localhost:8000/media",
    )

    url = asyncio.run(provider.upload_from_bytes(b"data", "a/b.txt"))
    assert url == "http://localhost:8000/media/a/b.txt"
    assert (provider.storage_path / "a" / "b.txt").read_bytes() == b"data"
