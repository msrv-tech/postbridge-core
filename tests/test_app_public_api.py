"""Browser-safe app API."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from postbridge.api.main import app
from postbridge.db import Base, BatchImportRunOrm, ENGINE, RssFeedItemOrm, SESSION_LOCAL, init_db
from postbridge.infrastructure.crypto.credentials import decrypt_credential_secret, encrypt_credential_secret
from postbridge.services.postbridge_workspace_content import content_item_to_api_dict
from postbridge.models.domain import (
    AgentRunOrm,
    BridgeOrm,
    ChannelOrm,
    ChannelCredentialOrm,
    ContentItemAiChatMessageOrm,
    ContentItemOrm,
    ContentCandidateOrm,
    InstallationSecretOrm,
    LlmProviderConfigOrm,
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
    monkeypatch.setenv("POSTBRIDGE_VERSION", "v0.1.2")
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
    assert body["version"]["current"] == "v0.1.2"
    assert body["version"]["release_repository"] == "msrv-tech/postbridge-core"
    assert "core_service_token" not in str(body).lower()


def test_app_version_check_returns_update_command(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_VERSION", "v0.1.2")
    monkeypatch.setenv("POSTBRIDGE_RELEASE_REPOSITORY", "https://github.com/msrv-tech/postbridge-core")
    monkeypatch.setenv("POSTBRIDGE_CONTAINER_IMAGE", "ghcr.io/example/postbridge-core")
    calls: list[str] = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "tag_name": "v0.1.3",
                "html_url": "https://github.com/msrv-tech/postbridge-core/releases/tag/v0.1.3",
            }

    def fake_get(url, *args, **kwargs):
        _ = args, kwargs
        calls.append(url)
        return FakeResponse()

    monkeypatch.setattr(app_public.httpx, "get", fake_get)
    client = TestClient(app)

    response = client.get("/api/app/version-check")

    assert response.status_code == 200
    body = response.json()
    assert body["current_version"] == "v0.1.2"
    assert body["latest_version"] == "v0.1.3"
    assert body["update_available"] is True
    assert "repository" not in body
    assert calls == ["https://api.github.com/repos/msrv-tech/postbridge-core/releases/latest"]
    assert "ghcr.io/example/postbridge-core:v0.1.3" in body["update_command"]
    assert "docker compose" in body["update_command"]


def test_app_version_check_hides_invalid_release_source(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_RELEASE_REPOSITORY", "not a release source")
    client = TestClient(app)

    response = client.get("/api/app/version-check")

    assert response.status_code == 200
    body = response.json()
    assert body["check_status"] == "release_source_unavailable"
    assert "repository" not in body


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
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert trailing.headers["x-robots-tag"] == "noindex, nofollow"

    asset_path = response.text.split('/web/assets/', 1)[1].split('"', 1)[0]
    asset = client.get(f"/web/assets/{asset_path}")
    assert asset.status_code == 200


def test_root_redirects_to_web_in_selfhost_mode(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    client = TestClient(app, follow_redirects=False)

    response = client.get("/")

    assert response.status_code == 307
    assert response.headers["location"] == "/web"


def test_hosted_frontend_returns_real_404_and_noindexes_private_routes(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "saas")
    client = TestClient(app)

    root = client.get("/")
    missing = client.get("/definitely-not-a-postbridge-route")
    login = client.get("/login")
    dashboard = client.get("/dashboard")
    workspace = client.get("/workspaces/ws-1/content")

    assert root.status_code == 200
    assert "__POSTBRIDGE_PUBLIC_BASE_URL__" not in root.text
    assert root.text.count('rel="canonical"') == 1
    assert root.text.count('name="twitter:card"') == 1
    assert missing.status_code == 404
    assert 'content="noindex, nofollow"' in missing.text
    assert missing.headers["x-robots-tag"] == "noindex, nofollow"
    assert login.status_code == 200
    assert login.headers["x-robots-tag"] == "noindex, nofollow"
    assert dashboard.status_code == 200
    assert dashboard.headers["x-robots-tag"] == "noindex, nofollow"
    assert workspace.status_code == 200
    assert workspace.headers["x-robots-tag"] == "noindex, nofollow"


def test_hosted_seo_open_graph_assets_are_1200_by_630(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "saas")
    client = TestClient(app)
    assets = (
        "home.png",
        "platforms.png",
        "cases.png",
        "mcp.png",
        "pricing.png",
        "platform-x.png",
        "platform-linkedin.png",
        "case-multi-platform-publishing.png",
        "case-chatgpt-social-publishing.png",
    )

    for filename in assets:
        response = client.get(f"/og/{filename}")
        assert response.status_code == 200, filename
        assert response.headers["content-type"] == "image/png"
        assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
        width = int.from_bytes(response.content[16:20], "big")
        height = int.from_bytes(response.content[20:24], "big")
        assert (width, height) == (1200, 630), filename


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


def test_app_installation_secrets_encrypt_and_hide_plaintext(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000044")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    response = client.put(
        "/api/app/installation-secrets/ai-gateway",
        json={
            "secret": {"api_key": "sk-local-test"},
            "config": {"base_url": "https://api.example.com/v1", "default_model": "gpt-test"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["category"] == "ai_gateway"
    assert body["configured"] is True
    assert body["config"] == {"base_url": "https://api.example.com/v1", "default_model": "gpt-test"}
    assert "sk-local-test" not in json.dumps(body)

    session = SESSION_LOCAL()
    try:
        row = session.scalar(
            select(InstallationSecretOrm).where(InstallationSecretOrm.category == "ai_gateway")
        )
        assert row is not None
        assert "sk-local-test" not in row.encrypted_secret
        assert json.loads(decrypt_credential_secret(row.encrypted_secret)) == {"api_key": "sk-local-test"}
    finally:
        session.close()

    listed = client.get("/api/app/installation-secrets")
    assert listed.status_code == 200
    assert "sk-local-test" not in json.dumps(listed.json())


def test_app_installation_secret_config_without_secret_stays_unconfigured(monkeypatch):
    from postbridge.api import app_public

    tenant_id = "10000000-0000-4000-8000-000000000049"
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", tenant_id)
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    response = client.put(
        "/api/app/installation-secrets/ai-gateway",
        json={"config": {"base_url": "https://api.example.com/v1"}},
    )

    assert response.status_code == 200
    assert response.json()["configured"] is False
    session = SESSION_LOCAL()
    try:
        row = session.scalar(
            select(InstallationSecretOrm).where(InstallationSecretOrm.category == "ai_gateway")
        )
        assert row is not None
        assert row.encrypted_secret is None
        assert app_public._installation_secret_payload(
            session,
            tenant_id=tenant_id,
            category="ai_gateway",
        ) == ({"base_url": "https://api.example.com/v1"}, {})
    finally:
        session.close()


def test_app_installation_secret_payload_rejects_invalid_secret_json(monkeypatch):
    from fastapi import HTTPException
    from postbridge.api import app_public

    tenant_id = "10000000-0000-4000-8000-000000000055"
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", tenant_id)
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    session = SESSION_LOCAL()
    try:
        row = session.scalar(
            select(InstallationSecretOrm).where(
                InstallationSecretOrm.tenant_id == tenant_id,
                InstallationSecretOrm.category == "ai_gateway",
            )
        )
        assert row is None
        session.add(
            InstallationSecretOrm(
                id="10000000-0000-4000-8000-000000000056",
                tenant_id=tenant_id,
                category="ai_gateway",
                status="configured",
                encrypted_secret=encrypt_credential_secret("not json"),
                config_json='{"base_url": "https://api.example.com/v1"}',
            )
        )
        session.commit()
        with pytest.raises(HTTPException) as exc_info:
            app_public._installation_secret_payload(
                session,
                tenant_id=tenant_id,
                category="ai_gateway",
            )
        assert exc_info.value.status_code == 422
    finally:
        session.close()


def test_app_bootstrap_commits_installation_secrets_before_welcome_content(monkeypatch):
    from postbridge.api import app_public

    tenant_id = "10000000-0000-4000-8000-000000000054"
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", tenant_id)

    def fail_welcome(*args, **kwargs):
        _ = args, kwargs
        raise RuntimeError("welcome failed")

    monkeypatch.setattr(app_public, "_ensure_selfhost_welcome_content", fail_welcome)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/app/bootstrap",
        json={
            "tenant_name": "Local",
            "installation_secrets": {
                "ai_gateway": {
                    "config": {"base_url": "https://gitsell.test/api/v1"},
                    "secret": {"api_key": "gsa-test"},
                }
            },
        },
    )

    assert response.status_code == 500
    session = SESSION_LOCAL()
    try:
        rows = {
            row.category: row
            for row in session.scalars(
                select(InstallationSecretOrm).where(InstallationSecretOrm.tenant_id == tenant_id)
            ).all()
        }
        assert "local_admin" in rows
        assert "ai_gateway" in rows
        assert rows["ai_gateway"].encrypted_secret
    finally:
        session.close()


def test_app_gitsell_device_flow_uses_locale_domain_and_returns_ai_gateway(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("AGENT_LLM_DEFAULT_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("AI_IMAGE_GENERATION_MODEL", "gpt-image-2")
    calls = []

    class FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/device_authorization"):
                return FakeResponse(
                    200,
                    {
                        "device_code": "device-token",
                        "user_code": "ABCD-EFGH",
                        "verification_uri_complete": "https://gitsell.ru/device?user_code=ABCD-EFGH",
                        "interval": 3,
                    },
                )
            return FakeResponse(
                200,
                {
                    "ai_proxy_token": "gsa-test-token",
                    "ai_proxy_token_id": 42,
                    "ai_proxy_token_name": "postbridge",
                },
            )

    monkeypatch.setattr(app_public.httpx, "Client", FakeClient)
    client = TestClient(app)

    start = client.post(
        "/api/app/gitsell-device/start",
        json={"locale": "ru", "instance_id": "postbridge-test", "instance_label": "Local"},
    )
    assert start.status_code == 200
    assert calls[0][0] == "https://gitsell.ru/api/oauth/device_authorization"
    assert start.json()["ai_gateway"]["base_url"] == "https://gitsell.ru/api/v1"
    assert start.json()["ai_gateway"]["default_model"] == "gpt-5.4-mini"
    assert start.json()["ai_gateway"]["image_model"] == "gpt-image-2"

    poll = client.post(
        "/api/app/gitsell-device/poll",
        json={"locale": "en", "device_code": "device-token"},
    )
    assert poll.status_code == 200
    body = poll.json()
    assert calls[1][0] == "https://gitsell.tech/api/oauth/token"
    assert body["status"] == "approved"
    assert body["ai_gateway"]["base_url"] == "https://gitsell.tech/api/v1"
    assert body["ai_gateway"]["default_model"] == "gpt-5.4-mini"
    assert body["ai_gateway"]["image_model"] == "gpt-image-2"
    assert body["ai_gateway"]["api_key"] == "gsa-test-token"


def test_app_telegram_import_flow_stores_telethon_session(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000069")

    class FakeSession:
        def __init__(self, value=None):
            self.value = value or "seed-session"

        def save(self):
            return self.value

    class FakeClient:
        def __init__(self, session_obj, api_id, api_hash):
            self.session_obj = session_obj
            self.api_id = api_id
            self.api_hash = api_hash

        async def connect(self):
            return None

        async def disconnect(self):
            return None

        async def send_code_request(self, phone):
            assert phone == "+12025550123"
            return SimpleNamespace(phone_code_hash="phone-code-hash")

        async def sign_in(self, **kwargs):
            assert kwargs["phone"] == "+12025550123"
            assert kwargs["code"] == "12345"
            assert kwargs["phone_code_hash"] == "phone-code-hash"
            self.session_obj.value = "final-session"

        async def get_me(self):
            return SimpleNamespace(id=42, username="editor", phone="12025550123", first_name="Ada", last_name="Lovelace")

    monkeypatch.setattr(app_public, "_new_telegram_string_session", lambda value=None: FakeSession(value))
    monkeypatch.setattr(app_public, "_new_telegram_client", lambda session_obj, api_id, api_hash: FakeClient(session_obj, api_id, api_hash))
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    start = client.post(
        "/api/app/telegram-import/start",
        json={"api_id": "12345", "api_hash": "hash", "phone": "+12025550123"},
    )

    assert start.status_code == 200, start.text
    assert start.json()["status"] == "code_sent"
    session = SESSION_LOCAL()
    try:
        pending = session.scalar(
            select(InstallationSecretOrm).where(InstallationSecretOrm.category == "telegram_import_flow")
        )
        assert pending is not None
        pending_secret = json.loads(decrypt_credential_secret(pending.encrypted_secret))
        assert pending.status == "pending"
        assert pending_secret["flow_id"] == start.json()["flow_id"]
        assert pending_secret["session_string"] == "seed-session"
    finally:
        session.close()

    complete = client.post(
        "/api/app/telegram-import/complete",
        json={"flow_id": start.json()["flow_id"], "code": "12345"},
    )

    assert complete.status_code == 200, complete.text
    assert complete.json()["status"] == "configured"
    reused = client.post(
        "/api/app/telegram-import/complete",
        json={"flow_id": start.json()["flow_id"], "code": "12345"},
    )
    assert reused.status_code == 404
    assert complete.json()["account"]["username"] == "editor"
    assert "final-session" not in json.dumps(complete.json())
    session = SESSION_LOCAL()
    try:
        row = session.scalar(select(InstallationSecretOrm).where(InstallationSecretOrm.category == "telegram_import"))
        assert row is not None
        secret = json.loads(decrypt_credential_secret(row.encrypted_secret))
        assert secret == {"api_id": "12345", "api_hash": "hash", "session_string": "final-session"}
        pending = session.scalar(
            select(InstallationSecretOrm).where(InstallationSecretOrm.category == "telegram_import_flow")
        )
        assert pending is None
    finally:
        session.close()


def test_app_telegram_import_flow_handles_2fa_password(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000070")

    class PasswordRequired(Exception):
        pass

    class FakeSession:
        def __init__(self, value=None):
            self.value = value or "seed-session"

        def save(self):
            return self.value

    class FakeClient:
        def __init__(self, session_obj, api_id, api_hash):
            self.session_obj = session_obj

        async def connect(self):
            return None

        async def disconnect(self):
            return None

        async def send_code_request(self, phone):
            return SimpleNamespace(phone_code_hash="phone-code-hash")

        async def sign_in(self, **kwargs):
            if "password" not in kwargs:
                self.session_obj.value = "password-session"
                raise PasswordRequired("password required")
            assert kwargs["password"] == "cloud-password"
            self.session_obj.value = "final-session"

        async def get_me(self):
            return SimpleNamespace(id=42, username="editor", phone=None, first_name=None, last_name=None)

    monkeypatch.setattr(app_public, "_telegram_password_required_error_type", lambda: PasswordRequired)
    monkeypatch.setattr(app_public, "_new_telegram_string_session", lambda value=None: FakeSession(value))
    monkeypatch.setattr(app_public, "_new_telegram_client", lambda session_obj, api_id, api_hash: FakeClient(session_obj, api_id, api_hash))
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    start = client.post(
        "/api/app/telegram-import/start",
        json={"api_id": "12345", "api_hash": "hash", "phone": "+12025550123"},
    )
    password_step = client.post(
        "/api/app/telegram-import/complete",
        json={"flow_id": start.json()["flow_id"], "code": "12345"},
    )

    assert password_step.status_code == 200, password_step.text
    assert password_step.json()["status"] == "password_required"
    session = SESSION_LOCAL()
    try:
        pending = session.scalar(
            select(InstallationSecretOrm).where(InstallationSecretOrm.category == "telegram_import_flow")
        )
        assert pending is not None
        pending_secret = json.loads(decrypt_credential_secret(pending.encrypted_secret))
        assert pending_secret["session_string"] == "password-session"
    finally:
        session.close()

    complete = client.post(
        "/api/app/telegram-import/complete",
        json={"flow_id": start.json()["flow_id"], "password": "cloud-password"},
    )

    assert complete.status_code == 200, complete.text
    assert complete.json()["status"] == "configured"


def test_app_telegram_import_flow_expires_from_database(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000071")
    now = {"value": 1_000.0}

    class FakeSession:
        def __init__(self, value=None):
            self.value = value or "seed-session"

        def save(self):
            return self.value

    class FakeClient:
        def __init__(self, session_obj, api_id, api_hash):
            self.session_obj = session_obj

        async def connect(self):
            return None

        async def disconnect(self):
            return None

        async def send_code_request(self, phone):
            return SimpleNamespace(phone_code_hash="phone-code-hash")

    monkeypatch.setattr(app_public.time, "time", lambda: now["value"])
    monkeypatch.setattr(app_public, "_new_telegram_string_session", lambda value=None: FakeSession(value))
    monkeypatch.setattr(app_public, "_new_telegram_client", lambda session_obj, api_id, api_hash: FakeClient(session_obj, api_id, api_hash))
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    start = client.post(
        "/api/app/telegram-import/start",
        json={"api_id": "12345", "api_hash": "hash", "phone": "+12025550123"},
    )
    assert start.status_code == 200, start.text

    now["value"] += app_public.TELEGRAM_IMPORT_FLOW_TTL_SECONDS + 1
    complete = client.post(
        "/api/app/telegram-import/complete",
        json={"flow_id": start.json()["flow_id"], "code": "12345"},
    )

    assert complete.status_code == 404
    session = SESSION_LOCAL()
    try:
        pending = session.scalar(
            select(InstallationSecretOrm).where(InstallationSecretOrm.category == "telegram_import_flow")
        )
        assert pending is None
    finally:
        session.close()


def test_app_telegram_import_flow_hides_provider_exception_text(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000072")

    class FakeSession:
        def __init__(self, value=None):
            self.value = value or "seed-session"

        def save(self):
            return self.value

    class StartFailingClient:
        def __init__(self, session_obj, api_id, api_hash):
            _ = session_obj, api_id, api_hash

        async def connect(self):
            return None

        async def disconnect(self):
            return None

        async def send_code_request(self, phone):
            _ = phone
            raise RuntimeError("raw phone +12025550123 leaked by provider")

    monkeypatch.setattr(app_public, "_new_telegram_string_session", lambda value=None: FakeSession(value))
    monkeypatch.setattr(
        app_public,
        "_new_telegram_client",
        lambda session_obj, api_id, api_hash: StartFailingClient(session_obj, api_id, api_hash),
    )
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    start = client.post(
        "/api/app/telegram-import/start",
        json={"api_id": "12345", "api_hash": "hash", "phone": "+12025550123"},
    )

    assert start.status_code == 502
    assert "Telegram login code request failed" in start.text
    assert "+12025550123" not in start.text
    assert "raw phone" not in start.text


def test_app_session_reports_unbootstrapped_selfhost(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    client = TestClient(app)

    response = client.get("/api/app/session")

    assert response.status_code == 200
    assert response.json() == {
        "app_mode": "selfhost",
        "bootstrapped": False,
        "setup_required": True,
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
        "display_name": "admin",
        "username": "admin",
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


def test_app_bootstrap_creates_postbridge_source_and_welcome_post(monkeypatch):
    tenant_id = "10000000-0000-4000-8000-000000000046"
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", tenant_id)
    client = TestClient(app)

    response = client.post("/api/app/bootstrap", json={"tenant_name": "Acme Local", "locale": "ru"})

    assert response.status_code == 200
    channels = client.get("/api/app/channels")
    assert channels.status_code == 200
    source = next(item for item in channels.json()["items"] if item["platform"] == "postbridge")
    assert source["kind"] == "source"
    assert source["external_id"] == "postbridge-local"
    assert source["can_read"] is True
    assert source["can_write"] is False
    content = client.get("/api/app/content-items")
    assert content.status_code == 200
    welcome = content.json()["items"][0]
    assert welcome["title"] == "Добро пожаловать в Postbridge"
    assert welcome["live_sync_source_core_channel_id"] is None
    assert "Добавьте канал" in welcome["content_md"]

    session = SESSION_LOCAL()
    try:
        row = session.get(ContentItemOrm, welcome["id"])
        assert row is not None
        assert row.language == "ru"
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
    assert second.json()["bootstrapped"] is True
    assert second.json()["authenticated"] is False

    session = SESSION_LOCAL()
    try:
        welcome_rows = session.scalars(
            select(ContentItemOrm).where(
                ContentItemOrm.tenant_id == tenant_id,
                ContentItemOrm.source_type == "postbridge",
                ContentItemOrm.body_structured_json.contains('"welcome"'),
            )
        ).all()
        assert len(welcome_rows) == 1
    finally:
        session.close()


def test_app_selfhost_welcome_content_is_idempotent(monkeypatch):
    from postbridge.api import app_public

    tenant_id = "10000000-0000-4000-8000-000000000059"
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", tenant_id)
    client = TestClient(app)

    setup = client.post("/api/app/bootstrap", json={"tenant_name": "Local", "locale": "en"})

    assert setup.status_code == 200
    session = SESSION_LOCAL()
    try:
        app_public._ensure_selfhost_welcome_content(session, tenant_id=tenant_id, locale="en")
        app_public._ensure_selfhost_welcome_content(session, tenant_id=tenant_id, locale="ru")
        session.commit()
        welcome_rows = session.scalars(
            select(ContentItemOrm).where(
                ContentItemOrm.tenant_id == tenant_id,
                ContentItemOrm.source_type == "postbridge",
                ContentItemOrm.body_structured_json.contains('"welcome"'),
            )
        ).all()
        assert len(welcome_rows) == 1
    finally:
        session.close()


def test_app_bootstrap_repeated_call_with_password_returns_session(monkeypatch):
    tenant_id = "10000000-0000-4000-8000-000000000047"
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", tenant_id)
    client = TestClient(app)

    first = client.post(
        "/api/app/bootstrap",
        json={"tenant_name": "First", "admin_username": "owner", "admin_password": "strong-password"},
    )
    second = client.post(
        "/api/app/bootstrap",
        json={"tenant_name": "Second", "admin_username": "owner", "admin_password": "strong-password"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["authenticated"] is True
    assert second.json()["token"]


def test_app_bootstrap_repeated_call_accepts_current_admin_password(monkeypatch):
    tenant_id = "10000000-0000-4000-8000-000000000051"
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", tenant_id)
    client = TestClient(app)

    first = client.post(
        "/api/app/bootstrap",
        json={"tenant_name": "First", "admin_username": "owner", "admin_password": "strong-password"},
    )
    second = client.post(
        "/api/app/bootstrap",
        json={"tenant_name": "Second", "current_admin_password": "strong-password"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["authenticated"] is True
    assert second.json()["token"]


def test_app_bootstrap_reports_missing_encryption_key_as_validation_error(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000048")
    monkeypatch.delenv("CREDENTIALS_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("CORE_SERVICE_TOKEN", raising=False)
    client = TestClient(app)

    response = client.post(
        "/api/app/bootstrap",
        json={"tenant_name": "Local", "admin_password": "strong-password"},
    )

    assert response.status_code == 422
    assert "CREDENTIALS_ENCRYPTION_KEY" in str(response.json())


def test_app_bootstrap_requires_new_admin_password_outside_tests(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000050")
    client = TestClient(app)

    response = client.post("/api/app/bootstrap", json={"tenant_name": "Local"})

    assert response.status_code == 422
    assert "new admin password is required" in str(response.json())


def test_app_selfhost_requires_local_admin_token(monkeypatch):
    tenant_id = "10000000-0000-4000-8000-000000000045"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("POSTBRIDGE_TEST_REQUIRE_AUTH", "1")
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", tenant_id)
    client = TestClient(app)

    setup = client.post(
        "/api/app/bootstrap",
        json={"tenant_name": "Private", "admin_username": "owner", "admin_password": "strong-password"},
    )
    assert setup.status_code == 200
    token = setup.json()["token"]

    assert client.get("/api/app/channels").status_code == 401
    assert client.get("/api/app/channels", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    authorized_session = client.get("/api/app/session", headers={"Authorization": f"Bearer {token}"})
    assert authorized_session.status_code == 200
    assert authorized_session.json()["authenticated"] is True
    assert authorized_session.json()["user"]["username"] == "owner"

    bad_login = client.post("/api/app/auth/login", json={"username": "owner", "password": "wrong"})
    assert bad_login.status_code == 401
    good_login = client.post("/api/app/auth/login", json={"username": "owner", "password": "strong-password"})
    assert good_login.status_code == 200
    assert good_login.json()["token"]


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


def test_app_telegram_target_uses_installation_bot_secret(monkeypatch):
    tenant_id = "10000000-0000-4000-8000-000000000042"
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", tenant_id)
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    saved_secret = client.put(
        "/api/app/installation-secrets/telegram_bot",
        json={
            "config": {"bot_username": "postbridge_test_bot"},
            "secret": {"bot_token": "123456:test-token"},
        },
    )
    assert saved_secret.status_code == 200

    def fake_admin_bot_api(self, method, url, **kwargs):
        _ = self, method, kwargs
        request = httpx.Request("GET", url)
        if url.endswith("/getMe"):
            return httpx.Response(200, request=request, json={"ok": True, "result": {"id": 42}})
        return httpx.Response(
            200,
            request=request,
            json={"ok": True, "result": {"status": "administrator", "can_post_messages": True}},
        )

    monkeypatch.setattr("postbridge.api.app_public.TelegramPublisher._request_bot_api", fake_admin_bot_api)
    channel = client.post(
        "/api/app/channels",
        json={
            "platform": "telegram",
            "kind": "destination",
            "title": "Telegram Target",
            "external_id": "@target",
            "can_read": False,
            "capabilities": {"can_write": "true"},
        },
    )

    assert channel.status_code == 200
    credential = client.get(f"/api/app/channels/{channel.json()['id']}/credential")
    assert credential.status_code == 200
    assert credential.json()["auth_type"] == "telegram_bot"
    assert credential.json()["has_secret"] is True
    assert "test-token" not in str(credential.json())

    session = SESSION_LOCAL()
    try:
        row = session.query(ChannelCredentialOrm).filter_by(channel_id=channel.json()["id"]).one()
        assert json.loads(decrypt_credential_secret(row.encrypted_secret)) == {
            "bot_token": "123456:test-token"
        }
    finally:
        session.close()


def test_app_telegram_source_does_not_attach_installation_bot_secret(monkeypatch):
    tenant_id = "10000000-0000-4000-8000-000000000242"
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", tenant_id)
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    assert client.put(
        "/api/app/installation-secrets/telegram_bot",
        json={
            "config": {"bot_username": "postbridge_test_bot"},
            "secret": {"bot_token": "123456:test-token"},
        },
    ).status_code == 200

    channel = client.post(
        "/api/app/channels",
        json={
            "platform": "telegram",
            "kind": "source",
            "title": "Telegram Source",
            "external_id": "https://t.me/postbridge_source",
            "can_read": True,
            "can_write": False,
        },
    )

    assert channel.status_code == 200, channel.text
    assert channel.json()["platform_channel_id"] == "@postbridge_source"
    credential = client.get(f"/api/app/channels/{channel.json()['id']}/credential")
    assert credential.status_code == 200
    assert credential.json()["has_secret"] is False


def test_app_telegram_target_credentials_ref_runs_bot_validation(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000243")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    source = client.post(
        "/api/app/channels",
        json={
            "platform": "telegram",
            "kind": "source",
            "title": "Telegram Source",
            "external_id": "https://t.me/postbridge_target",
            "can_read": True,
            "can_write": False,
        },
    )
    assert source.status_code == 200, source.text

    target = client.post(
        "/api/app/channels",
        json={
            "platform": "telegram",
            "kind": "destination",
            "title": "Telegram Target",
            "credentials_ref": source.json()["id"],
        },
    )

    assert target.status_code == 422
    assert target.json()["message"] == "connections.validation.telegram.bot_not_configured"


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


def test_app_channel_registry_validates_rss_source_reachability(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000044")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    def fake_fetch(url):
        request = httpx.Request("GET", url)
        return httpx.Response(503, request=request)

    monkeypatch.setattr(app_public, "_fetch_public_rss_url_once", fake_fetch)
    response = client.post(
        "/api/app/channel-registry/validate",
        json={"platform": "rss", "platform_channel_id": "https://example.com/feed.xml", "role": "source"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["errors"] == ["connections.validation.rss.unreachable"]

    missing = client.post(
        "/api/app/channel-registry/validate",
        json={"platform": "rss", "platform_channel_id": "   ", "role": "source"},
    )
    assert missing.status_code == 200
    assert missing.json()["ok"] is False
    assert missing.json()["errors"] == ["connections.validation.rss.url_required"]

    invalid = client.post(
        "/api/app/channel-registry/validate",
        json={"platform": "rss", "platform_channel_id": "example.com/feed.xml", "role": "source"},
    )
    assert invalid.status_code == 200
    assert invalid.json()["ok"] is False
    assert invalid.json()["errors"] == ["connections.validation.rss.url_invalid"]


def test_app_channel_registry_rejects_private_rss_source_url(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000145")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    response = client.post(
        "/api/app/channel-registry/validate",
        json={"platform": "rss", "platform_channel_id": "http://169.254.169.254/latest/meta-data", "role": "source"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["errors"] == ["connections.validation.rss.url_invalid"]


def test_app_channel_registry_rejects_rss_redirect_to_private_url(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000146")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    monkeypatch.setattr(
        app_public.socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 0))],
    )

    def fake_fetch(url):
        if "127.0.0.1" in url:
            return None
        request = httpx.Request("GET", url)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/feed.xml"}, request=request)

    monkeypatch.setattr(app_public, "_fetch_public_rss_url_once", fake_fetch)
    response = client.post(
        "/api/app/channel-registry/validate",
        json={"platform": "rss", "platform_channel_id": "https://example.com/feed.xml", "role": "source"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["errors"] == ["connections.validation.rss.url_invalid"]


def test_app_rss_source_fetch_uses_validated_address_with_tls_sni(monkeypatch):
    from postbridge.api import app_public

    created_connections = []
    wrapped_hosts = []

    class FakeSocket:
        def __init__(self):
            self.sent = b""
            self.closed = False

        def sendall(self, data):
            self.sent += data

        def makefile(self, *args, **kwargs):
            _ = args, kwargs
            return BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")

        def close(self):
            self.closed = True

    class FakeSslContext:
        def wrap_socket(self, sock, *, server_hostname):
            wrapped_hosts.append(server_hostname)
            return sock

    fake_socket = FakeSocket()
    monkeypatch.setattr(
        app_public.socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 0))],
    )
    monkeypatch.setattr(
        app_public.socket,
        "create_connection",
        lambda address, timeout: created_connections.append((address, timeout)) or fake_socket,
    )
    monkeypatch.setattr(app_public.ssl, "create_default_context", lambda: FakeSslContext())

    response = app_public._fetch_public_rss_url_once("https://example.com/feed.xml")

    assert response is not None
    assert response.status_code == 200
    assert created_connections == [(("93.184.216.34", 443), app_public.RSS_SOURCE_VALIDATION_TIMEOUT_SECONDS)]
    assert wrapped_hosts == ["example.com"]
    assert b"Host: example.com\r\n" in fake_socket.sent
    assert fake_socket.closed is True


def test_app_channel_registry_rejects_mixed_public_private_rss_dns(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000150")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    monkeypatch.setattr(
        app_public.socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: [
            (None, None, None, None, ("93.184.216.34", 0)),
            (None, None, None, None, ("127.0.0.1", 0)),
        ],
    )

    def fail_get(*args, **kwargs):
        _ = args, kwargs
        raise AssertionError("private DNS answers must be rejected before fetch")

    monkeypatch.setattr(app_public.httpx, "get", fail_get)
    response = client.post(
        "/api/app/channel-registry/validate",
        json={"platform": "rss", "platform_channel_id": "https://example.com/feed.xml", "role": "source"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["errors"] == ["connections.validation.rss.url_invalid"]


def test_app_channel_registry_handles_rss_dns_failure_and_timeout(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000147")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    monkeypatch.setattr(
        app_public.socket,
        "getaddrinfo",
        lambda *args, **kwargs: (_ for _ in ()).throw(app_public.socket.gaierror()),
    )
    dns_failed = client.post(
        "/api/app/channel-registry/validate",
        json={"platform": "rss", "platform_channel_id": "https://missing.example/feed.xml", "role": "source"},
    )
    assert dns_failed.status_code == 200
    assert dns_failed.json()["ok"] is False
    assert dns_failed.json()["errors"] == ["connections.validation.rss.url_invalid"]

    monkeypatch.setattr(
        app_public.socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 0))],
    )

    def timeout_fetch(url):
        _ = url
        raise httpx.TimeoutException("slow feed")

    monkeypatch.setattr(app_public, "_fetch_public_rss_url_once", timeout_fetch)
    timed_out = client.post(
        "/api/app/channel-registry/validate",
        json={"platform": "rss", "platform_channel_id": "https://example.com/feed.xml", "role": "source"},
    )
    assert timed_out.status_code == 200
    assert timed_out.json()["ok"] is False
    assert timed_out.json()["errors"] == ["connections.validation.rss.unreachable"]


def test_app_channel_registry_caps_rss_source_validation_latency(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000148")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    monkeypatch.setattr(
        app_public.socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 0))],
    )
    calls = []

    def redirect_forever(url):
        calls.append(url)
        request = httpx.Request("GET", url)
        return httpx.Response(302, headers={"location": "/next.xml"}, request=request)

    monkeypatch.setattr(app_public, "_fetch_public_rss_url_once", redirect_forever)
    response = client.post(
        "/api/app/channel-registry/validate",
        json={"platform": "rss", "platform_channel_id": "https://example.com/feed.xml", "role": "source"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["errors"] == ["connections.validation.rss.unreachable"]
    assert len(calls) == app_public.RSS_SOURCE_VALIDATION_MAX_REDIRECTS + 1
    assert app_public.RSS_SOURCE_VALIDATION_TIMEOUT_SECONDS <= 2.0


def test_app_channel_registry_validates_rss_target_without_fetching_source_url(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000144")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    def fail_get(*args, **kwargs):
        _ = args, kwargs
        raise AssertionError("RSS target validation must not fetch feed URL")

    monkeypatch.setattr(app_public.httpx, "get", fail_get)
    response = client.post(
        "/api/app/channel-registry/validate",
        json={"platform": "rss", "platform_channel_id": "public-feed", "role": "target"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["platform_channel_id"] == "public-feed"


def test_app_channel_registry_validates_telegram_target_bot_admin(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000043")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    missing_bot = client.post(
        "/api/app/channel-registry/validate",
        json={"platform": "telegram", "platform_channel_id": "https://t.me/postbridge_test3", "role": "target"},
    )
    assert missing_bot.status_code == 200
    assert missing_bot.json()["ok"] is False
    assert missing_bot.json()["errors"] == ["connections.validation.telegram.bot_not_configured"]

    saved_secret = client.put(
        "/api/app/installation-secrets/telegram_bot",
        json={
            "config": {"bot_username": "postbridge_test_bot"},
            "secret": {"bot_token": "123456:test-token"},
        },
    )
    assert saved_secret.status_code == 200

    def fake_bot_api(self, method, url, **kwargs):
        _ = self, method
        request = httpx.Request("GET", url)
        if url.endswith("/getMe"):
            return httpx.Response(200, request=request, json={"ok": True, "result": {"id": 42}})
        assert url.endswith("/getChatMember")
        assert kwargs["params"] == {"chat_id": "@postbridge_test3", "user_id": 42}
        return httpx.Response(400, request=request, json={"ok": False, "description": "Bad Request: user not found"})

    monkeypatch.setattr("postbridge.api.app_public.TelegramPublisher._request_bot_api", fake_bot_api)
    not_admin = client.post(
        "/api/app/channel-registry/validate",
        json={"platform": "telegram", "platform_channel_id": "https://t.me/postbridge_test3", "role": "target"},
    )
    assert not_admin.status_code == 200
    assert not_admin.json()["ok"] is False
    assert not_admin.json()["errors"] == ["connections.validation.telegram.target_bot_admin_required"]

    def fake_admin_without_post_permission(self, method, url, **kwargs):
        _ = self, method, kwargs
        request = httpx.Request("GET", url)
        if url.endswith("/getMe"):
            return httpx.Response(200, request=request, json={"ok": True, "result": {"id": 42}})
        return httpx.Response(
            200,
            request=request,
            json={"ok": True, "result": {"status": "administrator"}},
        )

    monkeypatch.setattr("postbridge.api.app_public.TelegramPublisher._request_bot_api", fake_admin_without_post_permission)
    missing_post_permission = client.post(
        "/api/app/channel-registry/validate",
        json={"platform": "telegram", "platform_channel_id": "https://t.me/postbridge_test3", "role": "target"},
    )
    assert missing_post_permission.status_code == 200
    assert missing_post_permission.json()["ok"] is False
    assert missing_post_permission.json()["errors"] == ["connections.validation.telegram.target_bot_admin_required"]

    def fake_admin_bot_api(self, method, url, **kwargs):
        _ = self, method, kwargs
        request = httpx.Request("GET", url)
        if url.endswith("/getMe"):
            return httpx.Response(200, request=request, json={"ok": True, "result": {"id": 42}})
        return httpx.Response(
            200,
            request=request,
            json={"ok": True, "result": {"status": "administrator", "can_post_messages": True}},
        )

    monkeypatch.setattr("postbridge.api.app_public.TelegramPublisher._request_bot_api", fake_admin_bot_api)
    admin = client.post(
        "/api/app/channel-registry/validate",
        json={"platform": "telegram", "platform_channel_id": "https://t.me/postbridge_test3", "role": "target"},
    )
    assert admin.status_code == 200
    assert admin.json()["ok"] is True
    assert admin.json()["platform_channel_id"] == "@postbridge_test3"


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


def test_app_vk_and_linkedin_channels_require_managed_credentials(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000045")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    vk = client.post(
        "/api/app/channels",
        json={
            "platform": "vk",
            "platform_channel_id": "vk.com/club123456789",
            "title": "VK Community",
            "can_read": True,
            "can_write": True,
        },
    )
    linkedin = client.post(
        "/api/app/channels",
        json={
            "platform": "linkedin",
            "platform_channel_id": "organization:42",
            "title": "LinkedIn Org",
            "can_read": False,
            "can_write": True,
        },
    )

    assert vk.status_code == 422
    assert vk.json()["message"] == "Connect VK credentials first."
    assert linkedin.status_code == 422
    assert linkedin.json()["message"] == "Connect LinkedIn credentials first."


def test_app_manual_global_credentials_create_and_channel_upsert(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000046")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    facebook = client.post(
        "/api/app/credentials/platform/manual",
        json={
            "platform": "facebook",
            "platform_channel_id": "facebook/page/42",
            "page_access_token": "fb-secret",
            "display": "FB Page",
        },
    )
    bluesky = client.post(
        "/api/app/credentials/platform/manual",
        json={
            "platform": "bluesky",
            "platform_channel_id": "@alice.test",
            "app_password": "bsky-secret",
            "display": "Alice",
        },
    )

    assert facebook.status_code == 200, facebook.text
    assert bluesky.status_code == 200, bluesky.text
    assert facebook.json()["platform_channel_id"] == "42"
    assert bluesky.json()["platform_channel_id"] == "alice.test"

    channel = client.post(
        "/api/app/channels",
        json={
            "platform": "facebook",
            "platform_channel_id": facebook.json()["platform_channel_id"],
            "title": "FB Page",
            "can_read": False,
            "can_write": True,
            "credentials_ref": facebook.json()["id"],
        },
    )

    assert channel.status_code == 200, channel.text
    assert channel.json()["id"] == facebook.json()["id"]
    assert channel.json()["credentials_ref"] == facebook.json()["id"]
    assert channel.json()["can_read"] is False
    assert channel.json()["can_write"] is True

    session = SESSION_LOCAL()
    try:
        fb_credential = session.query(ChannelCredentialOrm).filter_by(channel_id=facebook.json()["id"]).one()
        bsky_credential = session.query(ChannelCredentialOrm).filter_by(channel_id=bluesky.json()["id"]).one()
        assert fb_credential.auth_type == "facebook_page_access_token"
        assert bsky_credential.auth_type == "bluesky_app_password"
        assert "fb-secret" not in fb_credential.encrypted_secret
        assert "bsky-secret" not in bsky_credential.encrypted_secret
        assert json.loads(decrypt_credential_secret(fb_credential.encrypted_secret))["page_access_token"] == "fb-secret"
        assert json.loads(decrypt_credential_secret(bsky_credential.encrypted_secret))["identifier"] == "alice.test"
    finally:
        session.close()


def test_app_global_channels_require_managed_credentials(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000047")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    response = client.post(
        "/api/app/channels",
        json={
            "platform": "x",
            "platform_channel_id": "@postbridge",
            "title": "X",
            "can_read": False,
            "can_write": True,
        },
    )

    assert response.status_code == 422
    assert response.json()["message"] == "Connect X credentials first."


def test_app_oauth_authorize_url_helpers(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000048")
    monkeypatch.setenv("META_OAUTH_CLIENT_ID", "meta-client")
    monkeypatch.setenv("META_OAUTH_REDIRECT_URI", "https://core.test/oauth/meta")
    monkeypatch.setenv("META_OAUTH_CONFIG_ID", "meta-config")
    monkeypatch.setenv("X_OAUTH_CLIENT_ID", "x-client")
    monkeypatch.setenv("X_OAUTH_REDIRECT_URI", "https://core.test/oauth/x")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    meta = client.post(
        "/api/app/credentials/oauth/authorize-url",
        json={"platform": "facebook", "state": "state-1"},
    )
    x = client.post(
        "/api/app/credentials/oauth/authorize-url",
        json={"platform": "x", "state": "state-2"},
    )

    assert meta.status_code == 200, meta.text
    assert x.status_code == 200, x.text
    meta_url = urlparse(meta.json()["authorize_url"])
    meta_qs = parse_qs(meta_url.query)
    assert meta_url.netloc == "www.facebook.com"
    assert meta_qs["client_id"] == ["meta-client"]
    assert meta_qs["redirect_uri"] == ["https://core.test/oauth/meta"]
    assert meta_qs["config_id"] == ["meta-config"]
    assert "business_management" in meta_qs["scope"][0]
    assert "pages_manage_posts" in meta_qs["scope"][0]
    assert "instagram_content_publish" in meta_qs["scope"][0]

    x_url = urlparse(x.json()["authorize_url"])
    x_qs = parse_qs(x_url.query)
    assert x_url.netloc == "x.com"
    assert x_qs["client_id"] == ["x-client"]
    assert x_qs["redirect_uri"] == ["https://core.test/oauth/x"]
    assert x_qs["code_challenge_method"] == ["S256"]
    assert x.json()["code_verifier"]
    assert "media.write" in x_qs["scope"][0]


def test_app_global_credential_validation_helpers(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000049")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    class FakeProviderClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, **kwargs):
            request = httpx.Request("GET", url)
            if url.endswith("/v25.0/42"):
                return httpx.Response(200, json={"id": "42", "name": "Page Name"}, request=request)
            if url.endswith("/v25.0/17841400000000000"):
                return httpx.Response(
                    200,
                    json={"id": "17841400000000000", "username": "ig_name"},
                    request=request,
                )
            if url == "https://api.x.com/2/users/me":
                return httpx.Response(
                    200,
                    json={"data": {"id": "7", "username": "postbridge", "name": "Postbridge"}},
                    request=request,
                )
            if url == "https://mastodon.social/api/v1/accounts/verify_credentials":
                return httpx.Response(
                    200,
                    json={"id": "9", "acct": "postbridge", "display_name": "Postbridge Masto"},
                    request=request,
                )
            raise AssertionError(f"unexpected GET {url}")

        def post(self, url, **kwargs):
            request = httpx.Request("POST", url)
            if url == "https://bsky.social/xrpc/com.atproto.server.createSession":
                return httpx.Response(
                    200,
                    json={"did": "did:plc:abc", "handle": "alice.test", "accessJwt": "jwt"},
                    request=request,
                )
            raise AssertionError(f"unexpected POST {url}")

    monkeypatch.setattr(app_public.httpx, "Client", FakeProviderClient)

    facebook = client.post(
        "/api/app/credentials/platform/validate",
        json={"platform": "facebook", "platform_channel_id": "42", "page_access_token": "secret"},
    )
    instagram = client.post(
        "/api/app/credentials/platform/validate",
        json={
            "platform": "instagram",
            "platform_channel_id": "17841400000000000",
            "access_token": "secret",
        },
    )
    x = client.post(
        "/api/app/credentials/platform/validate",
        json={"platform": "x", "platform_channel_id": "@postbridge", "access_token": "secret"},
    )
    bluesky = client.post(
        "/api/app/credentials/platform/validate",
        json={"platform": "bluesky", "platform_channel_id": "@alice.test", "app_password": "secret"},
    )
    mastodon = client.post(
        "/api/app/credentials/platform/validate",
        json={
            "platform": "mastodon",
            "platform_channel_id": "@postbridge@mastodon.social",
            "access_token": "secret",
            "instance_url": "https://mastodon.social",
        },
    )

    assert facebook.status_code == 200, facebook.text
    assert instagram.status_code == 200, instagram.text
    assert x.status_code == 200, x.text
    assert bluesky.status_code == 200, bluesky.text
    assert mastodon.status_code == 200, mastodon.text
    assert facebook.json()["display"] == "Page Name"
    assert instagram.json()["display"] == "ig_name"
    assert x.json()["platform_channel_id"] == "postbridge"
    assert bluesky.json()["did"] == "did:plc:abc"
    assert mastodon.json()["platform_channel_id"] == "@postbridge@mastodon.social"
    assert all(item.json()["can_write"] is True for item in [facebook, instagram, x, bluesky, mastodon])


def test_app_oauth_token_and_meta_pages_helpers(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000050")
    monkeypatch.setenv("META_OAUTH_CLIENT_ID", "meta-client")
    monkeypatch.setenv("META_OAUTH_CLIENT_SECRET", "meta-secret")
    monkeypatch.setenv("META_OAUTH_REDIRECT_URI", "https://core.test/oauth/meta")
    monkeypatch.setenv("X_OAUTH_CLIENT_ID", "x-client")
    monkeypatch.setenv("X_OAUTH_CLIENT_SECRET", "x-secret")
    monkeypatch.setenv("X_OAUTH_REDIRECT_URI", "https://core.test/oauth/x")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    class FakeProviderClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, **kwargs):
            request = httpx.Request("GET", url)
            if url.endswith("/oauth/access_token"):
                return httpx.Response(
                    200,
                    json={"access_token": "meta-user-token", "expires_in": 3600},
                    request=request,
                )
            if url.endswith("/me/accounts"):
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": "42",
                                "name": "Page Name",
                                "access_token": "page-token",
                                "instagram_business_account": {
                                    "id": "17841400000000000",
                                    "username": "ig_name",
                                },
                            }
                        ]
                    },
                    request=request,
                )
            raise AssertionError(f"unexpected GET {url}")

        def post(self, url, **kwargs):
            request = httpx.Request("POST", url)
            if url == "https://api.x.com/2/oauth2/token":
                return httpx.Response(
                    200,
                    json={"access_token": "x-token", "refresh_token": "x-refresh", "expires_in": 7200},
                    request=request,
                )
            raise AssertionError(f"unexpected POST {url}")

    monkeypatch.setattr(app_public.httpx, "Client", FakeProviderClient)

    meta_token = client.post("/api/app/credentials/oauth/token", json={"platform": "meta", "code": "abc"})
    x_token = client.post(
        "/api/app/credentials/oauth/token",
        json={"platform": "x", "code": "abc", "code_verifier": "verifier"},
    )
    pages = client.post(
        "/api/app/credentials/meta/pages",
        json={"access_token": "meta-user-token"},
    )

    assert meta_token.status_code == 200, meta_token.text
    assert x_token.status_code == 200, x_token.text
    assert pages.status_code == 200, pages.text
    assert meta_token.json()["access_token"] == "meta-user-token"
    assert meta_token.json()["expires_at"]
    assert x_token.json()["refresh_token"] == "x-refresh"
    assert x_token.json()["expires_at"]
    assert pages.json()["items"] == [
        {
            "page_id": "42",
            "name": "Page Name",
            "page_access_token": "page-token",
            "instagram_user_id": "17841400000000000",
            "instagram_username": "ig_name",
        }
    ]


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
    empty_previews = client.post("/api/app/platform-previews")

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
    assert empty_previews.status_code == 200
    assert empty_previews.json()["items"] == []


def test_app_selfhost_platform_previews_include_active_postbridge_bridges(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000065")
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
    target = client.post(
        "/api/app/channels",
        json={
            "platform": "rss",
            "kind": "destination",
            "title": "RSS",
            "external_id": "rss",
            "can_write": True,
        },
    ).json()
    connection = client.post(
        "/api/app/connections/create",
        json={
            "source_platform": "postbridge",
            "source_channel_id": source["platform_channel_id"],
            "source_display": source["title"],
            "target_platform": "rss",
            "target_channel_id": target["platform_channel_id"],
            "target_display": target["title"],
        },
    )
    assert connection.status_code == 200, connection.text
    bridge = connection.json()["bridge"]

    response = client.post(
        "/api/app/platform-previews",
        json={"title": "Demo post", "content_md": "Bridge-ready content"},
    )

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["platform"] == "rss"
    assert items[0]["targets"] == [
        {
            "id": target["id"],
            "title": "RSS",
            "platform_channel_id": "rss",
            "bridge_id": bridge["id"],
        }
    ]
    assert "Demo post" in items[0]["text"]
    assert "Bridge-ready content" in items[0]["text"]
    assert items[0]["adaptation_status"] == "ready"


def test_app_selfhost_platform_preview_accepts_serialized_bridge_settings(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000067")
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
    target = client.post(
        "/api/app/channels",
        json={
            "platform": "rss",
            "kind": "destination",
            "title": "RSS",
            "external_id": "rss",
            "can_write": True,
        },
    ).json()
    connection = client.post(
        "/api/app/connections/create",
        json={
            "source_platform": "postbridge",
            "source_channel_id": source["platform_channel_id"],
            "source_display": source["title"],
            "target_platform": "rss",
            "target_channel_id": target["platform_channel_id"],
            "target_display": target["title"],
        },
    )
    assert connection.status_code == 200, connection.text
    bridge = connection.json()["bridge"]
    second_target = client.post(
        "/api/app/channels",
        json={
            "platform": "rss",
            "kind": "destination",
            "title": "RSS 2",
            "external_id": "rss-2",
            "can_write": True,
        },
    ).json()
    second_connection = client.post(
        "/api/app/connections/create",
        json={
            "source_platform": "postbridge",
            "source_channel_id": source["platform_channel_id"],
            "source_display": source["title"],
            "target_platform": "rss",
            "target_channel_id": second_target["platform_channel_id"],
            "target_display": second_target["title"],
        },
    )
    assert second_connection.status_code == 200, second_connection.text
    session = SESSION_LOCAL()
    try:
        row = session.get(BridgeOrm, bridge["id"])
        assert row is not None
        row.settings_json = json.dumps({"adaptation_mode": "ai_review"})
        session.commit()
    finally:
        session.close()

    response = client.post(
        "/api/app/platform-previews",
        json={"title": "Demo post", "content_md": "Bridge-ready content"},
    )

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert {item["adaptation_mode"] for item in items} == {"ai_review", "rule_only"}
    ai_review = next(item for item in items if item["adaptation_mode"] == "ai_review")
    rule_only = next(item for item in items if item["adaptation_mode"] == "rule_only")
    assert [target["id"] for target in ai_review["targets"]] == [target["id"]]
    assert [target["id"] for target in rule_only["targets"]] == [second_target["id"]]


def test_app_selfhost_telegram_source_channel_normalizes_external_id(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000167")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    response = client.post(
        "/api/app/channels",
        json={
            "platform": "telegram",
            "kind": "source",
            "title": "Telegram Source",
            "external_id": "https://t.me/postbridge_test3",
            "can_read": True,
            "can_write": False,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["platform_channel_id"] == "@postbridge_test3"


def test_app_selfhost_rss_target_channel_exposes_local_feed_url(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000057")
    client = TestClient(app)
    setup = client.post(
        "/api/app/bootstrap",
        json={"tenant_name": "Local", "admin_username": "owner", "admin_password": "strong-password"},
    )
    assert setup.status_code == 200
    source = client.post(
        "/api/app/channels",
        json={
            "platform": "postbridge",
            "kind": "source",
            "title": "Postbridge Source",
            "external_id": "postbridge-local",
        },
    ).json()
    created = client.post(
        "/api/app/channels",
        json={
            "platform": "rss",
            "kind": "destination",
            "title": "RSS",
            "external_id": "rss",
            "can_write": True,
        },
    )
    assert created.status_code == 200
    assert created.json()["platform_channel_id"] == "rss"
    assert created.json()["can_read"] is False
    assert created.json()["can_write"] is True
    assert created.json()["rss_feed_url"] == "/rss/rss.xml"
    bridge = client.post(
        "/api/app/connections/create",
        json={
            "source_platform": "postbridge",
            "source_channel_id": source["platform_channel_id"],
            "source_display": source["title"],
            "target_platform": "rss",
            "target_channel_id": created.json()["platform_channel_id"],
            "target_display": created.json()["title"],
        },
    )
    assert bridge.status_code == 200, bridge.text
    response = client.get("/api/app/channels")

    assert response.status_code == 200, response.text
    rss_channels = [item for item in response.json()["items"] if item["platform"] == "rss"]
    assert rss_channels
    assert rss_channels[0]["platform_channel_id"] == "rss"
    assert rss_channels[0]["rss_feed_url"] == "/rss/rss.xml"


def test_app_selfhost_rss_target_rejects_long_feed_id(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000178")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    created = client.post(
        "/api/app/channels",
        json={
            "platform": "rss",
            "kind": "destination",
            "title": "RSS",
            "external_id": "r" * 129,
            "can_write": True,
        },
    )

    assert created.status_code == 422
    assert created.json()["message"] == "connections.validation.rss.feed_id_too_long"


def test_app_selfhost_delete_channel_detaches_batch_import_runs(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000068")
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
    target = client.post(
        "/api/app/channels",
        json={
            "platform": "rss",
            "kind": "destination",
            "title": "RSS",
            "external_id": "rss",
            "can_write": True,
        },
    ).json()
    bridge = client.post(
        "/api/app/connections/create",
        json={
            "source_platform": "postbridge",
            "source_channel_id": source["platform_channel_id"],
            "target_platform": "rss",
            "target_channel_id": target["platform_channel_id"],
        },
    )
    assert bridge.status_code == 200, bridge.text
    session = SESSION_LOCAL()
    try:
        session.add(
            BatchImportRunOrm(
                id="10000000-0000-4000-8000-000000000168",
                tenant_id="10000000-0000-4000-8000-000000000068",
                source_channel="postbridge-local",
                target_channel="rss",
                status="completed",
                requested_limit=10,
                processed_posts=1,
                retry_count=0,
                source_platform="postbridge",
                target_platform="rss",
                source_core_channel_id=source["id"],
                target_core_channel_id=target["id"],
            )
        )
        content = ContentItemOrm(
            id="10000000-0000-4000-8000-000000000268",
            tenant_id="10000000-0000-4000-8000-000000000068",
            author_user_id="local-admin",
            source_type="manual",
            title="Draft",
            body_markdown="Draft",
            body_structured_json=None,
            status="draft",
        )
        plan = PublicationPlanOrm(
            id="10000000-0000-4000-8000-000000000368",
            tenant_id="10000000-0000-4000-8000-000000000068",
            content_item_id=content.id,
            strategy="manual",
            status="draft",
        )
        publication_target = PublicationTargetOrm(
            id="10000000-0000-4000-8000-000000000468",
            tenant_id="10000000-0000-4000-8000-000000000068",
            publication_plan_id=plan.id,
            channel_id=target["id"],
            platform="rss",
            status="draft",
        )
        session.add_all([content, plan, publication_target])
        session.commit()
    finally:
        session.close()

    response = client.delete(f"/api/app/channels/{target['id']}")

    assert response.status_code == 204, response.text
    session = SESSION_LOCAL()
    try:
        assert session.get(ChannelOrm, target["id"]) is None
        assert session.scalar(select(BridgeOrm).where(BridgeOrm.target_channel_id == target["id"])) is None
        assert session.scalar(select(PublicationTargetOrm).where(PublicationTargetOrm.channel_id == target["id"])) is None
        batch_run = session.get(BatchImportRunOrm, "10000000-0000-4000-8000-000000000168")
        assert batch_run is not None
        assert batch_run.source_core_channel_id == source["id"]
        assert batch_run.target_core_channel_id is None
        assert batch_run.target_channel == f"deleted:{target['id']}"
    finally:
        session.close()


def test_app_selfhost_delete_channel_removes_bridges_for_any_user(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000170")
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
    target = client.post(
        "/api/app/channels",
        json={
            "platform": "rss",
            "kind": "destination",
            "title": "RSS",
            "external_id": "rss",
            "can_write": True,
        },
    ).json()
    session = SESSION_LOCAL()
    try:
        session.add(
            BridgeOrm(
                id="10000000-0000-4000-8000-000000000270",
                tenant_id="10000000-0000-4000-8000-000000000170",
                saas_user_id="legacy-user",
                source_channel_id=source["id"],
                target_channel_id=target["id"],
                status="active",
                mode="live_sync",
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.delete(f"/api/app/channels/{target['id']}")

    assert response.status_code == 204, response.text
    session = SESSION_LOCAL()
    try:
        assert session.get(ChannelOrm, target["id"]) is None
        assert session.scalar(select(BridgeOrm).where(BridgeOrm.target_channel_id == target["id"])) is None
    finally:
        session.close()


def test_app_selfhost_delete_source_channel_detaches_legacy_batch_import_refs(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000169")
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
    target = client.post(
        "/api/app/channels",
        json={
            "platform": "rss",
            "kind": "destination",
            "title": "RSS",
            "external_id": "rss",
            "can_write": True,
        },
    ).json()
    session = SESSION_LOCAL()
    try:
        session.add(
            BatchImportRunOrm(
                id="10000000-0000-4000-8000-000000000269",
                tenant_id="10000000-0000-4000-8000-000000000169",
                source_channel="postbridge-local",
                target_channel="rss",
                status="completed",
                requested_limit=10,
                processed_posts=1,
                retry_count=0,
                source_platform="postbridge",
                target_platform="rss",
                source_core_channel_id=source["id"],
                target_core_channel_id=target["id"],
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.delete(f"/api/app/channels/{source['id']}")

    assert response.status_code == 204, response.text
    session = SESSION_LOCAL()
    try:
        batch_run = session.get(BatchImportRunOrm, "10000000-0000-4000-8000-000000000269")
        assert batch_run is not None
        assert batch_run.source_core_channel_id is None
        assert batch_run.source_channel == f"deleted:{source['id']}"
        assert batch_run.target_core_channel_id == target["id"]
    finally:
        session.close()


def test_app_selfhost_serves_local_rss_feed(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    session = SESSION_LOCAL()
    try:
        session.add(
            RssFeedItemOrm(
                feed_id="rss",
                source_channel="postbridge-local",
                source_post_id="post-1",
                text="Hello from Postbridge\nSecond line",
                media_url="https://example.test/image.png",
            )
        )
        session.commit()
    finally:
        session.close()

    client = TestClient(app)
    response = client.get("/rss/rss.xml")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/rss+xml")
    assert "<title>Hello from Postbridge</title>" in response.text
    assert "<description>Hello from Postbridge\nSecond line</description>" in response.text
    assert '<guid isPermaLink="false">rss:item:1</guid>' in response.text
    assert "<link>https://example.test/image.png</link>" in response.text


def test_app_selfhost_rss_feed_handles_empty_item_text(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    session = SESSION_LOCAL()
    try:
        session.add(
            RssFeedItemOrm(
                feed_id="rss",
                source_channel="postbridge-local",
                source_post_id="post-empty",
                text="   \n  ",
            )
        )
        session.commit()
    finally:
        session.close()

    client = TestClient(app)
    response = client.get("/rss/rss.xml")

    assert response.status_code == 200, response.text
    assert "<title>Postbridge post</title>" in response.text


def test_app_selfhost_serves_rss_feed_id_up_to_channel_limit(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    feed_id = "rss-" + ("a" * 124)
    session = SESSION_LOCAL()
    try:
        session.add(
            RssFeedItemOrm(
                feed_id=feed_id,
                source_channel="postbridge-local",
                source_post_id="post-long-feed",
                text="Long feed id",
            )
        )
        session.commit()
    finally:
        session.close()

    client = TestClient(app)
    response = client.get(f"/rss/{feed_id}.xml")

    assert response.status_code == 200, response.text
    assert "<title>Long feed id</title>" in response.text


def test_app_selfhost_rss_feed_escapes_xml_and_rejects_invalid_feed_id(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    session = SESSION_LOCAL()
    try:
        session.add(
            RssFeedItemOrm(
                feed_id="rss",
                source_channel="postbridge-local",
                source_post_id="post-xml",
                text="Tom & Jerry <launch>",
                media_url="https://example.test/?a=1&b=2",
            )
        )
        session.commit()
    finally:
        session.close()

    client = TestClient(app)
    response = client.get("/rss/rss.xml")

    assert response.status_code == 200, response.text
    assert "<title>Tom &amp; Jerry &lt;launch&gt;</title>" in response.text
    assert "<description>Tom &amp; Jerry &lt;launch&gt;</description>" in response.text
    assert "<link>https://example.test/?a=1&amp;b=2</link>" in response.text
    assert client.get(f"/rss/{'a' * 129}.xml").status_code == 404


def test_app_selfhost_rss_feed_is_disabled_when_multiple_tenants_exist(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    session = SESSION_LOCAL()
    try:
        now = datetime.now(UTC)
        session.add_all(
            [
                TenantOrm(id="10000000-0000-4000-8000-000000000201", name="One", created_at=now, updated_at=now),
                TenantOrm(id="10000000-0000-4000-8000-000000000202", name="Two", created_at=now, updated_at=now),
                RssFeedItemOrm(
                    feed_id="rss",
                    source_channel="postbridge-local",
                    source_post_id="post-tenant",
                    text="Tenant scoped post",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    client = TestClient(app)
    assert client.get("/rss/rss.xml").status_code == 404


def test_app_selfhost_published_post_fans_out_to_rss_bridge(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000061")
    queued: list[dict] = []
    monkeypatch.setattr(
        app_public,
        "queue_live_sync_publish",
        lambda **kwargs: queued.append(kwargs),
    )
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    channels = client.get("/api/app/channels").json()["items"]
    source = next(item for item in channels if item["platform"] == "postbridge")
    target = client.post(
        "/api/app/channels",
        json={
            "platform": "rss",
            "kind": "destination",
            "title": "RSS",
            "external_id": "rss",
            "can_write": True,
        },
    ).json()
    bridge = client.post(
        "/api/app/bridges",
        json={
            "source_channel_id": source["id"],
            "target_channel_id": target["id"],
            "mode": "live_sync",
            "status": "active",
        },
    )
    assert bridge.status_code == 200, bridge.text

    created = client.post(
        "/api/app/content-items",
        json={
            "content_md": "Hello RSS",
            "title": "RSS title",
            "link_url": "https://example.test/post",
            "status": "published",
        },
    )

    assert created.status_code == 200
    assert len(queued) == 1
    assert queued[0]["source_channel"] == "postbridge-local"
    assert queued[0]["target_channel"] == "rss"
    assert queued[0]["target_platform"] == "rss"
    assert queued[0]["post"]["source_post_id"] == created.json()["id"]
    assert "RSS title" in queued[0]["post"]["text"]
    assert "Hello RSS" in queued[0]["post"]["text"]
    assert queued[0]["post"]["text"].count("https://example.test/post") == 1


def test_app_selfhost_channel_capabilities_parse_string_flags():
    from postbridge.api import app_public

    read_disabled = ChannelOrm(
        id="channel-read-disabled",
        tenant_id="tenant",
        platform="rss",
        external_id="https://example.com/feed.xml",
        title="RSS source",
        kind="source",
        capabilities_json=json.dumps({"can_read": "false", "can_write": "0"}),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    write_enabled = ChannelOrm(
        id="channel-write-enabled",
        tenant_id="tenant",
        platform="rss",
        external_id="rss",
        title="RSS target",
        kind="source",
        capabilities_json=json.dumps({"can_read": "yes", "can_write": "true"}),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    assert app_public._channel_can_read(read_disabled) is False
    assert app_public._channel_can_write(read_disabled) is False
    assert app_public._channel_public_dict(read_disabled)["can_read"] is False
    assert app_public._channel_public_dict(read_disabled)["can_write"] is False
    assert app_public._channel_can_read(write_enabled) is True
    assert app_public._channel_can_write(write_enabled) is True


def test_app_selfhost_create_publish_rolls_back_when_live_sync_queue_fails(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000161")

    def fail_queue(**kwargs):
        _ = kwargs
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(app_public, "queue_live_sync_publish", fail_queue)
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    channels = client.get("/api/app/channels").json()["items"]
    source = next(item for item in channels if item["platform"] == "postbridge")
    target = client.post(
        "/api/app/channels",
        json={
            "platform": "rss",
            "kind": "destination",
            "title": "RSS",
            "external_id": "rss",
            "can_write": True,
        },
    ).json()
    assert client.post(
        "/api/app/bridges",
        json={
            "source_channel_id": source["id"],
            "target_channel_id": target["id"],
            "mode": "live_sync",
            "status": "active",
        },
    ).status_code == 200

    created = client.post(
        "/api/app/content-items",
        json={
            "content_md": "Hello RSS",
            "content_plain": "Plain RSS",
            "media_url": "https://media.example/create.jpg",
            "media_urls": ["https://media.example/create.jpg"],
            "title": "Queue failure",
            "summary": "Create summary",
            "link_url": "https://example.test/create",
            "cta": "Create CTA",
            "tags": ["create", "failed"],
            "author": "Create author",
            "cover_image_url": "https://media.example/create-cover.jpg",
            "status": "published",
        },
    )

    assert created.status_code == 503
    session = SESSION_LOCAL()
    try:
        row = session.scalar(
            select(ContentItemOrm).where(
                ContentItemOrm.tenant_id == "10000000-0000-4000-8000-000000000161",
                ContentItemOrm.title == "Queue failure",
            )
        )
        assert row is not None
        assert row.status == "draft"
        assert row.media_url == "https://media.example/create.jpg"
        assert row.media_urls == ["https://media.example/create.jpg"]
        assert "published_at" not in (row.body_structured_json or "")
        restored = content_item_to_api_dict(row)
        assert restored["content_plain"] == "Plain RSS"
        assert restored["summary"] == "Create summary"
        assert restored["link_url"] == "https://example.test/create"
        assert restored["cta"] == "Create CTA"
        assert restored["tags"] == ["create", "failed"]
        assert restored["author"] == "Create author"
        assert restored["cover_image_url"] == "https://media.example/create-cover.jpg"
    finally:
        session.close()


def test_app_selfhost_patch_publish_rolls_back_when_live_sync_queue_fails(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000162")

    def fail_queue(**kwargs):
        _ = kwargs
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(app_public, "queue_live_sync_publish", fail_queue)
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    channels = client.get("/api/app/channels").json()["items"]
    source = next(item for item in channels if item["platform"] == "postbridge")
    target = client.post(
        "/api/app/channels",
        json={
            "platform": "rss",
            "kind": "destination",
            "title": "RSS",
            "external_id": "rss",
            "can_write": True,
        },
    ).json()
    assert client.post(
        "/api/app/bridges",
        json={
            "source_channel_id": source["id"],
            "target_channel_id": target["id"],
            "mode": "live_sync",
            "status": "active",
        },
    ).status_code == 200
    item = client.post(
        "/api/app/content-items",
        json={
            "content_md": "Draft body",
            "content_plain": "Plain draft",
            "media_url": "https://media.example/old.jpg",
            "media_urls": ["https://media.example/old.jpg", "https://media.example/old-2.jpg"],
            "title": "Draft title",
            "summary": "Old summary",
            "link_url": "https://example.test/old",
            "cta": "Old CTA",
            "tags": ["old", "draft"],
            "author": "Old author",
            "cover_image_url": "https://media.example/cover-old.jpg",
            "status": "draft",
        },
    ).json()

    patched = client.patch(
        f"/api/app/content-items/{item['id']}",
        json={
            "content_md": "Published body",
            "content_plain": "Plain published",
            "media_url": "https://media.example/new.jpg",
            "media_urls": ["https://media.example/new.jpg"],
            "title": "Published title",
            "summary": "New summary",
            "link_url": "https://example.test/new",
            "cta": "New CTA",
            "tags": ["new", "published"],
            "author": "New author",
            "cover_image_url": "https://media.example/cover-new.jpg",
            "status": "published",
        },
    )

    assert patched.status_code == 503
    session = SESSION_LOCAL()
    try:
        row = session.get(ContentItemOrm, item["id"])
        assert row is not None
        assert row.status == "draft"
        assert row.title == "Draft title"
        assert row.body_markdown == "Draft body"
        assert row.media_url == "https://media.example/old.jpg"
        assert row.media_urls == ["https://media.example/old.jpg", "https://media.example/old-2.jpg"]
        assert "published_at" not in (row.body_structured_json or "")
        restored = content_item_to_api_dict(row)
        assert restored["content_plain"] == "Plain draft"
        assert restored["summary"] == "Old summary"
        assert restored["link_url"] == "https://example.test/old"
        assert restored["cta"] == "Old CTA"
        assert restored["tags"] == ["old", "draft"]
        assert restored["author"] == "Old author"
        assert restored["cover_image_url"] == "https://media.example/cover-old.jpg"
    finally:
        session.close()


def test_app_selfhost_partial_live_sync_enqueue_keeps_published_state(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000163")
    queued: list[dict] = []

    def fail_second_queue(**kwargs):
        if queued:
            raise RuntimeError("second queue unavailable")
        queued.append(kwargs)

    monkeypatch.setattr(app_public, "queue_live_sync_publish", fail_second_queue)
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    channels = client.get("/api/app/channels").json()["items"]
    source = next(item for item in channels if item["platform"] == "postbridge")
    targets = []
    for feed_id in ("rss-a", "rss-b"):
        targets.append(
            client.post(
                "/api/app/channels",
                json={
                    "platform": "rss",
                    "kind": "destination",
                    "title": feed_id,
                    "external_id": feed_id,
                    "can_write": True,
                },
            ).json()
        )
    for target in targets:
        assert client.post(
            "/api/app/bridges",
            json={
                "source_channel_id": source["id"],
                "target_channel_id": target["id"],
                "mode": "live_sync",
                "status": "active",
            },
        ).status_code == 200

    created = client.post(
        "/api/app/content-items",
        json={
            "content_md": "Partial publish",
            "title": "Partial live sync",
            "status": "published",
        },
    )

    assert created.status_code == 200, created.text
    assert len(queued) == 1
    assert created.json()["live_sync_warning"] == {
        "code": "live_sync_partial_queue_failure",
        "message": "Some live sync targets were not queued. Check channels and retry missing targets manually.",
        "queued_count": 1,
        "failed_count": 1,
    }
    session = SESSION_LOCAL()
    try:
        row = session.scalar(
            select(ContentItemOrm).where(
                ContentItemOrm.tenant_id == "10000000-0000-4000-8000-000000000163",
                ContentItemOrm.title == "Partial live sync",
            )
        )
        assert row is not None
        assert row.status == "published"
        assert "published_at" in (row.body_structured_json or "")
    finally:
        session.close()


def test_app_selfhost_partial_patch_enqueue_keeps_updated_published_state(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000164")
    queued: list[dict] = []

    def fail_second_queue(**kwargs):
        if queued:
            raise RuntimeError("second queue unavailable")
        queued.append(kwargs)

    monkeypatch.setattr(app_public, "queue_live_sync_publish", fail_second_queue)
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    channels = client.get("/api/app/channels").json()["items"]
    source = next(item for item in channels if item["platform"] == "postbridge")
    for feed_id in ("rss-a", "rss-b"):
        target = client.post(
            "/api/app/channels",
            json={
                "platform": "rss",
                "kind": "destination",
                "title": feed_id,
                "external_id": feed_id,
                "can_write": True,
            },
        ).json()
        assert client.post(
            "/api/app/bridges",
            json={
                "source_channel_id": source["id"],
                "target_channel_id": target["id"],
                "mode": "live_sync",
                "status": "active",
            },
        ).status_code == 200
    item = client.post(
        "/api/app/content-items",
        json={"content_md": "Draft body", "title": "Draft title", "status": "draft"},
    ).json()

    patched = client.patch(
        f"/api/app/content-items/{item['id']}",
        json={
            "content_md": "Published body",
            "media_url": "https://media.example/partial.jpg",
            "media_urls": ["https://media.example/partial.jpg"],
            "title": "Published partial",
            "status": "published",
        },
    )

    assert patched.status_code == 200, patched.text
    assert len(queued) == 1
    assert patched.json()["live_sync_warning"] == {
        "code": "live_sync_partial_queue_failure",
        "message": "Some live sync targets were not queued. Check channels and retry missing targets manually.",
        "queued_count": 1,
        "failed_count": 1,
    }
    session = SESSION_LOCAL()
    try:
        row = session.get(ContentItemOrm, item["id"])
        assert row is not None
        assert row.status == "published"
        assert row.title == "Published partial"
        assert row.body_markdown == "Published body"
        assert row.media_url == "https://media.example/partial.jpg"
        assert row.media_urls == ["https://media.example/partial.jpg"]
        assert "published_at" in (row.body_structured_json or "")
    finally:
        session.close()


def test_app_selfhost_published_post_commits_before_live_sync_adaptation(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000068")
    monkeypatch.setattr(app_public, "queue_live_sync_publish", lambda **kwargs: None)

    def assert_committed_before_adaptation(*args, content_item_id: str | None = None, **kwargs):
        assert content_item_id is not None
        nested = SESSION_LOCAL()
        try:
            committed = nested.get(ContentItemOrm, content_item_id)
            assert committed is not None
            assert committed.status == "published"
        finally:
            nested.close()
        return SimpleNamespace(text="Committed post", status="ready")

    monkeypatch.setattr(app_public, "adapt_post_for_bridge", assert_committed_before_adaptation)
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    channels = client.get("/api/app/channels").json()["items"]
    source = next(item for item in channels if item["platform"] == "postbridge")
    target = client.post(
        "/api/app/channels",
        json={
            "platform": "rss",
            "kind": "destination",
            "title": "RSS",
            "external_id": "rss",
            "can_write": True,
        },
    ).json()
    assert client.post(
        "/api/app/bridges",
        json={
            "source_channel_id": source["id"],
            "target_channel_id": target["id"],
            "mode": "live_sync",
            "status": "active",
        },
    ).status_code == 200

    created = client.post(
        "/api/app/content-items",
        json={"content_md": "Hello RSS", "title": "RSS title", "status": "published"},
    )

    assert created.status_code == 200, created.text


def test_app_selfhost_news_auth_exemption_is_not_prefix_wide(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("POSTBRIDGE_TEST_REQUIRE_AUTH", "1")
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000058")
    client = TestClient(app)
    setup = client.post(
        "/api/app/bootstrap",
        json={"tenant_name": "Local", "admin_username": "owner", "admin_password": "strong-password"},
    )
    assert setup.status_code == 200

    assert client.get("/api/app/news").status_code == 200
    assert client.get("/api/app/news/product-update").status_code == 200
    assert app_public._is_auth_exempt("/api/app/news") is True
    assert app_public._is_auth_exempt("/api/app/news/product-update") is True
    assert app_public._is_auth_exempt("/api/app/newsletter") is False
    assert app_public._is_auth_exempt("/api/app/news/product-update/extra") is False
    assert app_public._is_auth_exempt("/api/app/news-archive/product-update") is False


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
    assert summary.json()["channels_count"] == 3
    assert summary.json()["bridges_count"] == 1
    assert summary.json()["content_items_count"] == 2
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
    assert item["id"] in [row["id"] for row in listed.json()["items"]]

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


def test_app_content_items_published_rejects_schedule(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000057")
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200
    future = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(minutes=10)
    minute_adjust = future.minute % 5
    if minute_adjust:
        future += timedelta(minutes=5 - minute_adjust)

    response = client.post(
        "/api/app/content-items",
        json={
            "content_md": "Ready now",
            "status": "published",
            "scheduled_publish_at": future.isoformat(),
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_SCHEDULE_CONFLICT"


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


def test_app_content_items_ignore_source_on_unscheduled_draft(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000052")
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

    response = client.post(
        "/api/app/content-items",
        json={
            "content_md": "Plain draft",
            "status": "draft",
            "live_sync_source_core_channel_id": source["id"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["scheduled_publish_at"] is None
    assert body["live_sync_source_core_channel_id"] is None
    session = SESSION_LOCAL()
    try:
        row = session.get(ContentItemOrm, body["id"])
        assert row is not None
        assert "live_sync_source_core_channel_id" not in (row.body_structured_json or "")
        row.body_structured_json = json.dumps(
            {"postbridge_extra": {"live_sync_source_core_channel_id": source["id"]}}
        )
        session.commit()
    finally:
        session.close()
    fetched = client.get(f"/api/app/content-items/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["live_sync_source_core_channel_id"] is None


def test_app_content_items_clear_source_when_schedule_removed(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000053")
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
    created = client.post(
        "/api/app/content-items",
        json={
            "content_md": "Scheduled body",
            "status": "draft",
            "scheduled_publish_at": future.isoformat(),
            "live_sync_source_core_channel_id": source["id"],
        },
    ).json()

    patched = client.patch(
        f"/api/app/content-items/{created['id']}",
        json={"scheduled_publish_at": None},
    )

    assert patched.status_code == 200
    assert patched.json()["scheduled_publish_at"] is None
    assert patched.json()["live_sync_source_core_channel_id"] is None


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


def test_app_media_upload_selfhost_uses_local_storage_when_s3_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000066")
    monkeypatch.setenv("MEDIA_STORAGE_TYPE", "s3")
    monkeypatch.setenv("MEDIA_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("MEDIA_BASE_URL", "http://testserver/media")
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.delenv("MEDIA_S3_BUCKET", raising=False)
    client = TestClient(app)
    assert client.post("/api/app/bootstrap", json={"tenant_name": "Local"}).status_code == 200

    response = client.post(
        "/api/app/media/upload",
        files={"file": ("cover.png", BytesIO(b"abc"), "image/png")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["url"].startswith("http://testserver/media/")
    assert (tmp_path / f"tenants/10000000-0000-4000-8000-000000000066/media/{body['media_asset_id']}.png").is_file()


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


def test_app_media_generation_job_allows_installation_ai_gateway(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000024")
    monkeypatch.setenv("AI_GATEWAY_ENABLED", "0")
    client = TestClient(app)
    assert client.post(
        "/api/app/bootstrap",
        json={
            "tenant_name": "Local",
            "installation_secrets": {
                "ai_gateway": {
                    "config": {
                        "base_url": "https://gitsell.test/api/v1",
                        "default_model": "gpt-5.4-mini",
                        "image_model": "gpt-image-2",
                        "image_size": "1536x1024",
                    },
                    "secret": {"api_key": "gsa-test"},
                }
            },
        },
    ).status_code == 200
    queued: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "postbridge.api.app_public.process_media_generation_job_task.delay",
        lambda job_id, correlation_id=None: queued.append((job_id, correlation_id)),
    )

    response = client.post(
        "/api/app/media/generation-jobs",
        json={"target": "cover", "title": "Image post"},
    )

    assert response.status_code == 202, response.text
    assert queued


def test_app_bootstrap_creates_default_agent_provider_from_ai_gateway(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000064")
    client = TestClient(app)

    response = client.post(
        "/api/app/bootstrap",
        json={
            "tenant_name": "Local",
            "installation_secrets": {
                "ai_gateway": {
                    "config": {
                        "base_url": "https://gitsell.test/api/v1",
                        "default_model": "gpt-5.4-mini",
                        "image_model": "gpt-image-2",
                    },
                    "secret": {"api_key": "gsa-test"},
                }
            },
        },
    )

    assert response.status_code == 200, response.text
    session = SESSION_LOCAL()
    try:
        provider = session.scalar(select(LlmProviderConfigOrm))
        assert provider is not None
        assert provider.tenant_id == "10000000-0000-4000-8000-000000000064"
        assert provider.provider_type == "openai_compatible"
        assert provider.base_url == "https://gitsell.test/api/v1"
        assert provider.model_name == "gpt-5.4-mini"
        assert provider.api_key == "gsa-test"
        assert provider.is_default is True
        assert json.loads(provider.capabilities_json or "{}")["image_model"] == "gpt-image-2"
    finally:
        session.close()


def test_app_agent_task_sees_synced_ai_gateway_provider(monkeypatch):
    from postbridge.api import app_public

    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("POSTBRIDGE_SELFHOST_TENANT_ID", "10000000-0000-4000-8000-000000000164")
    monkeypatch.setenv("AI_GATEWAY_ENABLED", "0")
    client = TestClient(app)
    setup = client.post("/api/app/bootstrap", json={"tenant_name": "Local"})
    assert setup.status_code == 200
    secret = client.put(
        "/api/app/installation-secrets/ai-gateway",
        json={
            "config": {
                "base_url": "https://gitsell.test/api/v1",
                "default_model": "gpt-5.4-mini",
            },
            "secret": {"api_key": "gsa-test"},
        },
    )
    assert secret.status_code == 200
    channel = client.post(
        "/api/app/channels",
        json={"platform": "postbridge", "kind": "source", "title": "Postbridge Source", "external_id": "source"},
    ).json()

    seen: dict[str, str] = {}

    def fake_create_service_agent_task(body, *, tenant_id, session):
        _ = body
        provider = session.scalar(select(LlmProviderConfigOrm).where(LlmProviderConfigOrm.tenant_id == tenant_id))
        assert provider is not None
        seen["provider_id"] = provider.id
        return {"id": "task-id", "tenant_id": tenant_id, "channel_id": channel["id"], "status": "active"}

    monkeypatch.setattr(app_public, "create_service_agent_task", fake_create_service_agent_task)

    response = client.post(
        "/api/app/agent/tasks",
        json={
            "channel_id": channel["id"],
            "mode": "topic_scout",
            "goal_text": "Find timely engineering topics",
            "max_candidates_per_run": 2,
            "autonomy_mode": "draft_approval",
            "task_config": {},
            "search_image_mode": "none",
            "created_by": "local-admin",
        },
    )

    assert response.status_code == 200, response.text
    assert seen["provider_id"]


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
            "platform": "max",
            "kind": "destination",
            "title": "MAX",
            "external_id": "max-chat-1",
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
        assert "[adapt:max]" in (adapt_rv.body_text or "")
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
        json={"platform": "max", "kind": "destination", "title": "MAX", "external_id": "max-chat-1"},
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
        json={"platform": "max", "kind": "destination", "title": "MAX", "external_id": "max-chat-1"},
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
        json={"platform": "max", "kind": "destination", "title": "MAX", "external_id": "max-chat-1"},
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
