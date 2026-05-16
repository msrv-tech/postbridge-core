"""Internal AI service API и доменный слой ai_content."""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

os.environ["AI_GATEWAY_ENABLED"] = "1"

from postbridge.api.main import app  # noqa: E402
from postbridge.db import Base, ENGINE, SESSION_LOCAL, BatchImportRunOrm, init_db  # noqa: E402
from postbridge.models.domain import (
    ContentItemAiChatMessageOrm,
    ContentItemOrm,
    RenderVariantOrm,
)  # noqa: E402
from postbridge.services.publication_planning import create_content_with_plan_and_targets  # noqa: E402
from postbridge.models.domain import ChannelOrm, TenantOrm  # noqa: E402

TENANT = "a0000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def _echo_ai_gateway_for_tests(monkeypatch):
    """Generate-тесты рассчитаны на EchoAiGatewayClient, не на реальный OpenAI."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("AI_GATEWAY_BASE_URL", raising=False)


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    session = SESSION_LOCAL()
    session.query(BatchImportRunOrm).delete()
    session.add(TenantOrm(id=TENANT, name="t"))
    session.commit()
    session.close()
    yield


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _svc_headers(**kwargs: str) -> dict[str, str]:
    h = {
        "Authorization": "Bearer svc-test-secret",
        "X-Tenant-Id": TENANT,
        "X-Correlation-Id": "corr-ai-1",
    }
    if kwargs.get("idempotency_key"):
        h["X-Idempotency-Key"] = kwargs["idempotency_key"]
    return h


def _seed_publication() -> tuple[str, str]:
    session = SESSION_LOCAL()
    ch_id = str(uuid4())
    session.add(
        ChannelOrm(
            id=ch_id,
            tenant_id=TENANT,
            platform="telegram",
            kind="destination",
            title="TG",
            external_id="@x",
            status="connected",
            capabilities_json='{"max_length": 4096}',
        )
    )
    session.commit()
    session.close()
    session = SESSION_LOCAL()
    r = create_content_with_plan_and_targets(
        session,
        tenant_id=TENANT,
        channel_ids=[ch_id],
        title="T",
        body_markdown="Hello world",
        target_status="pending",
    )
    session.commit()
    cid = r.content_item_id
    session.close()
    return cid, ch_id


def test_adapt_creates_ai_render_variant_and_updates_target(client: TestClient):
    content_id, ch_id = _seed_publication()
    r = client.post(
        f"/internal/service/content-items/{content_id}/adapt",
        headers=_svc_headers(),
        json={"channel_id": ch_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["operation"] == "adapt"
    assert body["content_item_id"] == content_id
    assert body["render_variant_id"]

    session = SESSION_LOCAL()
    rv = session.get(RenderVariantOrm, body["render_variant_id"])
    assert rv is not None
    assert rv.created_by == "ai"
    assert "[adapt:telegram]" in (rv.body_text or "")
    session.close()


def test_adapt_idempotency_returns_same_payload(client: TestClient):
    content_id, ch_id = _seed_publication()
    key = "idem-adapt-1"
    r1 = client.post(
        f"/internal/service/content-items/{content_id}/adapt",
        headers=_svc_headers(idempotency_key=key),
        json={"channel_id": ch_id},
    )
    assert r1.status_code == 200
    p1 = r1.json()
    r2 = client.post(
        f"/internal/service/content-items/{content_id}/adapt",
        headers=_svc_headers(idempotency_key=key),
        json={"channel_id": ch_id},
    )
    assert r2.status_code == 200
    assert r2.json() == p1


def test_translate_endpoint(client: TestClient):
    content_id, ch_id = _seed_publication()
    r = client.post(
        f"/internal/service/content-items/{content_id}/translate",
        headers=_svc_headers(),
        json={"channel_id": ch_id, "target_language": "de"},
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["operation"] == "translate"
    s = SESSION_LOCAL()
    try:
        rv = s.get(RenderVariantOrm, j["render_variant_id"])
        assert rv is not None
        assert "[translate:de]" in (rv.body_text or "")
    finally:
        s.close()


def test_generate_without_channels(client: TestClient):
    r = client.post(
        "/internal/service/content-items/generate",
        headers=_svc_headers(),
        json={"prompt": "Write about cats"},
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["operation"] == "generate"
    assert j["content_item_id"]
    assert j["publication_plan_id"] is None
    assert j.get("generated_title")
    assert j.get("generated_body_markdown")
    assert j.get("usage_tokens_charged") == 1


def test_generate_stream_yields_delta_and_done(client: TestClient):
    with client.stream(
        "POST",
        "/internal/service/content-items/generate-stream",
        headers=_svc_headers(),
        json={"prompt": "Write about cats"},
    ) as r:
        assert r.status_code == 200
        body = b"".join(r.iter_bytes()).decode("utf-8")
    assert '"type": "delta"' in body or '"type":"delta"' in body
    assert "done" in body
    assert "content_item_id" in body


def test_generate_messages_without_content_item_id_persists_ai_chat(client: TestClient):
    """Первый запрос с messages без content_item_id создаёт пост — чат должен сохраниться под новым id."""
    r = client.post(
        "/internal/service/content-items/generate",
        headers=_svc_headers(),
        json={"messages": [{"role": "user", "content": "first turn"}]},
    )
    assert r.status_code == 200, r.text
    cid = r.json()["content_item_id"]
    session = SESSION_LOCAL()
    try:
        chat_rows = (
            session.query(ContentItemAiChatMessageOrm)
            .filter(ContentItemAiChatMessageOrm.content_item_id == cid)
            .all()
        )
        assert len(chat_rows) == 2
        assert {row.role for row in chat_rows} == {"user", "assistant"}
        user_row = next(x for x in chat_rows if x.role == "user")
        assert "first turn" in (user_row.content or "")
    finally:
        session.close()


def test_generate_with_content_item_id_refines(client: TestClient):
    r1 = client.post(
        "/internal/service/content-items/generate",
        headers=_svc_headers(),
        json={"prompt": "first draft"},
    )
    assert r1.status_code == 200, r1.text
    cid = r1.json()["content_item_id"]
    r2 = client.post(
        "/internal/service/content-items/generate",
        headers=_svc_headers(),
        json={
            "messages": [{"role": "user", "content": "make it shorter"}],
            "content_item_id": cid,
        },
    )
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["content_item_id"] == cid
    assert j2.get("generated_title") == "Generated"
    assert "[generate-chat]" in (j2.get("generated_body_markdown") or "")
    session = SESSION_LOCAL()
    try:
        ci = session.get(ContentItemOrm, cid)
        assert ci is not None
        assert "[generate-chat]" in (ci.body_markdown or "")
        assert ci.title == "Generated"
        chat_rows = (
            session.query(ContentItemAiChatMessageOrm)
            .filter(ContentItemAiChatMessageOrm.content_item_id == cid)
            .all()
        )
        assert len(chat_rows) == 2
        assert {r.role for r in chat_rows} == {"user", "assistant"}
    finally:
        session.close()

    r_chat = client.get(
        f"/internal/service/content-items/postbridge/{cid}/ai-chat",
        headers=_svc_headers(),
    )
    assert r_chat.status_code == 200, r_chat.text
    cm = r_chat.json().get("messages") or []
    assert len(cm) == 2


def test_gateway_text_response_usage_total_tokens() -> None:
    from postbridge.ai.schemas import (
        GatewayTextResponse,
        GatewayUsageStats,
        gateway_raw_total_tokens,
        usage_tokens_charged_for_billing,
    )

    r = GatewayTextResponse(
        body_text="x",
        usage=GatewayUsageStats(total_tokens=42),
    )
    assert gateway_raw_total_tokens(r) == 42
    assert usage_tokens_charged_for_billing(r) == 42

    r2 = GatewayTextResponse(body_text="x", total_tokens=7)
    assert gateway_raw_total_tokens(r2) == 7

    r3 = GatewayTextResponse.model_validate(
        {"body_text": "x", "usage": {"total_tokens": 100, "prompt_tokens": 40}}
    )
    assert usage_tokens_charged_for_billing(r3) == 100

    r4 = GatewayTextResponse(body_text="x")
    assert gateway_raw_total_tokens(r4) is None
    assert usage_tokens_charged_for_billing(r4) == 1


def test_openai_chat_completion_maps_to_gateway_response() -> None:
    from postbridge.ai.client import parse_openai_chat_completion_to_gateway_response

    r = parse_openai_chat_completion_to_gateway_response(
        {
            "choices": [
                {"message": {"role": "assistant", "content": "  hello  "}},
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 2,
                "total_tokens": 3,
            },
        }
    )
    assert r.body_text == "hello"
    assert r.usage is not None
    assert r.usage.total_tokens == 3


def test_usage_beats_flat_total_when_both_set() -> None:
    from postbridge.ai.schemas import (
        GatewayTextResponse,
        GatewayUsageStats,
        gateway_raw_total_tokens,
    )

    r = GatewayTextResponse(
        body_text="x",
        usage=GatewayUsageStats(total_tokens=10),
        total_tokens=999,
    )
    assert gateway_raw_total_tokens(r) == 10


def test_ai_disabled_returns_422(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    monkeypatch.setenv("AI_GATEWAY_ENABLED", "0")
    content_id, ch_id = _seed_publication()
    r = client.post(
        f"/internal/service/content-items/{content_id}/adapt",
        headers=_svc_headers(),
        json={"channel_id": ch_id},
    )
    assert r.status_code == 422
    monkeypatch.setenv("AI_GATEWAY_ENABLED", "1")
