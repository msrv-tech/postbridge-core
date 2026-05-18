"""Browser-safe app API."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from postbridge.api.main import app
from postbridge.db import Base, BatchImportRunOrm, ENGINE, SESSION_LOCAL, init_db
from postbridge.infrastructure.crypto.credentials import decrypt_credential_secret
from postbridge.models.domain import (
    AgentRunOrm,
    BridgeOrm,
    ChannelOrm,
    ChannelCredentialOrm,
    ContentItemAiChatMessageOrm,
    ContentItemOrm,
    ContentCandidateOrm,
    MediaGenerationJobOrm,
    PublicationPlanOrm,
    PublicationTargetOrm,
    RenderVariantOrm,
    ReviewQueueItemOrm,
    TenantOrm,
)


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    yield


def test_app_runtime_config_defaults_to_selfhost(monkeypatch):
    monkeypatch.delenv("POSTBRIDGE_APP_MODE", raising=False)
    monkeypatch.delenv("POSTBRIDGE_DEFAULT_LOCALE", raising=False)
    client = TestClient(app)

    response = client.get("/api/app/runtime-config")

    assert response.status_code == 200
    body = response.json()
    assert body["app_mode"] == "selfhost"
    assert body["api"]["base_path"] == "/api/app"
    assert body["i18n"] == {"default_locale": "en", "locale_locked": False}
    assert body["features"]["local_auth"]["enabled"] is True
    assert body["features"]["billing"]["enabled"] is False
    assert body["features"]["workspaces"]["enabled"] is False
    assert body["features"]["managed_credentials"] == {"enabled": True, "mode": "core"}
    assert body["capabilities"]["managedCredentials"] == {"enabled": True, "mode": "core"}
    assert body["features"]["agent"]["enabled"] is True
    assert "core_service_token" not in str(body).lower()


def test_web_serves_selfhost_app_shell(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    client = TestClient(app)

    response = client.get("/web")
    trailing = client.get("/web/")

    assert response.status_code == 200
    assert trailing.status_code == 200
    assert "Postbridge" in response.text
    assert 'id="root"' in response.text
    assert "/web/assets/" in response.text
    assert "The web app lives in the SaaS repository" not in response.text
    assert "CORE_SERVICE_TOKEN" not in response.text

    asset_path = response.text.split('/web/assets/', 1)[1].split('"', 1)[0]
    asset = client.get(f"/web/assets/{asset_path}")
    assert asset.status_code == 200


def test_root_redirects_to_web_in_selfhost_mode(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    client = TestClient(app, follow_redirects=False)

    response = client.get("/")

    assert response.status_code == 307
    assert response.headers["location"] == "/web"


def test_app_runtime_config_saas_mode(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "saas")
    monkeypatch.setenv("POSTBRIDGE_DEFAULT_LOCALE", "ru")
    client = TestClient(app)

    response = client.get("/api/app/runtime-config")

    assert response.status_code == 200
    body = response.json()
    assert body["app_mode"] == "saas"
    assert body["i18n"] == {"default_locale": "ru", "locale_locked": True}
    assert body["features"]["local_auth"]["enabled"] is False
    assert body["features"]["billing"]["enabled"] is True
    assert body["features"]["workspaces"]["enabled"] is True
    assert body["features"]["managed_credentials"] == {"enabled": True, "mode": "bff"}


def test_app_session_reports_unbootstrapped_selfhost(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    client = TestClient(app)

    response = client.get("/api/app/session")

    assert response.status_code == 200
    assert response.json() == {
        "app_mode": "selfhost",
        "bootstrapped": False,
        "authenticated": False,
        "user": None,
        "tenant": None,
    }


def test_app_bootstrap_creates_selfhost_tenant(monkeypatch):
    tenant_id = "10000000-0000-4000-8000-000000000001"
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", tenant_id)
    client = TestClient(app)

    response = client.post("/api/app/bootstrap", json={"tenant_name": "Acme Local"})

    assert response.status_code == 200
    body = response.json()
    assert body["bootstrapped"] is True
    assert body["authenticated"] is True
    assert body["user"] == {
        "id": "local-admin",
        "display_name": "Local Admin",
        "role": "admin",
    }
    assert body["tenant"]["id"] == tenant_id
    assert body["tenant"]["name"] == "Acme Local"

    session = SESSION_LOCAL()
    try:
        row = session.get(TenantOrm, tenant_id)
        assert row is not None
        assert row.name == "Acme Local"
    finally:
        session.close()


def test_app_bootstrap_is_idempotent(monkeypatch):
    tenant_id = "10000000-0000-4000-8000-000000000002"
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", tenant_id)
    client = TestClient(app)

    first = client.post("/api/app/bootstrap", json={"tenant_name": "First"})
    second = client.post("/api/app/bootstrap", json={"tenant_name": "Second"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["tenant"]["id"] == tenant_id
    assert second.json()["tenant"]["name"] == "First"


def test_app_session_uses_existing_single_tenant(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000003")
    session = SESSION_LOCAL()
    try:
        session.add(TenantOrm(id="10000000-0000-4000-8000-000000000004", name="Existing"))
        session.commit()
    finally:
        session.close()
    client = TestClient(app)

    response = client.get("/api/app/session")

    assert response.status_code == 200
    body = response.json()
    assert body["bootstrapped"] is True
    assert body["tenant"]["name"] == "Existing"


def test_app_session_saas_mode_does_not_bootstrap(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "saas")
    client = TestClient(app)

    response = client.post("/api/app/bootstrap", json={"tenant_name": "Ignored"})

    assert response.status_code == 200
    assert response.json() == {
        "app_mode": "saas",
        "bootstrapped": False,
        "authenticated": False,
        "user": None,
        "tenant": None,
    }
    session = SESSION_LOCAL()
    try:
        assert session.query(TenantOrm).count() == 0
    finally:
        session.close()


def test_app_channels_require_bootstrap(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    client = TestClient(app)

    response = client.get("/api/app/channels")

    assert response.status_code == 409


def test_app_channels_create_list_get_delete(monkeypatch):
    tenant_id = "10000000-0000-4000-8000-000000000005"
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", tenant_id)
    client = TestClient(app)
    bootstrap = client.post("/api/app/bootstrap", json={"tenant_name": "Local"})
    assert bootstrap.status_code == 200

    created = client.post(
        "/api/app/channels",
        json={
            "platform": "Telegram",
            "kind": "Source",
            "title": "Telegram Source",
            "external_id": "@source",
            "status": "Connected",
            "config": {"mode": "history"},
            "capabilities": {"max_length": 4096},
        },
    )

    assert created.status_code == 200
    channel = created.json()
    assert channel["tenant_id"] == tenant_id
    assert channel["platform"] == "telegram"
    assert channel["kind"] == "source"
    assert channel["status"] == "connected"
    assert channel["config"] == {"mode": "history"}
    assert channel["capabilities"] == {"max_length": 4096}
    assert "encrypted_secret" not in str(channel).lower()

    listed = client.get("/api/app/channels", params={"platform": "telegram", "kind": "source"})
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [channel["id"]]

    listed_upper = client.get(
        "/api/app/channels",
        params={"platform": "TELEGRAM", "kind": "SOURCE", "status": "CONNECTED"},
    )
    assert listed_upper.status_code == 200
    assert [item["id"] for item in listed_upper.json()["items"]] == [channel["id"]]

    fetched = client.get(f"/api/app/channels/{channel['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "Telegram Source"

    deleted = client.delete(f"/api/app/channels/{channel['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/app/channels/{channel['id']}").status_code == 404


def test_app_channels_reject_postbridge_target(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000041")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    response = client.post(
        "/api/app/channels",
        json={
            "platform": "postbridge",
            "kind": "destination",
            "title": "Postbridge Target",
            "external_id": "local",
            "can_write": True,
        },
    )

    assert response.status_code == 400
    assert response.json()["message"] == "postbridge channel cannot be a target"


def test_app_channels_saas_mode_is_not_served_by_core(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "saas")
    client = TestClient(app)

    response = client.get("/api/app/channels")

    assert response.status_code == 404


def test_app_channel_credential_upsert_metadata_and_delete(monkeypatch):
    tenant_id = "10000000-0000-4000-8000-000000000006"
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", tenant_id)
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    channel = client.post(
        "/api/app/channels",
        json={
            "platform": "max",
            "kind": "destination",
            "title": "MAX",
            "external_id": "chat-1",
        },
    ).json()

    empty = client.get(f"/api/app/channels/{channel['id']}/credential")
    assert empty.status_code == 200
    assert empty.json()["has_secret"] is False

    saved = client.put(
        f"/api/app/channels/{channel['id']}/credential",
        json={
            "auth_type": "api_key",
            "status": "active",
            "secret": {"base_url": "https://platform.example", "token": "secret-token"},
        },
    )

    assert saved.status_code == 200
    body = saved.json()
    assert body["channel_id"] == channel["id"]
    assert body["auth_type"] == "api_key"
    assert body["status"] == "active"
    assert body["has_secret"] is True
    assert "secret-token" not in str(body)

    session = SESSION_LOCAL()
    try:
        row = session.query(ChannelCredentialOrm).filter_by(channel_id=channel["id"]).one()
        assert row.encrypted_secret
        assert row.created_at is not None
        assert row.updated_at is not None
        assert "secret-token" not in row.encrypted_secret
        assert decrypt_credential_secret(row.encrypted_secret) == (
            '{"base_url": "https://platform.example", "token": "secret-token"}'
        )
        created_at = row.created_at
        updated_at = row.updated_at
    finally:
        session.close()

    updated = client.put(
        f"/api/app/channels/{channel['id']}/credential",
        json={
            "auth_type": "api_key",
            "status": "rotated",
            "secret": {"base_url": "https://platform.example", "token": "rotated-token"},
        },
    )
    assert updated.status_code == 200
    session = SESSION_LOCAL()
    try:
        row = session.query(ChannelCredentialOrm).filter_by(channel_id=channel["id"]).one()
        assert row.created_at == created_at
        assert row.updated_at >= updated_at
        assert row.status == "rotated"
    finally:
        session.close()

    fetched = client.get(f"/api/app/channels/{channel['id']}/credential")
    assert fetched.status_code == 200
    assert fetched.json()["has_secret"] is True
    assert "secret-token" not in str(fetched.json())

    deleted = client.delete(f"/api/app/channels/{channel['id']}/credential")
    assert deleted.status_code == 204
    assert client.get(f"/api/app/channels/{channel['id']}/credential").json()["has_secret"] is False


def test_app_channel_credential_upsert_without_secret_does_not_encrypt_none(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000039")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    channel = client.post(
        "/api/app/channels",
        json={
            "platform": "max",
            "kind": "destination",
            "title": "MAX",
            "external_id": "chat-1",
        },
    ).json()

    def fail_on_call(_secret):
        raise AssertionError("encrypt_credential_secret should not be called without a secret")

    monkeypatch.setattr("postbridge.api.app_public.encrypt_credential_secret", fail_on_call)

    saved = client.put(
        f"/api/app/channels/{channel['id']}/credential",
        json={"auth_type": "api_key", "status": "active"},
    )

    assert saved.status_code == 200
    assert saved.json()["has_secret"] is False
    session = SESSION_LOCAL()
    try:
        row = session.query(ChannelCredentialOrm).filter_by(channel_id=channel["id"]).one()
        assert row.encrypted_secret is None
    finally:
        session.close()


def test_app_channel_credential_requires_existing_channel(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    response = client.put(
        "/api/app/channels/missing/credential",
        json={"secret": {"token": "x"}},
    )

    assert response.status_code == 404


def test_app_channel_registry_validate_and_max_verification(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000036")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    validated = client.post(
        "/api/app/channel-registry/validate",
        json={"platform": "vk", "platform_channel_id": "vk.com/club123456789", "role": "source"},
    )
    requested = client.post(
        "/api/app/channel-registry/max/request-verification",
        json={"platform_channel_id": "https://max.ru/channel-1"},
    )
    verified = client.post(
        "/api/app/channel-registry/max/verify",
        json={"platform_channel_id": "https://max.ru/channel-1", "code": requested.json()["code"]},
    )

    assert validated.status_code == 200
    assert validated.json()["ok"] is True
    assert validated.json()["platform_channel_id"] == "-123456789"
    assert requested.status_code == 200
    assert requested.json()["code"].startswith("PB-")
    assert verified.status_code == 200
    assert verified.json()["ok"] is True


def test_app_managed_credentials_create_and_channel_upsert(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000037")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    vk = client.post(
        "/api/app/credentials/vk/community-token",
        json={"group_id": "vk.com/club123456789", "access_token": "vk-secret"},
    )
    li = client.post(
        "/api/app/credentials/linkedin/access-token",
        json={
            "author_id": "organization:42",
            "access_token": "li-secret",
            "display": "LinkedIn Org",
        },
    )

    assert vk.status_code == 200, vk.text
    assert li.status_code == 200, li.text
    assert vk.json()["platform_channel_id"] == "-123456789"
    assert li.json()["platform_channel_id"] == "urn:li:organization:42"

    vk_channel = client.post(
        "/api/app/channels",
        json={
            "platform": "vk",
            "platform_channel_id": vk.json()["platform_channel_id"],
            "title": "VK Community",
            "can_read": True,
            "can_write": True,
            "credentials_ref": vk.json()["id"],
        },
    )
    li_channel = client.post(
        "/api/app/channels",
        json={
            "platform": "linkedin",
            "platform_channel_id": li.json()["platform_channel_id"],
            "title": "LinkedIn Org",
            "can_read": False,
            "can_write": True,
            "credentials_ref": li.json()["id"],
        },
    )

    assert vk_channel.status_code == 200
    assert li_channel.status_code == 200
    assert vk_channel.json()["id"] == vk.json()["id"]
    assert vk_channel.json()["credentials_ref"] == vk.json()["id"]
    assert vk_channel.json()["kind"] == "both"
    assert li_channel.json()["id"] == li.json()["id"]
    assert li_channel.json()["can_write"] is True

    session = SESSION_LOCAL()
    try:
        vk_credential = session.query(ChannelCredentialOrm).filter_by(channel_id=vk.json()["id"]).one()
        li_credential = session.query(ChannelCredentialOrm).filter_by(channel_id=li.json()["id"]).one()
        assert vk_credential.auth_type == "vk_community_token"
        assert li_credential.auth_type == "linkedin_access_token"
        assert "vk-secret" not in vk_credential.encrypted_secret
        assert "li-secret" not in li_credential.encrypted_secret
        assert json.loads(decrypt_credential_secret(vk_credential.encrypted_secret))["access_token"] == "vk-secret"
        assert json.loads(decrypt_credential_secret(li_credential.encrypted_secret))["author_urn"] == "urn:li:organization:42"
    finally:
        session.close()


def test_app_channels_expose_live_sync_source_capability(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000039")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    postbridge_channel = client.post(
        "/api/app/channels",
        json={
            "platform": "postbridge",
            "kind": "source",
            "title": "Postbridge Source",
            "external_id": "postbridge-local",
        },
    )
    max_channel = client.post(
        "/api/app/channels",
        json={
            "platform": "max",
            "kind": "destination",
            "title": "MAX Target",
            "external_id": "max-1",
        },
    )

    assert postbridge_channel.status_code == 200, postbridge_channel.text
    assert max_channel.status_code == 200, max_channel.text
    assert postbridge_channel.json()["live_sync_source_supported"] is True
    assert max_channel.json()["live_sync_source_supported"] is False

    listed = client.get("/api/app/channels")

    assert listed.status_code == 200
    items = {item["platform"]: item for item in listed.json()["items"]}
    assert items["postbridge"]["live_sync_source_supported"] is True
    assert items["max"]["live_sync_source_supported"] is False


def test_app_selfhost_disabled_saas_surfaces_are_explicit_core_endpoints(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000038")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    providers = client.get("/api/app/auth/providers")
    magic_link = client.post("/api/app/auth/magic-link/request", json={"email": "user@example.com"})
    telegram_web = client.post("/api/app/auth/telegram-web/start")
    billing_plans = client.get("/api/app/billing/plans")
    billing_action = client.post("/api/app/billing/subscription/create", json={})
    billing_email = client.post("/api/app/billing-email/request", json={"email": "user@example.com"})
    news = client.get("/api/app/news")
    news_detail = client.get("/api/app/news/product-update")
    previews = client.post("/api/app/platform-previews", json={"content": "hello"})

    assert providers.status_code == 200
    assert providers.json()["providers"] == []
    assert magic_link.status_code == 200
    assert magic_link.json()["disabled"] is True
    assert telegram_web.status_code == 200
    assert telegram_web.json()["status"] == "disabled"
    assert billing_plans.status_code == 200
    assert billing_plans.json()["items"] == []
    assert billing_action.status_code == 200
    assert billing_action.json()["disabled"] is True
    assert billing_action.json()["payment_url"] is None
    assert billing_email.status_code == 200
    assert billing_email.json()["ok"] is True
    assert news.status_code == 200
    assert news.json()["items"] == []
    assert news_detail.status_code == 200
    assert news_detail.json()["slug"] == "product-update"
    assert previews.status_code == 200
    assert previews.json()["items"] == []


def test_app_selfhost_agent_policy_and_embeddings_disabled_contracts(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000039")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    policy = client.get("/api/app/agent/workspace-policy")
    saved_policy = client.put("/api/app/agent/workspace-policy", json={"editor_instructions": "Prefer concise"})
    lifecycle = client.get("/api/app/agent/embeddings/lifecycle")
    reindex = client.post("/api/app/agent/reindex/channel/channel-1")
    maintenance = client.post("/api/app/agent/embeddings/maintenance")
    compact = client.post("/api/app/agent/embeddings/compact")
    cleanup = client.post("/api/app/agent/cleanup")

    assert policy.status_code == 200
    assert policy.json()["preferred_domains"] == []
    assert saved_policy.status_code == 200
    assert saved_policy.json()["blocked_url_patterns"] == []
    assert lifecycle.status_code == 200
    assert lifecycle.json()["backend"] == "core"
    assert lifecycle.json()["totals"]["materials"] == 0
    assert reindex.status_code == 200
    assert reindex.json()["status"] == "skipped"
    assert maintenance.status_code == 200
    assert compact.status_code == 200
    assert cleanup.status_code == 200


def _bootstrap_with_channels(client: TestClient) -> tuple[dict, dict]:
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    source = client.post(
        "/api/app/channels",
        json={
            "platform": "telegram",
            "kind": "source",
            "title": "Telegram Source",
            "external_id": "-1001",
        },
    ).json()
    target = client.post(
        "/api/app/channels",
        json={
            "platform": "max",
            "kind": "destination",
            "title": "MAX Target",
            "external_id": "max-1",
        },
    ).json()
    return source, target


def test_app_bridges_create_list_patch_get_delete(monkeypatch):
    tenant_id = "10000000-0000-4000-8000-000000000007"
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", tenant_id)
    client = TestClient(app)
    source, target = _bootstrap_with_channels(client)

    created = client.post(
        "/api/app/bridges",
        json={
            "source_channel_id": source["id"],
            "target_channel_id": target["id"],
            "mode": "live_sync",
            "status": "active",
            "settings": {"copy_media": True},
        },
    )

    assert created.status_code == 200
    bridge = created.json()
    assert bridge["tenant_id"] == tenant_id
    assert bridge["owner_user_id"] == "local-admin"
    assert bridge["source_channel_id"] == source["id"]
    assert bridge["target_channel_id"] == target["id"]
    assert bridge["mode"] == "live_sync"
    assert bridge["status"] == "active"
    assert bridge["settings"] == {"copy_media": True}
    assert str(UUID(bridge["id"])) == bridge["id"]
    session = SESSION_LOCAL()
    try:
        row = session.get(BridgeOrm, bridge["id"])
        assert row is not None
        assert row.settings_json == {"copy_media": True}
    finally:
        session.close()

    listed = client.get("/api/app/bridges", params={"mode": "live_sync", "status": "active"})
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [bridge["id"]]

    fetched = client.get(f"/api/app/bridges/{bridge['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == bridge["id"]

    patched = client.patch(
        f"/api/app/bridges/{bridge['id']}",
        json={"status": "paused", "settings": {"copy_media": False}},
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "paused"
    assert patched.json()["settings"] == {"copy_media": False}

    deleted = client.delete(f"/api/app/bridges/{bridge['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/app/bridges/{bridge['id']}").status_code == 404


def test_app_bridges_duplicate_returns_conflict(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000008")
    client = TestClient(app)
    source, target = _bootstrap_with_channels(client)
    payload = {
        "source_channel_id": source["id"],
        "target_channel_id": target["id"],
        "mode": "live_sync",
    }

    first = client.post("/api/app/bridges", json=payload)
    second = client.post("/api/app/bridges", json=payload)

    assert first.status_code == 200
    assert second.status_code == 409


def test_app_bridges_reject_postbridge_target(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000042")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    source = client.post(
        "/api/app/channels",
        json={
            "platform": "telegram",
            "kind": "source",
            "title": "Telegram Source",
            "external_id": "-1001",
        },
    ).json()
    session = SESSION_LOCAL()
    try:
        target = ChannelOrm(
            id="20000000-0000-4000-8000-000000000042",
            tenant_id="10000000-0000-4000-8000-000000000042",
            platform="postbridge",
            kind="source",
            title="Postbridge Source",
            external_id="local",
            status="connected",
        )
        session.add(target)
        session.commit()
    finally:
        session.close()

    response = client.post(
        "/api/app/bridges",
        json={
            "source_channel_id": source["id"],
            "target_channel_id": "20000000-0000-4000-8000-000000000042",
            "mode": "live_sync",
        },
    )

    assert response.status_code == 400
    assert response.json()["message"] == "postbridge channel cannot be a bridge target"


def test_app_connection_wizard_rejects_postbridge_target(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000043")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    source = client.post(
        "/api/app/channels",
        json={
            "platform": "telegram",
            "kind": "source",
            "title": "Telegram Source",
            "external_id": "-1001",
        },
    ).json()

    response = client.post(
        "/api/app/connections/create",
        json={
            "source_platform": "telegram",
            "source_channel_id": source["id"],
            "target_platform": "postbridge",
            "target_channel_id": "local",
        },
    )

    assert response.status_code == 400
    assert response.json()["message"] == "postbridge channel cannot be a bridge target"


def test_app_bridges_non_duplicate_integrity_error_is_not_reported_as_duplicate(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000038")
    client = TestClient(app)
    source, target = _bootstrap_with_channels(client)
    first = client.post(
        "/api/app/bridges",
        json={
            "source_channel_id": source["id"],
            "target_channel_id": target["id"],
            "mode": "live_sync",
        },
    )
    assert first.status_code == 200

    other_target = client.post(
        "/api/app/channels",
        json={
            "platform": "max",
            "kind": "destination",
            "title": "MAX Other Target",
            "external_id": "max-other",
        },
    ).json()
    monkeypatch.setattr("postbridge.api.app_public.uuid4", lambda: first.json()["id"])

    response = client.post(
        "/api/app/bridges",
        json={
            "source_channel_id": source["id"],
            "target_channel_id": other_target["id"],
            "mode": "live_sync",
        },
    )

    assert response.status_code == 500
    assert "bridge already exists" not in response.text


def test_app_bridges_require_existing_channels(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000009")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    response = client.post(
        "/api/app/bridges",
        json={
            "source_channel_id": "10000000-0000-4000-8000-000000000010",
            "target_channel_id": "10000000-0000-4000-8000-000000000011",
        },
    )

    assert response.status_code == 404


def test_app_bridges_live_sync_targets(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000012")
    client = TestClient(app)
    source, target = _bootstrap_with_channels(client)
    bridge = client.post(
        "/api/app/bridges",
        json={
            "source_channel_id": source["id"],
            "target_channel_id": target["id"],
            "mode": "live_sync",
            "status": "active",
            "settings": {"workspace_id": "selfhost"},
        },
    ).json()

    response = client.get(
        "/api/app/bridges/live-sync-targets",
        params={"source_channel_id": source["id"]},
    )

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "bridge_id": bridge["id"],
            "target_channel_id": target["id"],
            "platform": "max",
            "external_id": "max-1",
            "bridge_settings": {"workspace_id": "selfhost"},
        }
    ]


def test_app_connections_create_bridge_from_wizard_payload(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000033")
    client = TestClient(app)
    source, target = _bootstrap_with_channels(client)

    response = client.post(
        "/api/app/connections/create",
        json={
            "source_platform": "telegram",
            "source_channel_id": source["platform_channel_id"],
            "source_display": source["title"],
            "target_platform": "max",
            "target_channel_id": target["platform_channel_id"],
            "target_display": target["title"],
            "requested_limit": 0,
        },
    )
    duplicate = client.post(
        "/api/app/connections/create",
        json={
            "source_platform": "telegram",
            "source_channel_id": source["platform_channel_id"],
            "target_platform": "max",
            "target_channel_id": target["platform_channel_id"],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["status"] == "created"
    assert body["bridge"]["source_channel_id"] == source["id"]
    assert body["bridge"]["target_channel_id"] == target["id"]
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "exists"


def test_app_batch_import_jobs_start_get_retry_cancel_delete(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000034")
    enqueued: list[tuple[str, str]] = []

    def fake_delay(run_id: str, correlation_id: str):
        enqueued.append((run_id, correlation_id))

    monkeypatch.setattr("postbridge.api.app_public.process_batch_import_run_task.delay", fake_delay)
    client = TestClient(app)
    source, target = _bootstrap_with_channels(client)
    bridge = client.post(
        "/api/app/bridges",
        json={
            "source_channel_id": source["id"],
            "target_channel_id": target["id"],
            "mode": "live_sync",
        },
    ).json()

    created = client.post("/api/app/jobs/start", json={"bridge_id": bridge["id"], "requested_limit": 7})

    assert created.status_code == 200, created.text
    job = created.json()
    assert job["status"] == "pending"
    assert job["requested_limit"] == 7
    assert job["source_core_channel_id"] == source["id"]
    assert job["target_core_channel_id"] == target["id"]
    assert enqueued == [(job["id"], job["correlation_id"])]

    fetched = client.get(f"/api/app/jobs/{job['id']}")
    listed = client.get("/api/app/dashboard/jobs")
    assert fetched.status_code == 200
    assert fetched.json()["fetched_posts_count"] == 0
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == job["id"]

    paused = client.post(f"/api/app/jobs/{job['id']}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    session = SESSION_LOCAL()
    try:
        row = session.get(BatchImportRunOrm, job["id"])
        assert row is not None
        row.status = "failed"
        row.error_code = "REMOTE_TEMPORARY_ERROR"
        row.error_message = "Retry me"
        row.error_source = "source"
        row.error_retryable = True
        row.updated_at = datetime.now(UTC)
        session.commit()
    finally:
        session.close()

    retried = client.post(f"/api/app/jobs/{job['id']}/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] == "pending"
    assert len(enqueued) == 2

    cancelled = client.post(f"/api/app/jobs/{job['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "failed"
    assert cancelled.json()["error_payload"]["code"] == "VALIDATION_JOB_CANCELLED"

    deleted = client.delete(f"/api/app/jobs/{job['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/app/jobs/{job['id']}").status_code == 404


def test_app_bridges_saas_mode_is_not_served_by_core(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "saas")
    client = TestClient(app)

    response = client.get("/api/app/bridges")

    assert response.status_code == 404


def test_app_dashboard_summary_and_jobs_use_core_data(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000030")
    monkeypatch.setattr("postbridge.api.app_public.process_batch_import_run_task.delay", lambda *_args: None)
    client = TestClient(app)
    source, target = _bootstrap_with_channels(client)
    bridge = client.post(
        "/api/app/bridges",
        json={
            "source_channel_id": source["id"],
            "target_channel_id": target["id"],
            "mode": "live_sync",
        },
    ).json()
    item = client.post(
        "/api/app/content-items",
        json={"title": "Ready", "content_md": "Ready to publish", "status": "draft"},
    ).json()
    client.post(
        f"/api/app/content-items/{item['id']}/publication-targets",
        json={"channel_ids": [target["id"]], "dispatch": False},
    )
    job = client.post("/api/app/jobs/start", json={"bridge_id": bridge["id"], "requested_limit": 3}).json()

    summary = client.get("/api/app/dashboard/summary")
    jobs = client.get("/api/app/dashboard/jobs")

    assert summary.status_code == 200
    assert summary.json()["billing"]["plan_code"] == "selfhost"
    assert summary.json()["channels_count"] == 2
    assert summary.json()["bridges_count"] == 1
    assert summary.json()["content_items_count"] == 1
    assert summary.json()["pending_publication_targets_count"] == 1
    assert jobs.status_code == 200
    assert jobs.json()["items"][0]["id"] == job["id"]
    assert jobs.json()["items"][0]["requested_limit"] == 3


def test_app_workspace_settings_get_and_update(monkeypatch):
    tenant_id = "10000000-0000-4000-8000-000000000031"
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", tenant_id)
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    initial = client.get("/api/app/settings")
    updated = client.put("/api/app/settings", json={"image_style_prompt": "Clean editorial illustration"})

    assert initial.status_code == 200
    assert initial.json()["image_style_prompt"] == ""
    assert initial.json()["billing"]["enabled"] is False
    assert updated.status_code == 200
    assert updated.json()["tenant_id"] == tenant_id
    assert updated.json()["image_style_prompt"] == "Clean editorial illustration"

    session = SESSION_LOCAL()
    try:
        tenant = session.get(TenantOrm, tenant_id)
        assert tenant is not None
        assert tenant.image_style_prompt == "Clean editorial illustration"
    finally:
        session.close()


def test_app_content_items_create_list_patch_get_delete(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000013")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    created = client.post(
        "/api/app/content-items",
        json={
            "title": "Draft title",
            "content_md": "Hello **world**",
            "content_plain": "Hello world",
            "summary": "Short",
            "tags": ["news", "local"],
            "status": "draft",
        },
    )

    assert created.status_code == 200
    item = created.json()
    assert item["title"] == "Draft title"
    assert item["content_md"] == "Hello **world**"
    assert item["content_plain"] == "Hello world"
    assert item["summary"] == "Short"
    assert item["tags"] == ["news", "local"]
    assert item["status"] == "draft"

    listed = client.get("/api/app/content-items", params={"status": "draft"})
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["items"]] == [item["id"]]

    fetched = client.get(f"/api/app/content-items/{item['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == item["id"]

    session = SESSION_LOCAL()
    try:
        row = session.get(ContentItemOrm, item["id"])
        assert row is not None
        row.body_structured_json = json.dumps({"postbridge_extra": {"saas_workspace_id": "workspace-keep"}})
        session.commit()
    finally:
        session.close()

    patched = client.patch(
        f"/api/app/content-items/{item['id']}",
        json={"title": "Updated", "content_md": "Updated body", "status": "published"},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Updated"
    assert patched.json()["content_md"] == "Updated body"
    assert patched.json()["status"] == "published"
    assert patched.json()["published_at"] is not None
    assert patched.json()["saas_workspace_id"] == "workspace-keep"

    deleted = client.delete(f"/api/app/content-items/{item['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/app/content-items/{item['id']}").status_code == 404


def test_app_content_items_published_requires_content(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000014")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    response = client.post("/api/app/content-items", json={"status": "published", "content_md": ""})

    assert response.status_code == 422


def test_app_content_items_schedule_with_postbridge_source(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000015")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    source = client.post(
        "/api/app/channels",
        json={
            "platform": "postbridge",
            "kind": "source",
            "title": "Postbridge Source",
            "external_id": "postbridge-local",
        },
    ).json()
    future = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(minutes=10)
    minute_adjust = future.minute % 5
    if minute_adjust:
        future += timedelta(minutes=5 - minute_adjust)

    response = client.post(
        "/api/app/content-items",
        json={
            "content_md": "Scheduled body",
            "status": "draft",
            "scheduled_publish_at": future.isoformat(),
            "live_sync_source_core_channel_id": source["id"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["scheduled_publish_at"] is not None
    assert body["live_sync_source_core_channel_id"] == source["id"]


def test_app_content_items_saas_mode_is_not_served_by_core(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "saas")
    client = TestClient(app)

    response = client.get("/api/app/content-items")

    assert response.status_code == 404


def test_app_publication_targets_create_list_get_and_dispatch(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000016")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    target = client.post(
        "/api/app/channels",
        json={
            "platform": "max",
            "kind": "destination",
            "title": "MAX Target",
            "external_id": "max-1",
        },
    ).json()
    item = client.post(
        "/api/app/content-items",
        json={"title": "Ready", "content_md": "Ready to publish", "status": "draft"},
    ).json()

    created = client.post(
        f"/api/app/content-items/{item['id']}/publication-targets",
        json={"channel_ids": [target["id"]], "dispatch": False},
    )

    assert created.status_code == 200, created.text
    body = created.json()
    assert body["content_item_id"] == item["id"]
    assert len(body["publication_target_ids"]) == 1
    assert body["dispatched_target_ids"] == []

    session = SESSION_LOCAL()
    try:
        plan = session.get(PublicationPlanOrm, body["publication_plan_id"])
        pub_target = session.get(PublicationTargetOrm, body["publication_target_ids"][0])
        assert plan is not None
        assert plan.content_item_id == item["id"]
        assert plan.strategy == "immediate"
        assert plan.status == "draft"
        assert pub_target is not None
        assert pub_target.channel_id == target["id"]
        assert pub_target.status == "pending"
    finally:
        session.close()

    listed = client.get(f"/api/app/content-items/{item['id']}/publication-targets")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["channel_title"] == "MAX Target"
    assert listed.json()["items"][0]["content_item_id"] == item["id"]

    fetched = client.get(f"/api/app/publication-targets/{body['publication_target_ids'][0]}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "pending"

    all_targets = client.get("/api/app/publication-targets")
    assert all_targets.status_code == 200
    assert all_targets.json()["total"] == 1
    assert all_targets.json()["items"][0]["id"] == body["publication_target_ids"][0]

    dispatched: list[tuple[str, str]] = []

    def fake_delay(target_id: str, correlation_id: str):
        dispatched.append((target_id, correlation_id))

    monkeypatch.setattr("postbridge.api.app_public.process_publication_target_task.delay", fake_delay)
    dispatch = client.post(f"/api/app/publication-targets/{body['publication_target_ids'][0]}/dispatch")

    assert dispatch.status_code == 200
    assert dispatch.json() == {
        "status": "enqueued",
        "target_id": body["publication_target_ids"][0],
    }
    assert dispatched[0][0] == body["publication_target_ids"][0]
    assert dispatched[0][1]


def test_app_publication_targets_scheduled_plan_status(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000040")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    target = client.post(
        "/api/app/channels",
        json={
            "platform": "max",
            "kind": "destination",
            "title": "MAX Target",
            "external_id": "max-1",
        },
    ).json()
    item = client.post(
        "/api/app/content-items",
        json={"title": "Ready", "content_md": "Ready to publish", "status": "draft"},
    ).json()
    scheduled_at = (datetime.now(UTC) + timedelta(hours=2)).replace(second=0, microsecond=0).isoformat()

    created = client.post(
        f"/api/app/content-items/{item['id']}/publication-targets",
        json={"channel_ids": [target["id"]], "dispatch": False, "scheduled_at": scheduled_at},
    )

    assert created.status_code == 200, created.text
    session = SESSION_LOCAL()
    try:
        plan = session.get(PublicationPlanOrm, created.json()["publication_plan_id"])
        assert plan is not None
        assert plan.strategy == "scheduled"
        assert plan.status == "scheduled"
    finally:
        session.close()


def test_app_publication_targets_require_existing_channel(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000017")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    item = client.post(
        "/api/app/content-items",
        json={"title": "Ready", "content_md": "Ready to publish", "status": "draft"},
    ).json()

    response = client.post(
        f"/api/app/content-items/{item['id']}/publication-targets",
        json={"channel_ids": ["10000000-0000-4000-8000-000000000018"]},
    )

    assert response.status_code == 422


def test_app_publication_targets_saas_mode_is_not_served_by_core(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "saas")
    client = TestClient(app)

    response = client.get("/api/app/publication-targets/10000000-0000-4000-8000-000000000019")

    assert response.status_code == 404


def test_app_media_upload_local(monkeypatch, tmp_path):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000020")
    monkeypatch.setenv("MEDIA_STORAGE_TYPE", "local")
    monkeypatch.setenv("MEDIA_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("MEDIA_BASE_URL", "http://testserver/media")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    response = client.post(
        "/api/app/media/upload",
        files={"file": ("cover.png", BytesIO(b"abc"), "image/png")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["media_asset_id"]
    assert body["url"].startswith("http://testserver/media/")
    assert (tmp_path / f"tenants/10000000-0000-4000-8000-000000000020/media/{body['media_asset_id']}.png").is_file()


def test_app_media_generation_job_create_list_get(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000021")
    monkeypatch.setenv("AI_GATEWAY_ENABLED", "1")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    item = client.post(
        "/api/app/content-items",
        json={"title": "Image post", "content_md": "Generate an image for this post."},
    ).json()
    queued: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "postbridge.api.app_public.process_media_generation_job_task.delay",
        lambda job_id, correlation_id=None: queued.append((job_id, correlation_id)),
    )

    response = client.post(
        "/api/app/media/generation-jobs",
        json={
            "target": "media",
            "title": "Image post",
            "content_md": "Generate an image for this post.",
            "content_item_id": item["id"],
            "model": "image-test-model",
        },
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["requester_user_id"] == "local-admin"
    assert body["content_item_id"] == item["id"]
    assert queued[0][0] == body["id"]
    assert queued[0][1]

    session = SESSION_LOCAL()
    try:
        job = session.get(MediaGenerationJobOrm, body["id"])
        assert job is not None
        assert job.request_payload["model"] == "image-test-model"
    finally:
        session.close()

    listed = client.get("/api/app/media/generation-jobs")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == body["id"]

    fetched = client.get(f"/api/app/media/generation-jobs/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["target"] == "media"


def test_app_media_generation_job_records_queue_failure(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000022")
    monkeypatch.setenv("AI_GATEWAY_ENABLED", "1")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    def fail_delay(_job_id: str, _correlation_id: str | None = None) -> None:
        raise RuntimeError("queue offline")

    monkeypatch.setattr("postbridge.api.app_public.process_media_generation_job_task.delay", fail_delay)

    response = client.post(
        "/api/app/media/generation-jobs",
        json={"target": "cover", "title": "Image post"},
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "MEDIA_GENERATION_QUEUE_FAILED"


def test_app_media_generation_job_requires_ai_enabled(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000023")
    monkeypatch.setenv("AI_GATEWAY_ENABLED", "0")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    response = client.post(
        "/api/app/media/generation-jobs",
        json={"target": "cover", "title": "Image post"},
    )

    assert response.status_code == 422


def test_app_media_saas_mode_is_not_served_by_core(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "saas")
    client = TestClient(app)

    response = client.get("/api/app/media/generation-jobs")

    assert response.status_code == 404


def test_app_content_generate_creates_draft(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000024")
    monkeypatch.setenv("AI_GATEWAY_ENABLED", "1")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("AI_GATEWAY_BASE_URL", raising=False)
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    response = client.post("/api/app/content-items/generate", json={"prompt": "Write about bridges"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["operation"] == "generate"
    assert body["content_item_id"]
    assert body["publication_plan_id"] is None
    assert body["generated_title"]
    assert body["generated_body_markdown"]
    assert body["usage_tokens_charged"] == 1


def test_app_content_generate_refines_and_stores_ai_chat(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000025")
    monkeypatch.setenv("AI_GATEWAY_ENABLED", "1")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("AI_GATEWAY_BASE_URL", raising=False)
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    item = client.post(
        "/api/app/content-items",
        json={"title": "Draft", "content_md": "Long draft body"},
    ).json()

    response = client.post(
        "/api/app/content-items/generate",
        json={
            "messages": [{"role": "user", "content": "make it shorter"}],
            "content_item_id": item["id"],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["content_item_id"] == item["id"]
    assert "[generate-chat]" in body["generated_body_markdown"]

    chat = client.get(f"/api/app/content-items/{item['id']}/ai-chat")
    assert chat.status_code == 200
    assert len(chat.json()["messages"]) == 2

    session = SESSION_LOCAL()
    try:
        rows = (
            session.query(ContentItemAiChatMessageOrm)
            .filter(ContentItemAiChatMessageOrm.content_item_id == item["id"])
            .all()
        )
        assert len(rows) == 2
    finally:
        session.close()

    cleared = client.delete(f"/api/app/content-items/{item['id']}/ai-chat")
    assert cleared.status_code == 200
    assert cleared.json()["deleted"] == 2
    assert client.get(f"/api/app/content-items/{item['id']}/ai-chat").json()["messages"] == []


def test_app_content_adapt_and_translate(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000026")
    monkeypatch.setenv("AI_GATEWAY_ENABLED", "1")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("AI_GATEWAY_BASE_URL", raising=False)
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    channel = client.post(
        "/api/app/channels",
        json={
            "platform": "telegram",
            "kind": "destination",
            "title": "Telegram",
            "external_id": "@tg",
            "capabilities": {"max_length": 4096},
        },
    ).json()
    item = client.post(
        "/api/app/content-items",
        json={"title": "Draft", "content_md": "Hello world"},
    ).json()

    adapted = client.post(
        f"/api/app/content-items/{item['id']}/adapt",
        json={"channel_id": channel["id"]},
    )
    assert adapted.status_code == 200, adapted.text
    assert adapted.json()["operation"] == "adapt"

    translated = client.post(
        f"/api/app/content-items/{item['id']}/translate",
        json={"channel_id": channel["id"], "target_language": "de"},
    )
    assert translated.status_code == 200, translated.text
    assert translated.json()["operation"] == "translate"

    session = SESSION_LOCAL()
    try:
        adapt_rv = session.get(RenderVariantOrm, adapted.json()["render_variant_id"])
        translate_rv = session.get(RenderVariantOrm, translated.json()["render_variant_id"])
        assert adapt_rv is not None
        assert "[adapt:telegram]" in (adapt_rv.body_text or "")
        assert translate_rv is not None
        assert "[translate:de]" in (translate_rv.body_text or "")
    finally:
        session.close()


def test_app_content_ai_disabled_returns_422(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000027")
    monkeypatch.setenv("AI_GATEWAY_ENABLED", "0")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    response = client.post("/api/app/content-items/generate", json={"prompt": "Write"})

    assert response.status_code == 422


def test_app_content_ai_saas_mode_is_not_served_by_core(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "saas")
    client = TestClient(app)

    response = client.post("/api/app/content-items/generate", json={"prompt": "Write"})

    assert response.status_code == 404


def _patch_agent_provider(monkeypatch):
    def fake_invoke_json(self, *, messages, temperature=0.2):
        _ = self, temperature
        return (
            {
                "topic": "Manual topic",
                "headline": "Manual headline",
                "body_markdown": "Manual draft",
                "summary": "Manual summary",
                "why_now": "Timely",
                "style_fit_summary": "Good fit",
                "source_bundle": {"primary_sources": ["https://example.com/a"]},
                "scores": {"relevance": 0.95, "novelty": 0.9, "style_fit": 0.88},
                "risk_flags": [],
            },
            {"total_tokens": 42},
        )

    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        fake_invoke_json,
    )
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_embedding",
        lambda self, *, text: ([0.9, 0.1, 0.1, 0.1], {"total_tokens": 5}),
    )
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_rerank",
        lambda self, *, query, items, top_k: (
            [{"index": idx, "score": 1.0 - idx * 0.01, "reason": "fit"} for idx, _ in enumerate(items[:top_k])],
            {"total_tokens": 7},
        ),
    )
    monkeypatch.setattr("postbridge.agent.orchestrator.find_default_provider", lambda session, tenant_id: object())
    monkeypatch.setattr(
        "postbridge.agent.orchestrator.ensure_openai_compatible_provider",
        lambda row: __import__(
            "postbridge.agent.providers.openai_compatible",
            fromlist=["OpenAICompatibleProvider"],
        ).OpenAICompatibleProvider(
            base_url="https://example.invalid",
            model_name="gpt-test",
            api_key="secret",
        ),
    )


def test_app_agent_editor_message_runs_and_timeline(monkeypatch):
    _patch_agent_provider(monkeypatch)
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000028")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    channel = client.post(
        "/api/app/channels",
        json={"platform": "telegram", "kind": "destination", "title": "Telegram"},
    ).json()
    item = client.post(
        "/api/app/content-items",
        json={"title": "Draft", "content_md": "Draft body"},
    ).json()

    response = client.post(
        f"/api/app/agent/content-items/{item['id']}/messages",
        json={
            "channel_id": channel["id"],
            "user_request": "Make this draft clearer",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    run_id = payload["run"]["agent_run_id"]
    assert payload["run"]["id"] == run_id
    assert payload["timeline"]["content_item_id"] == item["id"]
    assert payload["timeline"]["latest_run"]["id"] == run_id
    assert any(
        event["role"] == "user" and "Make this draft clearer" in event["content"]
        for event in payload["timeline"]["events"]
    )

    runs = client.get("/api/app/agent/runs")
    assert runs.status_code == 200
    assert any(row["id"] == run_id for row in runs.json())

    run_detail = client.get(f"/api/app/agent/runs/{run_id}")
    assert run_detail.status_code == 200
    assert run_detail.json()["id"] == run_id

    steps = client.get(f"/api/app/agent/runs/{run_id}/steps")
    assert steps.status_code == 200
    assert any(row["step_name"] == "run_started" for row in steps.json())

    timeline = client.get(f"/api/app/agent/content-items/{item['id']}/timeline")
    assert timeline.status_code == 200
    assert timeline.json()["latest_run"]["id"] == run_id


def test_app_agent_tasks_policy_and_analytics(monkeypatch):
    _patch_agent_provider(monkeypatch)
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000033")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    channel = client.post(
        "/api/app/channels",
        json={"platform": "telegram", "kind": "destination", "title": "Telegram"},
    ).json()

    created = client.post(
        "/api/app/agent/tasks",
        json={
            "channel_id": channel["id"],
            "mode": "topic_scout",
            "goal_text": "Find timely engineering topics",
            "editorial_instructions": "Prefer practical examples",
            "max_candidates_per_run": 2,
            "autonomy_mode": "draft_approval",
            "task_config": {"priority": "normal"},
            "search_image_mode": "none",
            "seed_urls": [],
            "require_source_approval": False,
            "created_by": "local-admin",
        },
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["id"]
    assert created.json()["task_config"]["seed_urls"] == []

    listed = client.get("/api/app/agent/tasks")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [task_id]

    paused = client.post(f"/api/app/agent/tasks/{task_id}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = client.post(f"/api/app/agent/tasks/{task_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"

    run = client.post(f"/api/app/agent/tasks/{task_id}/run")
    assert run.status_code == 200, run.text
    assert run.json()["task_id"] == task_id
    assert run.json()["run"]["agent_run_id"]

    policy = client.put(
        "/api/app/agent/policies",
        json={"channel_id": channel["id"], "policy": {"autonomy_mode": "draft_approval"}},
    )
    assert policy.status_code == 200
    assert policy.json()["policy"]["autonomy_mode"] == "draft_approval"

    policies = client.get("/api/app/agent/policies", params={"channel_id": channel["id"]})
    assert policies.status_code == 200
    assert policies.json()["channel_id"] == channel["id"]

    overview = client.get("/api/app/agent/analytics/overview")
    assert overview.status_code == 200
    assert overview.json()["runs_total"] >= 1

    timeseries = client.get("/api/app/agent/analytics/timeseries", params={"days": 7})
    assert timeseries.status_code == 200
    assert "days" in timeseries.json()

    quality = client.get("/api/app/agent/analytics/quality")
    assert quality.status_code == 200
    assert "models" in quality.json()

    archived = client.delete(f"/api/app/agent/tasks/{task_id}")
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"


def test_app_review_queue_list_get_resolve(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000029")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    channel = client.post(
        "/api/app/channels",
        json={"platform": "telegram", "kind": "destination", "title": "Telegram"},
    ).json()
    run_id = "10000000-0000-4000-8000-000000000030"
    candidate_id = "10000000-0000-4000-8000-000000000031"
    review_id = "10000000-0000-4000-8000-000000000032"
    session = SESSION_LOCAL()
    try:
        session.add(
            AgentRunOrm(
                id=run_id,
                tenant_id="10000000-0000-4000-8000-000000000029",
                channel_id=channel["id"],
                graph_name="topic_scout",
                trigger_type="api",
                status="awaiting_review",
                model="gpt-test",
                provider_type="openai_compatible",
            )
        )
        session.add(
            ContentCandidateOrm(
                id=candidate_id,
                agent_run_id=run_id,
                tenant_id="10000000-0000-4000-8000-000000000029",
                channel_id=channel["id"],
                status="proposed",
                topic="Topic",
                headline="Headline",
                body_markdown="Draft",
                draft_json=json.dumps({"title": "Headline", "body_markdown": "Draft"}),
            )
        )
        session.add(
            ReviewQueueItemOrm(
                id=review_id,
                tenant_id="10000000-0000-4000-8000-000000000029",
                channel_id=channel["id"],
                agent_run_id=run_id,
                candidate_id=candidate_id,
                status="pending",
                review_payload_json=json.dumps({"kind": "candidate", "autonomy_mode": "draft_approval"}),
            )
        )
        session.commit()
    finally:
        session.close()

    listed = client.get("/api/app/review-queue", params={"status": "pending"})
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [review_id]

    fetched = client.get(f"/api/app/review-queue/{review_id}")
    assert fetched.status_code == 200
    assert fetched.json()["candidate_id"] == candidate_id

    resolved = client.post(
        f"/api/app/review-queue/{review_id}/resolve",
        json={"decision": "rejected", "review_action": "reject_low_quality", "reviewer_id": "local-admin"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "rejected"
    assert resolved.json()["decision"]["reviewer_id"] == "local-admin"


def test_app_agent_saas_mode_is_not_served_by_core(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "saas")
    client = TestClient(app)

    response = client.get("/api/app/agent/runs")

    assert response.status_code == 404
