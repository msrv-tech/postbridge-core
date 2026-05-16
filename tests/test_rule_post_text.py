"""Rule-based адаптация текста поста (registry + internal service)."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from postbridge.db import Base, ENGINE, init_db  # noqa: E402
from postbridge.integrations.registry import RULE_POST_TEXT_LIMITS, adapt_post_dict_for_platform


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


def test_adapt_telegram_includes_title_and_body():
    post = {"title": "Hello", "text": "World", "cta": "Go", "link_url": "https://x.example"}
    out = adapt_post_dict_for_platform(post, "telegram")
    assert "Hello" in out
    assert "World" in out
    assert "Go" in out
    assert "https://x.example" in out
    assert len(out) <= RULE_POST_TEXT_LIMITS["telegram"]


def test_adapt_max_includes_summary():
    post = {
        "title": "T",
        "summary": "S",
        "text": "B",
    }
    out = adapt_post_dict_for_platform(post, "max")
    assert "T" in out and "S" in out and "B" in out
    assert len(out) <= RULE_POST_TEXT_LIMITS["max"]


def test_adapt_vk_and_zen():
    long_body = "word " * 500
    vk = adapt_post_dict_for_platform({"text": long_body}, "vk")
    assert len(vk) <= RULE_POST_TEXT_LIMITS["vk"]
    zen = adapt_post_dict_for_platform({"text": long_body}, "zen")
    assert len(zen) <= RULE_POST_TEXT_LIMITS["zen"]


def test_adapt_rss_postbridge_unknown_returns_plain_text():
    post = {"text": "only this", "title": "ignored for rss"}
    assert adapt_post_dict_for_platform(post, "rss") == "only this"
    assert adapt_post_dict_for_platform(post, "postbridge") == "only this"
    assert adapt_post_dict_for_platform(post, "unknown_platform") == "only this"


def test_adapt_non_dict_returns_empty():
    assert adapt_post_dict_for_platform("x", "telegram") == ""  # type: ignore[arg-type]


def test_service_adapt_post_text_ok(client: TestClient):
    tid = str(uuid4())
    r = client.post(
        "/internal/service/platforms/adapt-post-text",
        json={"post": {"title": "A", "text": "B"}, "platform": "telegram"},
        headers=_headers(tid),
    )
    assert r.status_code == 200
    data = r.json()
    assert data.get("text") == "A\n\nB"


def test_service_adapt_post_text_payload_too_large(client: TestClient):
    tid = str(uuid4())
    huge = "x" * 500_001
    r = client.post(
        "/internal/service/platforms/adapt-post-text",
        json={"post": {"text": huge}, "platform": "telegram"},
        headers=_headers(tid),
    )
    assert r.status_code == 422
