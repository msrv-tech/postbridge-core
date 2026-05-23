"""Internal live-sync API: edit/delete/fetch behavior coverage."""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from postbridge.db import Base, ENGINE, PublishedPostOrm, init_db  # noqa: E402
from postbridge.domain.errors import ExternalApiError, ValidationError  # noqa: E402
from postbridge.infrastructure.crypto.credentials import encrypt_credential_secret  # noqa: E402
from postbridge.models.domain import ChannelCredentialOrm, ChannelOrm, TenantOrm  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    yield


@pytest.fixture()
def client() -> TestClient:
    from postbridge.api.main import app

    return TestClient(app)


def _db_session():
    return __import__("postbridge.db", fromlist=["SESSION_LOCAL"]).SESSION_LOCAL()


def _make_tenant_and_channel(*, platform: str) -> tuple[str, str]:
    tenant_id = str(uuid4())
    channel_id = str(uuid4())
    session = _db_session()
    try:
        session.add(TenantOrm(id=tenant_id, name="T"))
        session.flush()
        session.add(
            ChannelOrm(
                id=channel_id,
                tenant_id=tenant_id,
                platform=platform,
                kind="target",
                title=f"{platform} channel",
                external_id="ext-1",
                status="active",
                config_json=None,
                capabilities_json=None,
            )
        )
        session.commit()
        return tenant_id, channel_id
    finally:
        session.close()


def _set_channel_credential(*, tenant_id: str, channel_id: str, payload: dict) -> None:
    session = _db_session()
    try:
        session.add(
            ChannelCredentialOrm(
                id=str(uuid4()),
                tenant_id=tenant_id,
                channel_id=channel_id,
                auth_type="api_key",
                encrypted_secret=encrypt_credential_secret(json.dumps(payload)),
                refresh_token=None,
                expires_at=None,
                meta_json=None,
                status="active",
            )
        )
        session.commit()
    finally:
        session.close()


def _claim_published_post(
    *,
    source_channel: str,
    source_post_id: str,
    target_channel: str,
    max_message_id: str | None,
) -> None:
    session = _db_session()
    try:
        session.add(
            PublishedPostOrm(
                source_channel=source_channel,
                source_post_id=source_post_id,
                target_channel=target_channel,
                max_message_id=max_message_id,
            )
        )
        session.commit()
    finally:
        session.close()


def test_edit_single_vk_group_auth_fallback_republishes_and_updates_tracking(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, channel_id = _make_tenant_and_channel(platform="vk")
    _set_channel_credential(
        tenant_id=tenant_id,
        channel_id=channel_id,
        payload={"access_token": "vk-access-token"},
    )
    _claim_published_post(
        source_channel="pb/ws-1",
        source_post_id="post-1",
        target_channel="vk-target",
        max_message_id="vk-old",
    )

    class _FakeVkPublisher:
        def __init__(self) -> None:
            self.deleted: list[tuple[str, str]] = []
            self.published: list[tuple[str, str]] = []

        def edit_message(self, **_kwargs):
            raise ExternalApiError(
                code="EXTERNAL_API_VK_ERROR",
                message="group auth",
                source="vk",
                retryable=False,
            )

        def delete_post(self, *, target_channel: str, post_id: str, credentials):
            self.deleted.append((target_channel, post_id))
            raise ExternalApiError(
                code="EXTERNAL_API_VK_ERROR",
                message="group auth",
                source="vk",
                retryable=False,
            )

        def publish_post(self, *, target_channel: str, payload, credentials):
            self.published.append((target_channel, payload.source_post_id))
            return "vk-new"

    fake_publisher = _FakeVkPublisher()
    monkeypatch.setattr("postbridge.api.live_sync.get_publisher", lambda _p: fake_publisher)
    monkeypatch.setattr(
        "postbridge.api.live_sync.get_platform_capabilities",
        lambda _p: SimpleNamespace(live_sync_publish_supported=True),
    )

    response = client.post(
        "/internal/sync/edit-single",
        json={
            "source_channel": "pb/ws-1",
            "target_channel": "vk-target",
            "post": {"source_post_id": "post-1", "text": "Hi"},
            "target_platform": "vk",
            "tenant_id": tenant_id,
            "target_core_channel_id": channel_id,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"
    assert fake_publisher.deleted == [("vk-target", "vk-old")]
    assert fake_publisher.published == [("vk-target", "post-1")]

    session = _db_session()
    try:
        row = session.query(PublishedPostOrm).filter_by(
            source_channel="pb/ws-1",
            source_post_id="post-1",
            target_channel="vk-target",
        ).one()
        assert row.max_message_id == "vk-new"
    finally:
        session.close()


def test_edit_single_non_vk_external_error_returns_502(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, channel_id = _make_tenant_and_channel(platform="max")
    _set_channel_credential(
        tenant_id=tenant_id,
        channel_id=channel_id,
        payload={"base_url": "https://max.example", "token": "t"},
    )
    _claim_published_post(
        source_channel="pb/ws-1",
        source_post_id="post-2",
        target_channel="max-target",
        max_message_id="max-1",
    )

    class _FakeMaxPublisher:
        def edit_message(self, **_kwargs):
            raise ExternalApiError(
                code="EXTERNAL_API_MAX_ERROR",
                message="rate limit",
                source="max",
                retryable=True,
            )

    monkeypatch.setattr("postbridge.api.live_sync.get_publisher", lambda _p: _FakeMaxPublisher())
    monkeypatch.setattr(
        "postbridge.api.live_sync.get_platform_capabilities",
        lambda _p: SimpleNamespace(live_sync_publish_supported=True),
    )

    response = client.post(
        "/internal/sync/edit-single",
        json={
            "source_channel": "pb/ws-1",
            "target_channel": "max-target",
            "post": {"source_post_id": "post-2", "text": "Hi"},
            "target_platform": "max",
            "tenant_id": tenant_id,
            "target_core_channel_id": channel_id,
        },
    )
    assert response.status_code == 502, response.text
    assert response.json()["code"] == "HTTP_ERROR"
    assert response.json()["message"] == "rate limit"


def test_delete_single_passes_credentials_when_supported_by_signature(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, channel_id = _make_tenant_and_channel(platform="max")
    _set_channel_credential(
        tenant_id=tenant_id,
        channel_id=channel_id,
        payload={"base_url": "https://max.example", "token": "t"},
    )
    _claim_published_post(
        source_channel="pb/ws-1",
        source_post_id="post-3",
        target_channel="max-target",
        max_message_id="max-3",
    )

    captured: dict = {}

    class _FakeMaxPublisher:
        def delete_message(self, *, message_id: str, credentials):
            captured["message_id"] = message_id
            captured["has_credentials"] = credentials is not None

    monkeypatch.setattr("postbridge.api.live_sync.get_publisher", lambda _p: _FakeMaxPublisher())
    monkeypatch.setattr(
        "postbridge.api.live_sync.get_platform_capabilities",
        lambda _p: SimpleNamespace(live_sync_publish_supported=True),
    )

    response = client.post(
        "/internal/sync/delete-single",
        json={
            "source_channel": "pb/ws-1",
            "target_channel": "max-target",
            "source_post_id": "post-3",
            "target_platform": "max",
            "tenant_id": tenant_id,
            "target_core_channel_id": channel_id,
        },
    )
    assert response.status_code == 200, response.text
    assert captured == {"message_id": "max-3", "has_credentials": True}


def test_delete_single_omits_credentials_when_signature_does_not_accept_it(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, channel_id = _make_tenant_and_channel(platform="max")
    _set_channel_credential(
        tenant_id=tenant_id,
        channel_id=channel_id,
        payload={"base_url": "https://max.example", "token": "t"},
    )
    _claim_published_post(
        source_channel="pb/ws-1",
        source_post_id="post-4",
        target_channel="max-target",
        max_message_id="max-4",
    )

    captured: dict = {}

    class _FakeMaxPublisher:
        def delete_message(self, *, message_id: str):
            captured["message_id"] = message_id

    monkeypatch.setattr("postbridge.api.live_sync.get_publisher", lambda _p: _FakeMaxPublisher())
    monkeypatch.setattr(
        "postbridge.api.live_sync.get_platform_capabilities",
        lambda _p: SimpleNamespace(live_sync_publish_supported=True),
    )

    response = client.post(
        "/internal/sync/delete-single",
        json={
            "source_channel": "pb/ws-1",
            "target_channel": "max-target",
            "source_post_id": "post-4",
            "target_platform": "max",
            "tenant_id": tenant_id,
            "target_core_channel_id": channel_id,
        },
    )
    assert response.status_code == 200, response.text
    assert captured == {"message_id": "max-4"}


def test_fetch_posts_maps_credentials_validation_error_to_422(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, channel_id = _make_tenant_and_channel(platform="rss")
    monkeypatch.setattr(
        "postbridge.api.live_sync.resolve_fetch_credentials_for_core_channel",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            ValidationError(code="VALIDATION", message="missing credentials", details={})
        ),
    )

    response = client.post(
        "/internal/fetch-posts",
        json={
            "source_platform": "rss",
            "source_channel": "https://example.com/feed.xml",
            "limit": 1,
            "tenant_id": tenant_id,
            "source_core_channel_id": channel_id,
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "HTTP_ERROR"
    assert response.json()["message"] == "missing credentials"
