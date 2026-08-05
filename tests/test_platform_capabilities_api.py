"""Снимок PlatformCapabilities из реестра и internal GET."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from postbridge.db import Base, ENGINE, init_db  # noqa: E402
from postbridge.integrations.registry import platform_capabilities_public_map


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    yield


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        "postbridge.api.service_internal.process_publication_target_task.delay",
        MagicMock(),
    )
    monkeypatch.setattr(
        "postbridge.api.service_internal.process_batch_import_run_task.delay",
        MagicMock(),
    )
    from postbridge.api.main import app

    return TestClient(app)


def _headers(tenant_id: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer svc-test-secret",
        "X-Tenant-Id": tenant_id,
        "X-Correlation-Id": str(uuid4()),
    }


def test_platform_capabilities_public_map_covers_registry():
    m = platform_capabilities_public_map()
    assert "telegram" in m and "postbridge" in m
    assert m["telegram"]["supports_target"] is True
    assert m["postbridge"]["supports_target"] is False
    assert set(m["rss"].keys()) == {
        "supports_source",
        "supports_target",
        "live_sync_publish_supported",
        "live_sync_source_supported",
        "historical_migration_source_supported",
        "historical_migration_target_supported",
        "ai_adapt_supported",
        "fetch_credentials_required",
        "rule_post_text_limit",
    }
    assert m["rss"]["rule_post_text_limit"] is None
    assert m["max"]["rule_post_text_limit"] == 4000
    assert m["zen"]["historical_migration_source_supported"] is True
    assert m["zen"]["historical_migration_target_supported"] is False
    assert m["postbridge"]["historical_migration_source_supported"] is True
    assert m["postbridge"]["historical_migration_target_supported"] is False
    assert m["linkedin"]["supports_source"] is False
    assert m["linkedin"]["supports_target"] is True
    assert m["linkedin"]["live_sync_publish_supported"] is True
    assert m["linkedin"]["rule_post_text_limit"] == 3000
    for platform, limit in {
        "facebook": 63206,
        "instagram": 2200,
        "x": 280,
        "bluesky": 300,
        "mastodon": 500,
    }.items():
        assert m[platform]["supports_source"] is False
        assert m[platform]["supports_target"] is True
        assert m[platform]["live_sync_publish_supported"] is True
        assert m[platform]["historical_migration_target_supported"] is True
        assert m[platform]["rule_post_text_limit"] == limit


def test_service_list_platform_capabilities_ok(client: TestClient):
    tid = str(uuid4())
    r = client.get(
        "/internal/service/platforms/capabilities",
        headers=_headers(tid),
    )
    assert r.status_code == 200
    body = r.json()
    assert "platforms" in body
    assert body["platforms"]["zen"]["ai_adapt_supported"] is True
    assert body["platforms"]["telegram"]["rule_post_text_limit"] == 4096
    assert body["platforms"]["linkedin"]["supports_target"] is True
    assert body["platforms"]["facebook"]["supports_target"] is True
    assert body["platforms"]["x"]["rule_post_text_limit"] == 280
