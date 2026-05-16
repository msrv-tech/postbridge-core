from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from postbridge.api.main import app
from postbridge.i18n.service import get_i18n


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("POSTBRIDGE_DEFAULT_LOCALE", "ru")
    get_i18n.cache_clear()
    yield TestClient(app)
    get_i18n.cache_clear()


def test_media_not_found_uses_core_default_locale_ru(client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("postbridge.api.main._media_storage_dir", lambda: tmp_path)

    response = client.get("/media/nonexistent")

    assert response.status_code == 404
    payload = response.json()
    assert payload["message_key"] == "error.http.not_found"
    assert payload["message"] == "Не найдено."


def test_service_auth_error_uses_core_default_locale_ru(client: TestClient):
    response = client.post(
        "/internal/service/tenants/ensure",
        json={"name": "Workspace"},
        headers={
            "Authorization": "Bearer wrong",
            "X-Tenant-Id": str(uuid4()),
        },
    )

    assert response.status_code == 403
    payload = response.json()
    assert payload["code"] == "AUTH_UNAUTHORIZED"
    assert payload["message_key"] == "error.auth.invalid_or_missing_core_service_token"
    assert payload["message"] == "Некорректный или отсутствующий core service token."


def test_service_validation_error_uses_core_default_locale_ru(client: TestClient):
    response = client.post(
        "/internal/service/bridges",
        json={
            "saas_user_id": "saas-u1",
            "source_channel_id": str(uuid4()),
            "target_channel_id": str(uuid4()),
            "mode": "broken",
            "status": "active",
        },
        headers={
            "Authorization": "Bearer svc-test-secret",
            "X-Tenant-Id": str(uuid4()),
        },
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "VALIDATION_REQUEST_INVALID"
    assert payload["message_key"] == "error.validation.invalid_mode"
    assert payload["message"] == "Некорректный mode."


def test_live_sync_validation_error_uses_core_default_locale_ru(client: TestClient):
    response = client.get(
        "/internal/rss-feeds/meta/missing-feed",
        params={"secret_token": "missing-secret"},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "VALIDATION_RSS_FEED_NOT_FOUND"
    assert payload["message_key"] == "error.validation.rss_feed_not_found"
    assert payload["message"] == "RSS feed не найден."
