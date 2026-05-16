"""Bridge-level adaptation service and internal contract."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from postbridge.db import Base, ENGINE, SESSION_LOCAL, init_db
from postbridge.models.domain import AgentRunOrm, ChannelOrm, TenantOrm
from postbridge.services.bridge_adaptation import adapt_post_for_bridge


@pytest.fixture(autouse=True)
def reset_db() -> None:
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    yield


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        "postbridge.services.bridge_adaptation._default_generator",
        lambda **_kw: ("Reviewed service text", {"total_tokens": 5}),
    )
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


def test_bridge_adaptation_rule_only_uses_registry_text() -> None:
    session = SESSION_LOCAL()
    try:
        result = adapt_post_for_bridge(
            session,
            tenant_id=str(uuid4()),
            post={"title": "Hello", "text": "World"},
            platform="telegram",
            bridge_settings={"adaptation_mode": "rule_only"},
        )
    finally:
        session.close()

    assert result.text == "Hello\n\nWorld"
    assert result.status == "ready"
    assert result.mode == "rule_only"
    assert result.fallback_used is False


def test_bridge_adaptation_ai_auto_accepts_valid_agent_text() -> None:
    tenant_id = str(uuid4())
    target_channel_id = str(uuid4())
    session = SESSION_LOCAL()
    try:
        session.add(TenantOrm(id=tenant_id, name="Tenant"))
        session.flush()
        session.add(
            ChannelOrm(
                id=target_channel_id,
                tenant_id=tenant_id,
                platform="max",
                kind="destination",
                title="MAX",
                status="connected",
            )
        )
        session.flush()
        result = adapt_post_for_bridge(
            session,
            tenant_id=tenant_id,
            post={"title": "Long source", "text": "Source body"},
            platform="max",
            bridge_settings={
                "adaptation": {
                    "mode": "ai_auto",
                    "instructions": "Make it short.",
                }
            },
            target_channel_id=target_channel_id,
            generator=lambda **_kw: ("Short MAX text", {"total_tokens": 12}),
        )
        session.commit()
        run = session.get(AgentRunOrm, result.run_id)
    finally:
        session.close()

    assert result.text == "Short MAX text"
    assert result.status == "ready"
    assert result.mode == "ai_auto"
    assert result.fallback_used is False
    assert result.token_usage == {"total_tokens": 12}
    assert run is not None
    assert run.graph_name == "bridge_adapt"
    assert run.status == "completed"


def test_bridge_adaptation_ai_auto_trims_agent_text_when_agent_text_is_too_long() -> None:
    session = SESSION_LOCAL()
    try:
        result = adapt_post_for_bridge(
            session,
            tenant_id=str(uuid4()),
            post={"title": "T", "text": "B"},
            platform="max",
            bridge_settings={"adaptation_mode": "ai_auto"},
            generator=lambda **_kw: (
                "AI adapted text " + "x " * 3000,
                {"total_tokens": 20},
            ),
        )
    finally:
        session.close()

    assert len(result.text) <= 4000
    assert result.text.startswith("AI adapted text")
    assert result.fallback_used is False
    assert result.reason == "agent_text_trimmed_to_limit"


def test_bridge_adaptation_ai_auto_falls_back_when_agent_text_is_empty() -> None:
    session = SESSION_LOCAL()
    try:
        result = adapt_post_for_bridge(
            session,
            tenant_id=str(uuid4()),
            post={"title": "T", "text": "B"},
            platform="max",
            bridge_settings={"adaptation_mode": "ai_auto"},
            generator=lambda **_kw: ("   ", {"total_tokens": 20}),
        )
    finally:
        session.close()

    assert result.text == "T\n\nB"
    assert result.fallback_used is True
    assert result.reason == "agent_empty_text"


def test_bridge_adaptation_ai_review_marks_agent_text_as_needs_review() -> None:
    session = SESSION_LOCAL()
    try:
        result = adapt_post_for_bridge(
            session,
            tenant_id=str(uuid4()),
            post={"title": "T", "text": "B"},
            platform="telegram",
            bridge_settings={"adaptation_mode": "ai_review"},
            generator=lambda **_kw: ("AI review text", {"total_tokens": 7}),
        )
    finally:
        session.close()

    assert result.status == "needs_review"
    assert result.text == "AI review text"
    assert result.token_usage == {"total_tokens": 7}
    assert result.reason == "ai_review_requires_human_approval"


def test_service_adapt_post_for_bridge_rule_only(client: TestClient) -> None:
    tid = str(uuid4())
    r = client.post(
        "/internal/service/platforms/adapt-post-for-bridge",
        json={
            "post": {"title": "A", "text": "B"},
            "platform": "telegram",
            "bridge_settings": {"adaptation_mode": "rule_only"},
        },
        headers=_headers(tid),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["text"] == "A\n\nB"
    assert data["status"] == "ready"
    assert data["mode"] == "rule_only"
    assert data["limit"] == 4096


def test_service_adapt_post_for_bridge_accepts_target_channel_scope(client: TestClient) -> None:
    tid = str(uuid4())
    target_channel_id = str(uuid4())
    session = SESSION_LOCAL()
    try:
        session.add(TenantOrm(id=tid, name="Tenant"))
        session.flush()
        session.add(
            ChannelOrm(
                id=target_channel_id,
                tenant_id=tid,
                platform="max",
                kind="destination",
                title="MAX",
                status="connected",
            )
        )
        session.commit()
    finally:
        session.close()

    r = client.post(
        "/internal/service/platforms/adapt-post-for-bridge",
        json={
            "post": {"title": "A", "text": "B"},
            "platform": "max",
            "bridge_settings": {"adaptation_mode": "ai_review"},
            "target_channel_id": target_channel_id,
        },
        headers=_headers(tid),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "needs_review"
    assert data["mode"] == "ai_review"
    assert data["token_usage"] == {"total_tokens": 5}
