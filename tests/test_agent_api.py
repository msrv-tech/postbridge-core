"""Tests for agent runtime API."""

from __future__ import annotations

import json
import os
import base64
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

os.environ["AI_GATEWAY_ENABLED"] = "1"

from postbridge.api.main import app  # noqa: E402
from postbridge.agent.graphs.post_copilot import build_post_copilot_graph  # noqa: E402
from postbridge.agent.tools import (  # noqa: E402
    build_review_hints,
    canonical_angle_family,
    canonical_source_hash,
    classify_source_type,
    dedupe_mixed_list,
    historical_angle_pressure,
    infer_workflow_preset,
    review_action_from_hints,
    score_candidate_against_angles,
    search_similar_publications,
    shortlist_topic_angles,
    shortlist_topic_evidence,
    suggest_review_decision,
    source_conflict_explanations,
    summarize_dedup,
    extract_news_facts,
    validate_platform_constraints,
)
from postbridge.agent.storage import _sanitize_topic_scout_body_markdown  # noqa: E402
from postbridge.agent.providers.openai_compatible import _parse_json_object_loose  # noqa: E402
from postbridge.agent.providers.openai_compatible import OpenAICompatibleProvider  # noqa: E402
from postbridge.agent.policies import AutonomyPolicy, evaluate_policy_guardrails  # noqa: E402
from postbridge.services.ai_image_generation import ImageGenerationResult  # noqa: E402
from postbridge.agent.vector_store import get_vector_store  # noqa: E402
from postbridge.db import Base, ENGINE, SESSION_LOCAL, init_db  # noqa: E402
from postbridge.models.domain import (
    AgentRunOrm,
    AgentPolicyOrm,
    ChannelOrm,
    ContentCandidateOrm,
    ContentEmbeddingOrm,
    ContentItemOrm,
    ContentSourceFingerprintOrm,
    LlmProviderConfigOrm,
    MediaGenerationJobOrm,
    PublicationPlanOrm,
    PublicationTargetOrm,
    ReviewQueueItemOrm,
    TenantOrm,
)  # noqa: E402

TENANT = "b0000000-0000-4000-8000-000000000001"
CHANNEL = "b0000000-0000-4000-8000-000000000002"


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    session = SESSION_LOCAL()
    session.add(TenantOrm(id=TENANT, name="t"))
    session.flush()
    session.add(
        ChannelOrm(
            id=CHANNEL,
            tenant_id=TENANT,
            platform="telegram",
            kind="destination",
            title="TG",
            external_id="@x",
            status="connected",
        )
    )
    session.add(
        LlmProviderConfigOrm(
            id=str(uuid4()),
            tenant_id=TENANT,
            provider_type="openai_compatible",
            label="default",
            base_url="https://example.invalid",
            api_key="secret",
            model_name="gpt-test",
            is_default=True,
        )
    )
    session.commit()
    session.close()
    yield


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    def fake_invoke_json(self, *, messages, temperature=0.2):
        prompt = "\n".join(msg["content"] for msg in messages if msg["role"] == "user")
        if "Return 3 candidates." in prompt:
            return (
                {
                    "candidates": [
                        {
                            "topic": "Novosibirsk daily 1",
                            "headline": "Novosibirsk headline 1",
                            "body_markdown": "Draft 1",
                            "summary": "Summary 1",
                            "why_now": "Because it is new",
                            "style_fit_summary": "Fits channel",
                            "source_bundle": {"primary_sources": ["https://example.com/1"]},
                            "scores": {"relevance": 0.9, "novelty": 0.8, "style_fit": 0.85},
                            "risk_flags": [],
                        },
                        {
                            "topic": "Novosibirsk daily 2",
                            "headline": "Novosibirsk headline 2",
                            "body_markdown": "Draft 2",
                            "summary": "Summary 2",
                            "why_now": "Because it is recent",
                            "style_fit_summary": "Fits channel",
                            "source_bundle": {"primary_sources": ["https://example.com/2"]},
                            "scores": {"relevance": 0.88, "novelty": 0.81, "style_fit": 0.84},
                            "risk_flags": [],
                        },
                        {
                            "topic": "Novosibirsk daily 3",
                            "headline": "Novosibirsk headline 3",
                            "body_markdown": "Draft 3",
                            "summary": "Summary 3",
                            "why_now": "Because it matters today",
                            "style_fit_summary": "Fits channel",
                            "source_bundle": {"primary_sources": ["https://example.com/3"]},
                            "scores": {"relevance": 0.87, "novelty": 0.82, "style_fit": 0.83},
                            "risk_flags": [],
                        },
                    ]
                },
                {"total_tokens": 123},
            )
        if "Use sources" in prompt:
            return (
                {
                    "topic": "Manual topic",
                    "headline": "Manual headline",
                    "body_markdown": "Manual draft",
                    "summary": "Manual summary",
                    "why_now": "Timely",
                    "style_fit_summary": "Good fit",
                    "scores": {"relevance": 0.95, "novelty": 0.9, "style_fit": 0.88},
                    "risk_flags": [],
                },
                {"total_tokens": 42},
            )
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
        lambda self, *, text: (
            [0.9, 0.1, 0.1, 0.1]
            if "manual" in text.lower() or "inspect" in text.lower()
            else [0.8, 0.2, 0.1, 0.1]
            if "novosibirsk" in text.lower()
            else [0.1, 0.2, 0.3, 0.4],
            {"total_tokens": 5},
        ),
    )
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_rerank",
        lambda self, *, query, items, top_k: (
            [
                {"index": 2, "score": 0.99, "reason": "Most timely"},
                {"index": 0, "score": 0.91, "reason": "Strong fit"},
                {"index": 1, "score": 0.88, "reason": "Relevant"},
            ][:top_k],
            {"total_tokens": 7},
        ),
    )
    monkeypatch.setattr(
        "postbridge.agent.orchestrator.find_default_provider",
        lambda session, tenant_id: object(),
    )
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
    monkeypatch.setattr(
        "postbridge.agent.tools.fetch_seed_sources",
        lambda urls: [
            {
                "url": url,
                "title": f"title:{idx}",
                "text_excerpt": f"text:{idx}",
                "image_urls": [f"https://images.example.com/{idx}.jpg"],
                "preview_image_url": f"https://images.example.com/{idx}.jpg",
                "published_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
                "updated_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                "retrieval_backend": "seed_urls",
                "retrieval_score": 1.0,
                "retrieval_reason": "explicit seed url",
            }
            for idx, url in enumerate(urls, start=1)
        ],
    )
    return TestClient(app)


def _svc_headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer svc-test-secret",
        "X-Tenant-Id": TENANT,
        "X-Correlation-Id": "corr-agent-1",
    }


def _seed_agent_run(
    session,
    *,
    run_id: str,
    graph_name: str = "post_copilot",
    status: str = "completed",
    content_item_id: str | None = None,
) -> None:
    session.add(
        AgentRunOrm(
            id=run_id,
            tenant_id=TENANT,
            channel_id=CHANNEL,
            content_item_id=content_item_id,
            graph_name=graph_name,
            trigger_type="api",
            status=status,
            model="gpt-test",
            provider_type="openai_compatible",
        )
    )


def test_search_similar_publications_finds_source_and_headline_matches():
    session = SESSION_LOCAL()
    content_id = str(uuid4())
    plan_id = str(uuid4())
    session.add(
        ContentItemOrm(
            id=content_id,
            tenant_id=TENANT,
            source_type="agent",
            title="City bridge opens today",
            body_markdown="The new city bridge opened after reconstruction.",
            status="published",
        )
    )
    session.add(
        PublicationPlanOrm(
            id=plan_id,
            tenant_id=TENANT,
            content_item_id=content_id,
            strategy="immediate",
            status="published",
        )
    )
    session.add(
        PublicationTargetOrm(
            id=str(uuid4()),
            tenant_id=TENANT,
            publication_plan_id=plan_id,
            channel_id=CHANNEL,
            platform="telegram",
            status="published",
            published_at=datetime.now(UTC),
        )
    )
    session.add(
        ContentSourceFingerprintOrm(
            id=str(uuid4()),
            tenant_id=TENANT,
            channel_id=CHANNEL,
            source_url_hash=canonical_source_hash(
                source_url="https://news.example.com/bridge",
                title=None,
                body_markdown=None,
            ),
            canonical_url="https://news.example.com/bridge",
            published_content_item_id=content_id,
        )
    )
    session.commit()

    result = search_similar_publications(
        session,
        tenant_id=TENANT,
        channel_id=CHANNEL,
        title="City bridge opens today",
        body_markdown="A new city bridge opened after reconstruction.",
        source_url="https://news.example.com/bridge",
    )

    assert result["high_confidence_duplicate"] is True
    assert {item["match_type"] for item in result["matches"]} >= {"source_url", "headline_exact"}
    session.close()


def test_extract_news_facts_returns_structured_source_facts():
    result = extract_news_facts(
        {
            "url": "https://news.example.com/story",
            "title": "Novosibirsk Transport Department Opens New Bridge",
            "text_excerpt": "Novosibirsk officials said the bridge opened on Thursday after repairs.",
            "published_at": "2026-05-07T08:00:00+00:00",
        },
        topic="Novosibirsk bridge news",
    )

    assert result["fact_count"] == 1
    assert result["event_date"] == "2026-05-07T08:00:00+00:00"
    assert "Novosibirsk Transport Department Opens" in result["entities"]
    assert result["facts"][0]["source_url"] == "https://news.example.com/story"


def test_validate_platform_constraints_reports_length_and_media_errors():
    session = SESSION_LOCAL()
    result = validate_platform_constraints(
        session,
        tenant_id=TENANT,
        channel_id=CHANNEL,
        candidate={
            "headline": "Update",
            "body_markdown": "x" * 4100,
            "media_url": "ftp://example.com/file.jpg",
        },
    )

    assert result["ok"] is False
    assert {item["code"] for item in result["errors"]} >= {"text_too_long", "invalid_media_url"}
    assert result["limits"]["text_chars"] == 4096
    session.close()


def test_manual_agent_run_creates_review_items(client: TestClient):
    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Rewrite this for the channel",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "awaiting_review"
    assert len(payload["candidates"]) == 1
    assert len(payload["review_items"]) == 1


def test_post_copilot_timeline_persists_user_messages_and_agent_actions(client: TestClient):
    content_id = str(uuid4())
    session = SESSION_LOCAL()
    try:
        session.add(
            ContentItemOrm(
                id=content_id,
                tenant_id=TENANT,
                author_user_id=None,
                source_type="postbridge",
                title="Draft title",
                body_markdown="Draft body",
                body_structured_json=None,
                language="ru",
                status="draft",
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "content_item_id": content_id,
            "user_request": "Перепиши черновик короче и яснее",
        },
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["agent_run_id"]

    timeline = client.get(
        f"/internal/service/agent/content-items/{content_id}/timeline",
        headers=_svc_headers(),
    )
    assert timeline.status_code == 200, timeline.text
    payload = timeline.json()
    assert payload["content_item_id"] == content_id
    assert payload["content_item"]["id"] == content_id
    assert payload["content_item"]["title"] == "Manual headline"
    assert payload["content_item"]["content_md"] == "Manual draft"
    assert payload["latest_run"]["id"] == run_id
    events = payload["events"]
    assert len(events) >= 5
    assert events[0]["role"] == "user"
    assert events[0]["kind"] == "message"
    assert "Перепиши черновик" in events[0]["content"]
    assert any(
        event["role"] == "system"
        and event["kind"] == "action"
        and "начал обработку" in event["content"]
        for event in events
    )
    assert any(
        event["role"] == "system"
        and event["kind"] == "action"
        and "проверил текущий черновик" in event["content"].lower()
        for event in events
    )
    assert any(
        event["role"] == "assistant"
        and event["kind"] == "result"
        and "обновил текущий черновик" in event["content"].lower()
        for event in events
    )


def test_post_copilot_follow_up_message_reuses_content_item_timeline(client: TestClient):
    content_id = str(uuid4())
    session = SESSION_LOCAL()
    try:
        session.add(
            ContentItemOrm(
                id=content_id,
                tenant_id=TENANT,
                author_user_id=None,
                source_type="postbridge",
                title="Draft title",
                body_markdown="Draft body",
                body_structured_json=None,
                language="ru",
                status="draft",
            )
        )
        session.commit()
    finally:
        session.close()

    first = client.post(
        f"/internal/service/agent/content-items/{content_id}/messages",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "user_request": "Сделай черновик короче",
        },
    )
    assert first.status_code == 200, first.text
    first_payload = first.json()
    first_run_id = first_payload["run"]["agent_run_id"]
    assert first_payload["run"]["id"] == first_run_id
    assert first_payload["run"]["token_usage"]["total_tokens"] >= 1
    first_timeline = first_payload["timeline"]
    assert first_timeline["content_item_id"] == content_id
    assert first_timeline["content_item"]["id"] == content_id
    assert first_timeline["latest_run"]["id"] == first_run_id
    first_events = first_timeline["events"]
    assert any(
        event["role"] == "user" and "Сделай черновик короче" in event["content"]
        for event in first_events
    )

    second = client.post(
        f"/internal/service/agent/content-items/{content_id}/messages",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "user_request": "Теперь сделай тон более нейтральным",
        },
    )
    assert second.status_code == 200, second.text
    second_payload = second.json()
    second_run_id = second_payload["run"]["agent_run_id"]
    assert second_payload["run"]["id"] == second_run_id
    assert second_payload["run"]["token_usage"]["total_tokens"] >= 1
    assert second_run_id != first_run_id
    second_timeline = second_payload["timeline"]
    assert second_timeline["latest_run"]["id"] == second_run_id
    second_events = second_timeline["events"]
    assert len(second_events) > len(first_events)
    user_messages = [
        event["content"]
        for event in second_events
        if event["role"] == "user" and event["kind"] == "message"
    ]
    assert any("Сделай черновик короче" in text for text in user_messages)
    assert any("тон более нейтральным" in text for text in user_messages)


def test_post_copilot_follow_up_message_accepts_list_source_bundle_from_model(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_list_source_bundle(self, *, messages, temperature=0.2):
        return (
            {
                "topic": "Manual topic",
                "headline": "Manual headline",
                "body_markdown": "Обновлённый текст черновика",
                "summary": "Manual summary",
                "why_now": "Timely",
                "style_fit_summary": "Good fit",
                "source_bundle": [
                    {"url": "https://example.com/a", "title": "A"},
                    {"url": "https://example.com/b", "title": "B"},
                ],
                "scores": {"relevance": 0.95},
                "risk_flags": [],
            },
            {"total_tokens": 42},
        )

    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        fake_list_source_bundle,
    )

    content_id = str(uuid4())
    session = SESSION_LOCAL()
    try:
        session.add(
            ContentItemOrm(
                id=content_id,
                tenant_id=TENANT,
                author_user_id=None,
                source_type="postbridge",
                title="Draft title",
                body_markdown="Draft body",
                body_structured_json=None,
                language="ru",
                status="draft",
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.post(
        f"/internal/service/agent/content-items/{content_id}/messages",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "user_request": "Удали последний абзац",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["timeline"]["content_item_id"] == content_id
    assert payload["run"]["id"] == payload["run"]["agent_run_id"]
    assert payload["run"]["token_usage"]["total_tokens"] >= 1
    assert payload["run"]["status"] == "completed"
    assert payload["timeline"]["session_status"] == "completed"


def test_post_copilot_timeline_includes_review_resolution_events(client: TestClient):
    content_id = str(uuid4())
    session = SESSION_LOCAL()
    try:
        session.add(
            ContentItemOrm(
                id=content_id,
                tenant_id=TENANT,
                author_user_id=None,
                source_type="postbridge",
                title="Draft title",
                body_markdown="Draft body",
                body_structured_json=None,
                language="ru",
                status="draft",
            )
        )
        session.commit()
    finally:
        session.close()

    created = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "content_item_id": content_id,
            "user_request": "Перепиши черновик в более деловом тоне",
            "autonomy_mode": "plan_approval",
        },
    )
    assert created.status_code == 200, created.text

    queue = client.get("/internal/service/review-queue", headers=_svc_headers())
    assert queue.status_code == 200, queue.text
    review_item_id = queue.json()[0]["id"]

    resolved = client.post(
        f"/internal/service/review-queue/{review_item_id}/resolve",
        headers=_svc_headers(),
        json={
            "decision": "approved",
            "review_action": "approve_as_is",
            "note": "берём",
            "reviewer_id": "editor-1",
        },
    )
    assert resolved.status_code == 200, resolved.text

    timeline = client.get(
        f"/internal/service/agent/content-items/{content_id}/timeline",
        headers=_svc_headers(),
    )
    assert timeline.status_code == 200, timeline.text
    events = timeline.json()["events"]
    content_item = timeline.json()["content_item"]
    assert content_item["id"] == content_id
    assert content_item["title"] == "Manual headline"
    assert content_item["content_md"] == "Manual draft"
    assert any(
        event["role"] == "system"
        and event["kind"] == "action"
        and "согласовал вариант" in event["content"].lower()
        for event in events
    )
    assert any(
        event["role"] == "assistant"
        and event["kind"] == "result"
        and "исполнитель: editor-1" in event["content"].lower()
        for event in events
    )
    assert any(
        event["role"] == "assistant"
        and event["kind"] == "result"
        and "текущему черновику" in event["content"].lower()
        for event in events
    )

    queue_response = client.get("/internal/service/review-queue", headers=_svc_headers())
    assert queue_response.status_code == 200, queue_response.text
    queue = queue_response.json()
    assert len(queue) == 1
    assert queue[0]["review_payload"]["headline"] == "Manual headline"


def test_seed_urls_are_collected_into_review_payload(client: TestClient):
    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Use sources",
            "seed_urls": ["https://example.com/a", "https://example.com/b"],
        },
    )
    assert response.status_code == 200, response.text
    queue = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    payload = queue[0]["review_payload"]
    assert "seed_sources" in payload["source_bundle"]
    assert len(payload["source_bundle"]["seed_sources"]) == 2
    assert payload["source_bundle"]["seed_sources"][0]["url"] == "https://example.com/a"
    assert payload["source_bundle"]["seed_sources"][0]["retrieval_backend"] == "seed_urls"
    assert payload["source_bundle"]["seed_sources"][0]["retrieval_score"] == 1.0


def test_post_copilot_uses_search_images_when_request_needs_visuals(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "postbridge.agent.graphs.source_package.collect_topic_sources",
        lambda topic, seed_urls=None, **kwargs: [
            {
                "url": "https://example.com/story",
                "title": "Story",
                "text_excerpt": "Story text",
                "image_urls": ["https://images.example.com/story.jpg"],
                "preview_image_url": "https://images.example.com/story.jpg",
                "published_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
                "updated_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                "retrieval_backend": "duckduckgo",
                "retrieval_score": 0.91,
            }
        ],
    )
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        lambda self, *, messages, temperature=0.2: (
            {
                "topic": "Manual topic",
                "headline": "Manual headline",
                "body_markdown": "Manual draft",
                "summary": "Manual summary",
                "why_now": "Timely",
                "style_fit_summary": "Good fit",
                "source_bundle": {"primary_sources": ["https://example.com/story"]},
                "scores": {"relevance": 0.95, "novelty": 0.9, "style_fit": 0.88},
                "risk_flags": [],
            },
            {"total_tokens": 42},
        ),
    )
    session = SESSION_LOCAL()
    try:
        graph = build_post_copilot_graph(
            session=session,
            provider=OpenAICompatibleProvider(
                base_url="https://example.invalid",
                model_name="gpt-test",
                api_key="secret",
            ),
        )
        result = graph.invoke(
            {
                "tenant_id": TENANT,
                "channel_id": CHANNEL,
                "mode": "post_copilot",
                "user_request": "Добавь картинку в пост",
                "image_request": True,
                "topic_definition": None,
                "content_item_id": None,
                "agent_task_id": None,
                "agent_run_id": "run-1",
                "seed_urls": [],
                "tool_trace": [],
                "errors": [],
            }
        )
    finally:
        session.close()

    candidate = result["selected_candidates"][0]
    assert candidate["media_url"] == "https://images.example.com/story.jpg"
    assert candidate["cover_image_url"] == "https://images.example.com/story.jpg"
    assert candidate["media_urls"] == ["https://images.example.com/story.jpg"]
    assert candidate["source_bundle"]["selection_context"]["source_package_summary"]["image_candidate_count"] == 1
    assert candidate["source_bundle"]["package_status"] == "ready"


def test_post_copilot_creates_source_package_review_before_drafting(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    now = datetime.now(UTC)
    monkeypatch.setattr(
        "postbridge.agent.graphs.source_package.collect_topic_sources",
        lambda topic, seed_urls=None, **kwargs: [
            {
                "url": "https://a.example.com/1",
                "title": "A1",
                "text_excerpt": "A1",
                "published_at": (now - timedelta(hours=1)).isoformat(),
                "updated_at": (now - timedelta(minutes=30)).isoformat(),
                "retrieval_backend": "duckduckgo",
                "retrieval_score": 0.95,
                "retrieval_combined_score": 0.95,
                "image_urls": ["https://images.example.com/a1.jpg"],
                "preview_image_url": "https://images.example.com/a1.jpg",
            },
            {
                "url": "https://b.example.com/1",
                "title": "B1",
                "text_excerpt": "B1",
                "published_at": (now - timedelta(hours=2)).isoformat(),
                "updated_at": (now - timedelta(hours=1)).isoformat(),
                "retrieval_backend": "duckduckgo",
                "retrieval_score": 0.88,
                "retrieval_combined_score": 0.88,
            },
        ],
    )
    content_id = str(uuid4())
    session = SESSION_LOCAL()
    try:
        session.add(
            ContentItemOrm(
                id=content_id,
                tenant_id=TENANT,
                source_type="manual",
                title="Old",
                body_markdown="Old body",
                status="draft",
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Find sources for a Saint Petersburg post",
            "content_item_id": content_id,
            "autonomy_mode": "guarded_auto_publish",
            "require_source_approval": True,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "awaiting_review"
    assert payload["review_items"] == []
    assert len(payload["source_package_review_items"]) == 1

    queue = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    assert queue[0]["review_payload"]["kind"] == "source_package"
    assert queue[0]["review_payload"]["source_package"]["selection_context"]["source_package_summary"]["selected_source_count"] == 2
    timeline = client.get(
        f"/internal/service/agent/content-items/{content_id}/timeline",
        headers=_svc_headers(),
    ).json()
    assert "пакет источников" in timeline["events"][-1]["content"].lower()


def test_post_copilot_builds_source_package_subgraph(monkeypatch: pytest.MonkeyPatch):
    now = datetime.now(UTC)
    monkeypatch.setattr(
        "postbridge.agent.graphs.source_package.collect_topic_sources",
        lambda topic, seed_urls=None, **kwargs: [
            {
                "url": "https://a.example.com/1",
                "title": "A1",
                "text_excerpt": "A1",
                "published_at": (now - timedelta(hours=1)).isoformat(),
                "updated_at": (now - timedelta(minutes=30)).isoformat(),
                "retrieval_backend": "duckduckgo",
                "retrieval_score": 0.95,
                "retrieval_combined_score": 0.95,
                "image_urls": ["https://images.example.com/a1.jpg"],
                "preview_image_url": "https://images.example.com/a1.jpg",
            },
            {
                "url": "https://a.example.com/2",
                "title": "A2",
                "text_excerpt": "A2",
                "published_at": (now - timedelta(hours=2)).isoformat(),
                "updated_at": (now - timedelta(hours=1)).isoformat(),
                "retrieval_backend": "duckduckgo",
                "retrieval_score": 0.91,
                "retrieval_combined_score": 0.91,
            },
            {
                "url": "https://b.example.com/1",
                "title": "B1",
                "text_excerpt": "B1",
                "published_at": (now - timedelta(hours=3)).isoformat(),
                "updated_at": (now - timedelta(hours=2)).isoformat(),
                "retrieval_backend": "duckduckgo",
                "retrieval_score": 0.88,
                "retrieval_combined_score": 0.88,
            },
        ],
    )
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        lambda self, *, messages, temperature=0.2: (
            {
                "topic": "Manual topic",
                "headline": "Manual headline",
                "body_markdown": "Manual draft",
                "summary": "Manual summary",
                "why_now": "Timely",
                "style_fit_summary": "Good fit",
                "source_bundle": {"primary_sources": ["https://a.example.com/1"]},
                "scores": {"relevance": 0.95, "novelty": 0.9, "style_fit": 0.88},
                "risk_flags": [],
            },
            {"total_tokens": 42},
        ),
    )
    session = SESSION_LOCAL()
    try:
        graph = build_post_copilot_graph(
            session=session,
            provider=OpenAICompatibleProvider(
                base_url="https://example.invalid",
                model_name="gpt-test",
                api_key="secret",
            ),
        )
        result = graph.invoke(
            {
                "tenant_id": TENANT,
                "channel_id": CHANNEL,
                "mode": "post_copilot",
                "user_request": "Find sources and visuals",
                "image_request": True,
                "topic_definition": None,
                "content_item_id": None,
                "agent_task_id": None,
                "agent_run_id": "run-2",
                "seed_urls": [],
                "tool_trace": [],
                "errors": [],
            }
        )
    finally:
        session.close()

    source_package = result["source_package"]
    assert source_package["package_status"] == "ready"
    assert len(source_package["primary_sources_details"]) == 3
    assert source_package["selection_context"]["source_shortlist_summary"]["selected_sources"] == 3
    assert source_package["selection_context"]["source_package_summary"]["unique_domains"] == [
        "a.example.com",
        "b.example.com",
    ]
    assert source_package["image_candidates"][0]["url"] == "https://images.example.com/a1.jpg"


def test_post_copilot_source_package_skips_image_candidates_without_image_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    monkeypatch.setattr(
        "postbridge.agent.graphs.source_package.collect_topic_sources",
        lambda topic, seed_urls=None, **kwargs: [
            {
                "url": "https://a.example.com/1",
                "title": "A1",
                "text_excerpt": "A1",
                "published_at": (now - timedelta(hours=1)).isoformat(),
                "updated_at": (now - timedelta(minutes=30)).isoformat(),
                "retrieval_backend": "duckduckgo",
                "retrieval_score": 0.95,
                "retrieval_combined_score": 0.95,
                "image_urls": ["https://images.example.com/a1.jpg"],
                "preview_image_url": "https://images.example.com/a1.jpg",
            },
        ],
    )
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        lambda self, *, messages, temperature=0.2: (
            {
                "topic": "Manual topic",
                "headline": "Manual headline",
                "body_markdown": "Manual draft",
                "summary": "Manual summary",
                "why_now": "Timely",
                "style_fit_summary": "Good fit",
                "source_bundle": {"primary_sources": ["https://a.example.com/1"]},
                "scores": {"relevance": 0.95, "novelty": 0.9, "style_fit": 0.88},
                "risk_flags": [],
            },
            {"total_tokens": 42},
        ),
    )
    session = SESSION_LOCAL()
    try:
        graph = build_post_copilot_graph(
            session=session,
            provider=OpenAICompatibleProvider(
                base_url="https://example.invalid",
                model_name="gpt-test",
                api_key="secret",
            ),
        )
        result = graph.invoke(
            {
                "tenant_id": TENANT,
                "channel_id": CHANNEL,
                "mode": "post_copilot",
                "user_request": "Write about dogs without images",
                "image_request": False,
                "topic_definition": None,
                "content_item_id": None,
                "agent_task_id": None,
                "agent_run_id": "run-no-images",
                "seed_urls": [],
                "tool_trace": [],
                "errors": [],
            }
        )
    finally:
        session.close()

    source_package = result["source_package"]
    assert source_package["selection_context"]["source_package_summary"]["requested_images"] is False
    assert source_package["selection_context"]["source_package_summary"]["image_candidate_count"] == 0
    assert source_package["image_candidates"] == []


def test_workspace_policy_guides_source_collection_and_prompt(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SESSION_LOCAL()
    try:
        session.add(
            AgentPolicyOrm(
                id=str(uuid4()),
                tenant_id=TENANT,
                channel_id=None,
                policy_json='{"workspace_policy":{"editor_instructions":"Prefer concise, factual copy.","search_instructions":"Prefer official sources first.","preferred_domains":["official.example"],"blocked_domains":["blocked.example"],"blocked_url_patterns":["*utm_*"]}}',
                version=1,
            )
        )
        session.commit()
    finally:
        session.close()

    captured: dict[str, object] = {}

    def fake_collect_topic_sources(topic: str, seed_urls=None, **kwargs):
        captured["collect_kwargs"] = kwargs
        return [
            {
                "url": "https://official.example/story",
                "title": "Story",
                "text_excerpt": "Story text",
                "published_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "retrieval_backend": "duckduckgo",
                "retrieval_score": 0.93,
                "retrieval_combined_score": 0.93,
            }
        ]

    def fake_invoke_json(self, *, messages, temperature=0.2):
        captured["messages"] = messages
        return (
            {
                "topic": "Story topic",
                "headline": "Story headline",
                "body_markdown": "Story draft",
                "summary": "Summary",
                "why_now": "Timely",
                "style_fit_summary": "Good fit",
                "source_bundle": {"primary_sources": ["https://official.example/story"]},
                "scores": {"relevance": 0.9, "novelty": 0.8, "style_fit": 0.85},
                "risk_flags": [],
            },
            {"total_tokens": 42},
        )

    monkeypatch.setattr(
        "postbridge.agent.graphs.source_package.collect_topic_sources",
        fake_collect_topic_sources,
    )
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        fake_invoke_json,
    )

    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Create a short post",
            "autonomy_mode": "draft_approval",
            "seed_urls": ["https://official.example/story"],
        },
    )
    assert response.status_code == 200, response.text

    guided = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Find and draft from sources",
            "autonomy_mode": "draft_approval",
        },
    )
    assert guided.status_code == 200, guided.text

    collect_kwargs = captured["collect_kwargs"]
    assert collect_kwargs["preferred_domains"] == {"official.example"}
    assert collect_kwargs["blocked_domains"] == {"blocked.example"}
    assert collect_kwargs["blocked_url_patterns"] == ["*utm_*"]
    messages = captured["messages"]
    assert any("Prefer concise, factual copy." in msg["content"] for msg in messages if msg["role"] == "user")
    assert any("Prefer official sources first." in msg["content"] for msg in messages if msg["role"] == "user")


def test_approving_source_package_review_item_triggers_follow_up_run(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    now = datetime.now(UTC)
    discovered_sources = [
        {
            "url": "https://a.example.com/1",
            "title": "A1",
            "text_excerpt": "A1",
            "published_at": (now - timedelta(hours=1)).isoformat(),
            "updated_at": (now - timedelta(minutes=30)).isoformat(),
            "retrieval_backend": "duckduckgo",
            "retrieval_score": 0.95,
            "retrieval_combined_score": 0.95,
            "image_urls": ["https://images.example.com/a1.jpg"],
            "preview_image_url": "https://images.example.com/a1.jpg",
        },
        {
            "url": "https://b.example.com/1",
            "title": "B1",
            "text_excerpt": "B1",
            "published_at": (now - timedelta(hours=2)).isoformat(),
            "updated_at": (now - timedelta(hours=1)).isoformat(),
            "retrieval_backend": "duckduckgo",
            "retrieval_score": 0.88,
            "retrieval_combined_score": 0.88,
        },
    ]
    monkeypatch.setattr(
        "postbridge.agent.graphs.source_package.collect_topic_sources",
        lambda topic, seed_urls=None, **kwargs: discovered_sources,
    )
    monkeypatch.setattr(
        "postbridge.agent.graphs.source_package.collect_seed_sources",
        lambda urls: discovered_sources if urls else [],
    )
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        lambda self, *, messages, temperature=0.2: (
            {
                "topic": "Saint Petersburg",
                "headline": "Saint Petersburg draft",
                "body_markdown": "Draft body",
                "summary": "Summary",
                "why_now": "Timely",
                "style_fit_summary": "Good fit",
                "source_bundle": {"primary_sources": ["https://a.example.com/1"]},
                "scores": {"relevance": 0.9, "novelty": 0.8, "style_fit": 0.85},
                "risk_flags": [],
            },
            {"total_tokens": 42},
        ),
    )
    content_id = str(uuid4())
    session = SESSION_LOCAL()
    try:
        session.add(
            ContentItemOrm(
                id=content_id,
                tenant_id=TENANT,
                source_type="manual",
                title="Old",
                body_markdown="Old body",
                status="draft",
            )
        )
        session.commit()
    finally:
        session.close()

    start = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Write a Saint Petersburg post with sources",
            "content_item_id": content_id,
            "autonomy_mode": "guarded_auto_publish",
            "require_source_approval": True,
        },
    )
    assert start.status_code == 200, start.text
    review_item = client.get("/internal/service/review-queue", headers=_svc_headers()).json()[0]
    resolved = client.post(
        f"/internal/service/review-queue/{review_item['id']}/resolve",
        headers=_svc_headers(),
        json={
            "decision": "approved",
            "approved_seed_urls": ["https://a.example.com/1"],
            "approved_image_urls": ["https://images.example.com/a1.jpg"],
        },
    )
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["status"] == "approved"
    assert body["decision"]["follow_up_run_id"]
    assert body["decision"]["approved_seed_urls"] == ["https://a.example.com/1"]
    assert body["decision"]["approved_image_urls"] == ["https://images.example.com/a1.jpg"]
    assert body["follow_up_run"]["status"] == "completed"
    session = SESSION_LOCAL()
    try:
        row = session.get(ContentItemOrm, content_id)
        assert row is not None
        assert row.title == "Saint Petersburg draft"
        assert row.body_markdown == "Draft body"
    finally:
        session.close()


def test_post_copilot_materializes_selected_public_image_url(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        lambda self, *, messages, temperature=0.2: (
            {
                "topic": "Manual topic",
                "headline": "Manual headline",
                "body_markdown": "Manual draft with image",
                "summary": "Manual summary",
                "why_now": "Timely",
                "style_fit_summary": "Good fit",
                "source_bundle": {"primary_sources": ["https://example.com/a"]},
                "scores": {"relevance": 0.95, "novelty": 0.9, "style_fit": 0.88},
                "risk_flags": [],
                "media_url": "https://images.example.com/1.jpg",
                "media_urls": ["https://images.example.com/1.jpg"],
                "cover_image_url": "https://images.example.com/1.jpg",
            },
            {"total_tokens": 42},
        ),
    )
    session = SESSION_LOCAL()
    try:
        content_id = str(uuid4())
        session.add(
            ContentItemOrm(
                id=content_id,
                tenant_id=TENANT,
                source_type="manual",
                title="Old",
                body_markdown="Old body",
                status="draft",
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Добавь картинку в пост",
            "image_request": True,
            "content_item_id": content_id,
            "autonomy_mode": "guarded_auto_publish",
            "seed_urls": ["https://example.com/a"],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    session = SESSION_LOCAL()
    try:
        row = session.get(ContentItemOrm, content_id)
        assert row is not None
        assert row.media_url == "https://images.example.com/1.jpg"
        assert row.media_urls == ["https://images.example.com/1.jpg"]
        assert "https://images.example.com/1.jpg" in (row.body_structured_json or "")
    finally:
        session.close()


def test_collect_topic_sources_ranks_fresher_and_more_relevant_results(monkeypatch: pytest.MonkeyPatch):
    from postbridge.agent import retrieval

    monkeypatch.setattr(
        retrieval,
        "search_sources",
        lambda query, max_results=None: [
            {
                "url": "https://old.example.com/post",
                "title": "Novosibirsk transport update",
                "snippet": "Older but relevant report",
                "rank": 1,
                "search_score": 0.9,
                "backend": "duckduckgo",
            },
            {
                "url": "https://fresh.example.com/post",
                "title": "Fresh Novosibirsk transport update",
                "snippet": "Breaking local report",
                "rank": 2,
                "search_score": 0.84,
                "backend": "duckduckgo",
            },
        ],
    )

    def fake_fetch(url: str, *, timeout_seconds: float = 15.0):
        if "fresh" in url:
            return {
                "url": url,
                "title": "Fresh Novosibirsk transport update",
                "text_excerpt": "Fresh Novosibirsk transport update with local details",
                "published_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
                "updated_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            }
        return {
            "url": url,
            "title": "Novosibirsk transport update",
            "text_excerpt": "Relevant but old transport report",
            "published_at": (datetime.now(UTC) - timedelta(days=5)).isoformat(),
            "updated_at": (datetime.now(UTC) - timedelta(days=5)).isoformat(),
        }

    monkeypatch.setattr(retrieval, "fetch_url_source", fake_fetch)
    ranked = retrieval.collect_topic_sources("Novosibirsk transport", max_results=5)
    assert len(ranked) == 2
    assert ranked[0]["url"] == "https://fresh.example.com/post"
    assert ranked[0]["retrieval_score"] >= ranked[1]["retrieval_score"]
    assert ranked[0]["retrieval_rank"] == 1
    assert ranked[0]["retrieval_reason"]


def test_collect_topic_sources_applies_domain_and_source_type_filters(monkeypatch: pytest.MonkeyPatch):
    from postbridge.agent import retrieval

    monkeypatch.setenv("AGENT_SEARCH_ALLOWED_DOMAINS", "news.example.com,allowed.example.com")
    monkeypatch.setenv("AGENT_SEARCH_BLOCKED_DOMAINS", "blocked.example.com")
    monkeypatch.setenv("AGENT_SEARCH_BLOCKED_SOURCE_TYPES", "documentation")
    monkeypatch.setattr(
        retrieval,
        "search_sources",
        lambda query, max_results=None: [
            {"url": "https://news.example.com/a", "title": "Good", "snippet": "Fresh news", "rank": 1, "search_score": 0.9, "backend": "duckduckgo"},
            {"url": "https://blocked.example.com/a", "title": "Blocked", "snippet": "Fresh news", "rank": 2, "search_score": 0.8, "backend": "duckduckgo"},
            {"url": "https://docs.example.com/help", "title": "Documentation", "snippet": "Manual", "rank": 3, "search_score": 0.7, "backend": "duckduckgo"},
        ],
    )
    monkeypatch.setattr(
        retrieval,
        "fetch_url_source",
        lambda url, timeout_seconds=15.0: {
            "url": url,
            "title": "Documentation" if "docs." in url else "News item",
            "text_excerpt": "Manual page" if "docs." in url else "Fresh city news",
            "published_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )
    ranked = retrieval.collect_topic_sources("Novosibirsk transport", max_results=5)
    assert len(ranked) == 1
    assert ranked[0]["url"] == "https://news.example.com/a"


def test_collect_topic_sources_applies_max_age_filter(monkeypatch: pytest.MonkeyPatch):
    from postbridge.agent import retrieval

    monkeypatch.setenv("AGENT_SEARCH_MAX_SOURCE_AGE_HOURS", "24")
    monkeypatch.setattr(
        retrieval,
        "search_sources",
        lambda query, max_results=None: [
            {"url": "https://news.example.com/fresh", "title": "Fresh", "snippet": "Fresh", "rank": 1, "search_score": 0.9, "backend": "duckduckgo"},
            {"url": "https://news.example.com/stale", "title": "Stale", "snippet": "Old", "rank": 2, "search_score": 0.8, "backend": "duckduckgo"},
        ],
    )
    monkeypatch.setattr(
        retrieval,
        "fetch_url_source",
        lambda url, timeout_seconds=15.0: {
            "url": url,
            "title": "Fresh" if "fresh" in url else "Stale",
            "text_excerpt": "Fresh city news" if "fresh" in url else "Old city news",
            "published_at": (datetime.now(UTC) - timedelta(hours=3)).isoformat()
            if "fresh" in url
            else (datetime.now(UTC) - timedelta(days=4)).isoformat(),
            "updated_at": None,
        },
    )
    ranked = retrieval.collect_topic_sources("Novosibirsk transport", max_results=5)
    assert len(ranked) == 1
    assert ranked[0]["url"] == "https://news.example.com/fresh"


def test_search_sources_uses_backend_failover_and_query_variants(monkeypatch: pytest.MonkeyPatch):
    from postbridge.agent import retrieval

    monkeypatch.setenv("AGENT_SEARCH_BACKEND", "auto")
    monkeypatch.setenv("AGENT_SEARCH_QUERY_VARIANTS", "3")

    def fake_searxng(query: str, *, max_results: int, base_url: str, api_key: str | None, language: str | None):
        raise retrieval.ExternalApiError(
            code="EXTERNAL_SEARCH_HTTP_ERROR",
            message="temporary search error",
            source="agent_search",
            retryable=True,
            details={"backend": "searxng", "query": query},
        )

    observed_queries: list[str] = []

    def fake_duckduckgo(query: str, *, max_results: int):
        observed_queries.append(query)
        return [
            {
                "url": "https://news.example.com/shared",
                "title": "Shared result",
                "snippet": f"Variant for {query}",
                "rank": 1,
                "search_score": 0.82 if "latest news" in query else 0.9,
                "backend": "duckduckgo",
            }
        ]

    monkeypatch.setattr(retrieval, "_searxng_search", fake_searxng)
    monkeypatch.setattr(retrieval, "_duckduckgo_search", fake_duckduckgo)

    ranked = retrieval.search_sources("Novosibirsk transport", max_results=5)
    assert ranked
    assert len(ranked) == 1
    assert ranked[0]["url"] == "https://news.example.com/shared"
    assert ranked[0]["search_backends"] == ["duckduckgo"]
    assert ranked[0]["search_query_variant"]
    assert any("update" in query or "news" in query for query in observed_queries[1:])


def test_normalize_duckduckgo_result_url_extracts_target():
    from postbridge.agent import retrieval

    assert (
        retrieval._normalize_duckduckgo_result_url(
            "//duckduckgo.com/l/?uddg=https%3A%2F%2Fngs.ru%2Fnews%2Fstory"
        )
        == "https://ngs.ru/news/story"
    )
    assert retrieval._normalize_duckduckgo_result_url("https://vn.ru/news/") == "https://vn.ru/news/"
    assert retrieval._normalize_duckduckgo_result_url("javascript:void(0)") is None


def test_duckduckgo_search_normalizes_redirect_result_urls(monkeypatch: pytest.MonkeyPatch):
    from postbridge.agent import retrieval

    class DummyResponse:
        status_code = 200
        text = """
        <html>
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fngs.ru%2F">NGS</a>
          <a class="result__snippet">Fresh city news</a>
        </html>
        """

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, params=None, headers=None):
            return DummyResponse()

    monkeypatch.setattr(retrieval.httpx, "Client", DummyClient)

    results = retrieval._duckduckgo_search("Novosibirsk news", max_results=5)

    assert len(results) == 1
    assert results[0]["url"] == "https://ngs.ru/"
    assert results[0]["backend"] == "duckduckgo"


def test_collect_topic_sources_fetches_deduped_search_results(monkeypatch: pytest.MonkeyPatch):
    from postbridge.agent import retrieval

    monkeypatch.setenv("AGENT_SEARCH_BACKEND", "duckduckgo")
    monkeypatch.setenv("AGENT_SEARCH_QUERY_VARIANTS", "2")
    monkeypatch.setenv("AGENT_SEARCH_FETCH_BUDGET", "5")
    monkeypatch.setattr(
        retrieval,
        "_duckduckgo_search",
        lambda query, max_results: [
            {
                "url": "https://news.example.com/shared",
                "title": "Shared result",
                "snippet": "Variant A",
                "rank": 1,
                "search_score": 0.91,
                "backend": "duckduckgo",
            },
            {
                "url": "https://news.example.com/shared",
                "title": "Shared result duplicate",
                "snippet": "Variant B",
                "rank": 2,
                "search_score": 0.86,
                "backend": "duckduckgo",
            },
        ],
    )
    fetch_calls: list[str] = []

    def fake_fetch(url: str, *, timeout_seconds: float = 15.0):
        fetch_calls.append(url)
        return {
            "url": url,
            "title": "Shared result",
            "text_excerpt": "Fresh merged source",
            "published_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }

    monkeypatch.setattr(retrieval, "fetch_url_source", fake_fetch)
    ranked = retrieval.collect_topic_sources("Novosibirsk transport", max_results=5)
    assert len(fetch_calls) == 1
    assert len(ranked) == 1
    assert ranked[0]["url"] == "https://news.example.com/shared"
    assert "duckduckgo" in ranked[0]["retrieval_backends"]


def test_collect_topic_sources_fetches_normalized_duckduckgo_target_url(monkeypatch: pytest.MonkeyPatch):
    from postbridge.agent import retrieval

    monkeypatch.setenv("AGENT_SEARCH_BACKEND", "duckduckgo")
    monkeypatch.setenv("AGENT_SEARCH_QUERY_VARIANTS", "1")
    monkeypatch.setenv("AGENT_SEARCH_FETCH_BUDGET", "5")
    monkeypatch.setattr(
        retrieval,
        "_duckduckgo_search",
        lambda query, max_results: [
            {
                "url": "https://ngs.ru/",
                "title": "Новости Новосибирска",
                "snippet": "Fresh city news",
                "rank": 1,
                "search_score": 0.93,
                "backend": "duckduckgo",
            }
        ],
    )
    fetch_calls: list[str] = []

    def fake_fetch(url: str, *, timeout_seconds: float = 15.0):
        fetch_calls.append(url)
        return {
            "url": url,
            "title": "Новости Новосибирска",
            "text_excerpt": "Fresh city news from NGS",
            "published_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }

    monkeypatch.setattr(retrieval, "fetch_url_source", fake_fetch)

    ranked = retrieval.collect_topic_sources("Novosibirsk transport", max_results=5)

    assert fetch_calls == ["https://ngs.ru/"]
    assert len(ranked) == 1
    assert ranked[0]["url"] == "https://ngs.ru/"


def test_parse_image_dimensions_reads_png_size():
    from postbridge.agent.graphs import source_package

    payload = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + b"\x00\x00\x02\x80"
        + b"\x00\x00\x01\xe0"
        + b"\x08\x02\x00\x00\x00"
    )

    assert source_package._parse_image_dimensions(payload) == (640, 480)


def test_collect_image_candidates_filters_small_images(monkeypatch: pytest.MonkeyPatch):
    from postbridge.agent.graphs import source_package

    monkeypatch.setattr(
        source_package,
        "_probe_image_dimensions",
        lambda url, timeout_seconds=5.0: (120, 120) if "small" in url else (640, 480),
    )

    candidates = source_package._collect_image_candidates(
        [
            {
                "url": "https://news.example.com/story",
                "title": "City news",
                "preview_image_url": "https://images.example.com/small.png",
                "image_urls": [
                    "https://images.example.com/small.png",
                    "https://images.example.com/large.jpg",
                ],
            }
        ]
    )

    assert len(candidates) == 1
    assert candidates[0]["url"] == "https://images.example.com/large.jpg"
    assert candidates[0]["width"] == 640
    assert candidates[0]["height"] == 480


def test_collect_topic_evidence_prefers_historically_trusted_domain(monkeypatch: pytest.MonkeyPatch):
    from postbridge.agent.tools import collect_topic_evidence

    session = SESSION_LOCAL()
    try:
        _seed_agent_run(session, run_id="run-1", graph_name="topic_scout")
        _seed_agent_run(session, run_id="run-2", graph_name="topic_scout")
        session.add(
            ContentCandidateOrm(
                id=str(uuid4()),
                agent_run_id="run-1",
                tenant_id=TENANT,
                channel_id=CHANNEL,
                status="converted",
                headline="Trusted historical candidate",
                source_bundle_json='{"primary_sources_details":[{"url":"https://trusted.example.com/1","title":"Trusted","text_excerpt":"Good"}]}',
                scores_json="{}",
                risk_flags_json="[]",
                draft_json="{}",
            )
        )
        session.add(
            ContentCandidateOrm(
                id=str(uuid4()),
                agent_run_id="run-2",
                tenant_id=TENANT,
                channel_id=CHANNEL,
                status="rejected",
                headline="Risky historical candidate",
                source_bundle_json='{"primary_sources_details":[{"url":"https://risky.example.com/1","title":"Risky","text_excerpt":"Bad"}]}',
                scores_json="{}",
                risk_flags_json='["source_conflict"]',
                draft_json="{}",
            )
        )
        session.commit()

        monkeypatch.setattr(
            "postbridge.agent.tools.collect_topic_sources",
            lambda topic, seed_urls=None, **kwargs: [
                {
                    "url": "https://risky.example.com/new",
                    "title": "Topic update",
                    "text_excerpt": "Topic update",
                    "retrieval_score": 0.8,
                    "retrieval_backend": "duckduckgo",
                },
                {
                    "url": "https://trusted.example.com/new",
                    "title": "Topic update",
                    "text_excerpt": "Topic update",
                    "retrieval_score": 0.75,
                    "retrieval_backend": "duckduckgo",
                },
            ],
        )
        ranked = collect_topic_evidence(
            session,
            tenant_id=TENANT,
            channel_id=CHANNEL,
            topic="Topic update",
            seed_urls=[],
        )
        assert len(ranked) == 2
        assert ranked[0]["url"] == "https://trusted.example.com/new"
        assert ranked[0]["retrieval_trust_label"] in {"trusted", "mixed", "insufficient_data"}
        assert ranked[0]["retrieval_trust_score"] >= ranked[1]["retrieval_trust_score"]
        assert ranked[0]["retrieval_combined_score"] >= ranked[1]["retrieval_combined_score"]
    finally:
        session.close()


def test_shortlist_topic_evidence_applies_freshness_and_domain_caps():
    now = datetime.now(UTC)
    shortlisted, summary = shortlist_topic_evidence(
        [
            {
                "url": "https://a.example.com/1",
                "retrieval_combined_score": 0.95,
                "published_at": (now - timedelta(hours=2)).isoformat(),
            },
            {
                "url": "https://a.example.com/2",
                "retrieval_combined_score": 0.9,
                "published_at": (now - timedelta(hours=3)).isoformat(),
            },
            {
                "url": "https://a.example.com/3",
                "retrieval_combined_score": 0.89,
                "published_at": (now - timedelta(hours=4)).isoformat(),
            },
            {
                "url": "https://b.example.com/1",
                "retrieval_combined_score": 0.7,
                "published_at": (now - timedelta(hours=5)).isoformat(),
            },
            {
                "url": "https://stale.example.com/1",
                "retrieval_combined_score": 0.99,
                "published_at": (now - timedelta(days=10)).isoformat(),
            },
        ],
        max_sources=3,
        max_per_domain=2,
    )
    assert len(shortlisted) == 3
    assert sum(1 for item in shortlisted if "a.example.com" in item["url"]) <= 2
    assert all("stale.example.com" not in item["url"] for item in shortlisted)
    assert summary["selected_sources"] == 3
    assert summary["freshness_filtered"] >= 1


def test_shortlist_topic_evidence_prefers_news_like_sources_for_news_intent():
    now = datetime.now(UTC)
    shortlisted, summary = shortlist_topic_evidence(
        [
            {
                "url": "https://docs.example.com/novosibirsk-guide",
                "title": "Documentation guide",
                "text_excerpt": "Manual for Novosibirsk services",
                "published_at": (now - timedelta(hours=1)).isoformat(),
                "retrieval_combined_score": 0.99,
                "source_type": "documentation",
                "source_type_weight": 0.2,
            },
            {
                "url": "https://news.example.com/metro",
                "title": "Novosibirsk metro update",
                "text_excerpt": "Fresh city news",
                "published_at": (now - timedelta(hours=2)).isoformat(),
                "retrieval_combined_score": 0.9,
                "source_type": "local_news",
                "source_type_weight": 1.0,
            },
            {
                "url": "https://gov.example.gov/transport",
                "title": "Official transport update",
                "text_excerpt": "Municipal transport update",
                "published_at": (now - timedelta(hours=3)).isoformat(),
                "retrieval_combined_score": 0.88,
                "source_type": "government",
                "source_type_weight": 0.88,
            },
            {
                "url": "https://press.example.com/release",
                "title": "Press release for Novosibirsk event",
                "text_excerpt": "Official release",
                "published_at": (now - timedelta(hours=4)).isoformat(),
                "retrieval_combined_score": 0.82,
                "source_type": "press_release",
                "source_type_weight": 0.78,
            },
        ],
        topic="Find fresh Novosibirsk news",
        max_sources=3,
        max_per_domain=2,
    )
    assert len(shortlisted) == 3
    assert all(item["source_type"] != "documentation" for item in shortlisted)
    assert summary["topic_intent"] == "news"
    assert summary["source_type_filtered"] >= 1


def test_shortlist_topic_angles_builds_distinct_angle_pack():
    angles, summary = shortlist_topic_angles(
        [
            {
                "url": "https://news.example.com/metro",
                "title": "Novosibirsk metro expansion reaches new district",
                "text_excerpt": "City transport update",
                "retrieval_combined_score": 0.93,
                "source_type": "local_news",
                "retrieval_trust_label": "trusted",
                "local_relevance_score": 1.0,
                "news_relevance_score": 1.0,
            },
            {
                "url": "https://gov.example.gov/metro",
                "title": "Official update on Novosibirsk metro expansion",
                "text_excerpt": "Transport update",
                "retrieval_combined_score": 0.88,
                "source_type": "government",
                "retrieval_trust_label": "trusted",
                "local_relevance_score": 1.0,
                "news_relevance_score": 0.75,
            },
        ],
        topic="Find fresh Novosibirsk news",
        max_angles=2,
    )
    assert len(angles) == 2
    assert summary["selected_angles"] == 2
    assert summary["topic_intent"] == "news"
    assert angles[0]["source_types"]
    assert "fresh news lead" in angles[0]["why_this_angle"]


def test_classify_source_type_prefers_editorial_categories():
    assert classify_source_type({"url": "https://docs.example.com/help/article"}) == "documentation"
    assert classify_source_type({"url": "https://city.example.gov/news/update"}) == "government"
    assert classify_source_type({"url": "https://news.example.com/novosibirsk/update", "title": "Breaking news"}) in {
        "local_news",
        "news_article",
    }


def test_score_candidate_against_angles_prefers_closest_angle():
    score, matched = score_candidate_against_angles(
        {
            "headline": "Novosibirsk metro expansion reaches new district",
            "topic": "Metro expansion in Novosibirsk",
            "summary": "Transport update",
        },
        [
            {"angle": "Federal tax policy changes", "headline_hint": "Tax changes"},
            {"angle": "Novosibirsk metro expansion reaches new district", "headline_hint": "Metro expansion"},
        ],
    )
    assert score > 0.4
    assert matched == "Novosibirsk metro expansion reaches new district"


def test_collect_topic_evidence_enriches_source_type_trust(monkeypatch: pytest.MonkeyPatch):
    from postbridge.agent.tools import collect_topic_evidence

    session = SESSION_LOCAL()
    try:
        run_one = str(uuid4())
        run_two = str(uuid4())
        _seed_agent_run(session, run_id=run_one, graph_name="topic_scout")
        _seed_agent_run(session, run_id=run_two, graph_name="topic_scout")
        session.add(
            ContentCandidateOrm(
                id=str(uuid4()),
                tenant_id=TENANT,
                channel_id=CHANNEL,
                agent_run_id=run_one,
                status="converted",
                topic="Topic update",
                headline="Topic headline",
                body_markdown="Body",
                summary="Summary",
                why_now="Now",
                style_fit_summary="Fits",
                dedup_summary="",
                source_bundle_json='{"primary_sources_details":[{"url":"https://news.example.com/1","title":"News 1","text_excerpt":"Fresh city news"}]}',
                scores_json="{}",
                risk_flags_json="[]",
            )
        )
        session.add(
            ContentCandidateOrm(
                id=str(uuid4()),
                tenant_id=TENANT,
                channel_id=CHANNEL,
                agent_run_id=run_two,
                status="rejected",
                topic="Topic update",
                headline="Topic headline 2",
                body_markdown="Body",
                summary="Summary",
                why_now="Now",
                style_fit_summary="Fits",
                dedup_summary="",
                source_bundle_json='{"primary_sources_details":[{"url":"https://docs.example.com/help","title":"Documentation","text_excerpt":"Manual page"}]}',
                scores_json="{}",
                risk_flags_json='["low_trust_source_type_mix"]',
            )
        )
        session.commit()
        monkeypatch.setattr(
            "postbridge.agent.tools.collect_topic_sources",
            lambda topic, seed_urls=None, preferred_domains=None, blocked_domains=None, blocked_url_patterns=None: [
                {
                    "url": "https://docs.example.com/guide",
                    "title": "Documentation page",
                    "text_excerpt": "Manual and help",
                    "retrieval_score": 0.9,
                },
                {
                    "url": "https://news.example.com/update",
                    "title": "Fresh Novosibirsk update",
                    "text_excerpt": "Breaking city news",
                    "retrieval_score": 0.82,
                },
            ],
        )
        ranked = collect_topic_evidence(
            session,
            tenant_id=TENANT,
            channel_id=CHANNEL,
            topic="Find fresh Novosibirsk news",
            seed_urls=[],
        )
        assert ranked[0]["source_type"] in {"local_news", "news_article"}
        assert ranked[0]["source_type_trust_score"] >= ranked[1]["source_type_trust_score"]
    finally:
        session.close()


def test_source_disagreement_details_detects_stronger_contradiction():
    from postbridge.agent.tools import source_disagreement_details

    disagreement, conflict = source_disagreement_details(
        [
            {
                "title": "Novosibirsk metro project approved",
                "text_excerpt": "Officials confirmed the project will open this year with 10 stations.",
            },
            {
                "title": "Novosibirsk metro project cancelled",
                "text_excerpt": "Officials denied the launch and said only 4 stations remain in plan.",
            },
        ]
    )
    assert disagreement >= 0.2
    assert conflict >= 0.55


def test_source_disagreement_details_detects_entity_event_conflict():
    from postbridge.agent.tools import source_disagreement_details

    disagreement, conflict = source_disagreement_details(
        [
            {
                "title": "Mayor confirms bridge opening in Novosibirsk",
                "text_excerpt": "The mayor said the bridge opens next month.",
            },
            {
                "title": "Mayor denies bridge opening in Novosibirsk",
                "text_excerpt": "The mayor said the bridge launch was cancelled.",
            },
        ]
    )
    assert disagreement >= 0.2
    assert conflict >= 0.6


def test_source_conflict_explanations_returns_structured_pairs():
    examples = source_conflict_explanations(
        [
            {
                "url": "https://left.example.com/a",
                "title": "Mayor confirms bridge opening in Novosibirsk",
                "text_excerpt": "The mayor said the bridge opens next month.",
            },
            {
                "url": "https://right.example.com/b",
                "title": "Mayor denies bridge opening in Novosibirsk",
                "text_excerpt": "The mayor said the bridge launch was cancelled.",
            },
        ]
    )
    assert examples
    assert examples[0]["conflict_score"] >= 0.35
    assert examples[0]["reason"]
    assert examples[0]["left"]["url"] == "https://left.example.com/a"
    assert examples[0]["right"]["url"] == "https://right.example.com/b"


def test_build_review_hints_prioritizes_conflict_and_repetition():
    hints = build_review_hints(
        source_quality_summary={
            "conflict_explanations": [
                {"conflict_score": 0.82, "reason": "opposing factual claims"},
            ]
        },
        scores={"source_corroboration": 0.2, "angle_pressure": 0.5},
        risk_flags=["possible_duplicate", "repeated_angle"],
    )
    actions = [item["action"] for item in hints]
    assert "verify_conflicting_sources" in actions
    assert "compare_with_recent_publications" in actions
    assert "consider_new_angle" in actions


def test_build_review_hints_accepts_structured_risk_flags():
    hints = build_review_hints(
        source_quality_summary={},
        scores={"source_corroboration": 0.2},
        risk_flags=[
            {"flag": "possible_duplicate"},
            {"code": "repeated_angle"},
            {"name": "single_source"},
        ],
    )
    actions = [item["action"] for item in hints]
    assert "compare_with_recent_publications" in actions
    assert "consider_new_angle" in actions
    assert "seek_additional_source" in actions


def test_review_action_from_hints_maps_to_taxonomy():
    assert (
        review_action_from_hints(
            decision="approved",
            review_hints=[{"action": "verify_conflicting_sources"}],
        )
        == "approve_after_fact_check"
    )
    assert (
        review_action_from_hints(
            decision="approved",
            review_hints=[{"action": "consider_new_angle"}],
        )
        == "approve_after_new_angle"
    )
    assert (
        review_action_from_hints(
            decision="rejected",
            review_hints=[{"action": "compare_with_recent_publications"}],
        )
        == "reject_duplicate"
    )


def test_workflow_preset_and_suggested_decision_follow_hints():
    hints = build_review_hints(
        source_quality_summary={"conflict_explanations": [{"conflict_score": 0.8, "reason": "opposing factual claims"}]},
        scores={"source_conflict": 0.8, "source_corroboration": 0.6},
        risk_flags=[],
    )
    assert infer_workflow_preset(
        source_quality_summary={"conflict_explanations": [{"conflict_score": 0.8, "reason": "opposing factual claims"}]},
        scores={"source_conflict": 0.8},
        risk_flags=[],
        review_hints=hints,
    ) == "fact_check"
    suggestion = suggest_review_decision(
        source_quality_summary={"conflict_explanations": [{"conflict_score": 0.8, "reason": "opposing factual claims"}]},
        scores={"source_conflict": 0.8},
        risk_flags=[],
        review_hints=hints,
    )
    assert suggestion["review_action"] == "reject_conflict"


def test_evaluate_policy_guardrails_ignores_duplicate_and_blank_risk_flags():
    policy = AutonomyPolicy(
        mode="guarded_auto_publish",
        requires_review=True,
        materialize_on_approval=True,
        materialization_level="draft",
        auto_dispatch=False,
        blocked_risk_flags=("single_source",),
    )
    result = evaluate_policy_guardrails(
        policy,
        scores={"source_count": 1, "source_quality": 0.9},
        risk_flags=["single_source", "", "single_source"],
    )
    assert result["blocked"] is True
    assert result["reasons"] == ["blocked_risk_flag:single_source"]


def test_post_copilot_edit_prompt_includes_existing_draft_content():
    session = SESSION_LOCAL()
    content = ContentItemOrm(
        id=str(uuid4()),
        tenant_id=TENANT,
        source_type="postbridge",
        title="Старый заголовок",
        body_markdown="Первый абзац.\n\nВторой абзац.",
        status="draft",
    )
    session.add(content)
    session.commit()

    captured: dict[str, object] = {}

    class DummyProvider:
        def invoke_json(self, *, messages, temperature=0.2):
            captured["messages"] = messages
            return (
                {
                    "topic": "Новокузнецк",
                    "headline": "Обновлённый заголовок",
                    "body_markdown": "Первый абзац.",
                    "summary": "summary",
                    "why_now": "why now",
                    "style_fit_summary": "fit",
                    "source_bundle": {},
                    "scores": {},
                    "risk_flags": [],
                },
                {"total_tokens": 1},
            )

    graph = build_post_copilot_graph(session=session, provider=DummyProvider())
    graph.invoke(
        {
            "tenant_id": TENANT,
            "channel_id": CHANNEL,
            "content_item_id": content.id,
            "user_request": "Удали последний абзац.",
        }
    )

    messages = captured["messages"]
    assert isinstance(messages, list) and messages
    system_text = next(msg["content"] for msg in messages if msg["role"] == "system")
    user_text = next(msg["content"] for msg in messages if msg["role"] == "user")
    assert "revise that exact draft" in system_text
    assert "Existing draft metadata:" in user_text
    assert "Current title:" in user_text
    assert "Current body_markdown:" in user_text
    assert "Старый заголовок" in user_text
    assert "Первый абзац.\n\nВторой абзац." in user_text
    assert "Preserve every part the user did not ask to change." in user_text
    session.close()


def test_policy_override_can_auto_resolve_suggested_review_action(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("postbridge.workers.tasks.process_publication_target_task.delay", lambda *a, **k: None)
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        lambda self, *, messages, temperature=0.2: (
            {
                "topic": "Duplicate topic",
                "headline": "Duplicate headline",
                "body_markdown": "Draft",
                "summary": "Summary",
                "why_now": "Now",
                "style_fit_summary": "Fits",
                "source_bundle": {"primary_sources": ["https://example.com/a"]},
                "scores": {"relevance": 0.9},
                "risk_flags": ["possible_duplicate"],
            },
            {"total_tokens": 10},
        ),
    )
    policy = client.put(
        "/internal/service/agent/policies",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "policy": {
                "auto_resolve_review_actions": ["reject_duplicate", "reject_low_quality"],
            },
        },
    )
    assert policy.status_code == 200, policy.text
    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Use duplicate-like draft",
            "autonomy_mode": "draft_approval",
            "seed_urls": ["https://example.com/a", "https://example.com/b"],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["review_items"]
    assert payload["review_items"][0]["status"] == "rejected"
    assert payload["review_items"][0]["auto_resolved"] is True
    queue = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    assert queue[0]["status"] == "rejected"
    assert queue[0]["decision"]["auto_resolved"] is True
    assert queue[0]["decision"]["review_action"] in {"reject_duplicate", "reject_low_quality"}


def test_policy_override_can_auto_resolve_by_workflow_preset(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("postbridge.workers.tasks.process_publication_target_task.delay", lambda *a, **k: None)
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        lambda self, *, messages, temperature=0.2: (
            {
                "topic": "Duplicate topic",
                "headline": "Duplicate headline",
                "body_markdown": "Draft",
                "summary": "Summary",
                "why_now": "Now",
                "style_fit_summary": "Fits",
                "source_bundle": {"primary_sources": ["https://example.com/a"]},
                "scores": {"relevance": 0.9},
                "risk_flags": ["possible_duplicate"],
            },
            {"total_tokens": 10},
        ),
    )
    policy = client.put(
        "/internal/service/agent/policies",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "policy": {
                "auto_resolve_review_actions": ["reject_duplicate"],
                "auto_resolve_review_actions_by_preset": {
                    "anti_duplicate": ["reject_duplicate"],
                },
            },
        },
    )
    assert policy.status_code == 200, policy.text
    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Duplicate via preset mapping",
            "autonomy_mode": "draft_approval",
            "seed_urls": ["https://example.com/a", "https://example.com/b"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["review_items"][0]["auto_resolved"] is True
    queue = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    assert queue[0]["decision"]["workflow_preset"]


def test_historical_angle_pressure_detects_recent_repetition():
    session = SESSION_LOCAL()
    try:
        run_id = str(uuid4())
        _seed_agent_run(session, run_id=run_id, graph_name="topic_scout")
        session.add(
            ContentCandidateOrm(
                id=str(uuid4()),
                tenant_id=TENANT,
                channel_id=CHANNEL,
                agent_run_id=run_id,
                status="converted",
                topic="Metro expansion in Novosibirsk",
                headline="Novosibirsk metro expansion reaches new district",
                body_markdown="Body",
                summary="Summary",
                why_now="Now",
                style_fit_summary="Fits",
                dedup_summary="",
                source_bundle_json='{"matched_angle":"Novosibirsk metro expansion reaches new district"}',
                scores_json="{}",
                risk_flags_json="[]",
            )
        )
        session.commit()
        payload = historical_angle_pressure(
            session,
            tenant_id=TENANT,
            channel_id=CHANNEL,
            matched_angle="Novosibirsk metro expansion reaches new district",
        )
        assert payload["recent_match_count"] >= 1
        assert payload["pressure"] > 0.0
    finally:
        session.close()


def test_canonical_angle_family_clusters_related_angles():
    left = canonical_angle_family("Novosibirsk metro expansion reaches new district")
    right = canonical_angle_family("Metro expansion reaches another district in Novosibirsk")
    assert left
    assert right
    assert "metro" in left
    assert left == right


def test_dedupe_mixed_list_keeps_order_for_dict_risk_flags():
    flags = [
        "possible_duplicate",
        {"code": "source_conflict", "severity": "medium"},
        {"severity": "medium", "code": "source_conflict"},
        "possible_duplicate",
    ]

    assert dedupe_mixed_list(flags) == [
        "possible_duplicate",
        {"code": "source_conflict", "severity": "medium"},
    ]


def test_provider_json_parser_accepts_fenced_and_prefixed_objects():
    assert _parse_json_object_loose('```json\n{"ok": true}\n```') == {"ok": True}
    assert _parse_json_object_loose('Here is the JSON:\n{"ok": true}') == {"ok": True}


def test_provider_invoke_json_retries_when_response_is_truncated(monkeypatch: pytest.MonkeyPatch):
    provider = OpenAICompatibleProvider(base_url="https://example.invalid", model_name="gpt-test", max_tokens=128)
    calls: list[int] = []

    responses = [
        {
            "choices": [
                {
                    "finish_reason": "length",
                    "native_finish_reason": "max_output_tokens",
                    "message": {"content": '{"candidates":[{"headline":"A"}'},
                }
            ],
            "usage": {},
        },
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "native_finish_reason": "stop",
                    "message": {"content": '{"candidates":[{"headline":"A"}]}'},
                }
            ],
            "usage": {"total_tokens": 10},
        },
    ]

    def fake_post(payload):
        calls.append(payload["max_tokens"])
        return responses[len(calls) - 1]

    monkeypatch.setattr(OpenAICompatibleProvider, "_post", lambda self, payload: fake_post(payload))
    payload, usage = provider.invoke_json(messages=[{"role": "user", "content": "Return JSON"}])
    assert payload["candidates"][0]["headline"] == "A"
    assert usage["total_tokens"] == 10
    assert calls == [128, 4096]


def test_topic_scout_uses_shortlisted_sources_in_review_payload(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    now = datetime.now(UTC)
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        lambda self, *, messages, temperature=0.2: (
            {
                "candidates": [
                    {
                        "topic": "Shortlisted topic",
                        "headline": "Shortlisted headline",
                        "body_markdown": "Draft",
                        "summary": "Summary",
                        "why_now": "Now",
                        "style_fit_summary": "Fits",
                        "source_bundle": {},
                        "scores": {"relevance": 0.9},
                        "risk_flags": [],
                    }
                ]
            },
            {"total_tokens": 33},
        ),
    )
    monkeypatch.setattr(
        "postbridge.agent.graphs.topic_scout.collect_topic_evidence",
        lambda session, tenant_id, channel_id, topic, seed_urls=None, workspace_policy=None: [
            {
                "url": "https://a.example.com/1",
                "title": "A1",
                "text_excerpt": "A1",
                "published_at": (now - timedelta(hours=2)).isoformat(),
                "retrieval_combined_score": 0.95,
            },
            {
                "url": "https://a.example.com/2",
                "title": "A2",
                "text_excerpt": "A2",
                "published_at": (now - timedelta(hours=3)).isoformat(),
                "retrieval_combined_score": 0.9,
            },
            {
                "url": "https://a.example.com/3",
                "title": "A3",
                "text_excerpt": "A3",
                "published_at": (now - timedelta(hours=4)).isoformat(),
                "retrieval_combined_score": 0.89,
            },
            {
                "url": "https://b.example.com/1",
                "title": "B1",
                "text_excerpt": "B1",
                "published_at": (now - timedelta(hours=5)).isoformat(),
                "retrieval_combined_score": 0.8,
            },
            {
                "url": "https://stale.example.com/1",
                "title": "S1",
                "text_excerpt": "S1",
                "published_at": (now - timedelta(days=10)).isoformat(),
                "retrieval_combined_score": 0.99,
            },
        ],
    )
    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "topic_scout",
            "topic_definition": "Find fresh Novosibirsk news",
                "max_candidates": 2,
                "autonomy_mode": "draft_approval",
            },
        )
    assert response.status_code == 200, response.text
    queue = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    payload = queue[0]["review_payload"]
    details = payload["source_bundle"]["primary_sources_details"]
    assert len(details) == 5
    fresh_details = [item for item in details if "stale.example.com" not in item["url"]]
    assert len(fresh_details) == 4
    assert payload["source_bundle"]["topic_angles"]
    assert payload["source_bundle"]["selection_context"]["source_shortlist_summary"]["topic_intent"] == "news"
    assert payload["scores"]["angle_alignment"] >= 0.0
    assert "conflict_explanations" in payload["source_quality_summary"]
    assert payload["review_hints"]
    assert payload["workflow_preset"]
    assert payload["suggested_review_action"]


def test_run_and_candidate_detail_endpoints(client: TestClient):
    os.environ["AGENT_TRACE_POLICY"] = "summary"
    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Inspect details",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    run_id = payload["agent_run_id"]
    candidate_id = payload["candidates"][0]["candidate_id"]
    assert payload["id"] == run_id
    assert payload["token_usage"]["total_tokens"] >= 1

    runs = client.get("/internal/service/agent/runs", headers=_svc_headers())
    assert runs.status_code == 200, runs.text
    assert any(item["id"] == run_id for item in runs.json())

    run_detail = client.get(f"/internal/service/agent/runs/{run_id}", headers=_svc_headers())
    assert run_detail.status_code == 200, run_detail.text
    run_payload = run_detail.json()
    assert run_payload["id"] == run_id
    assert run_payload["trace_policy"] == "summary"
    assert run_payload["duration_ms"] is not None
    assert run_payload["tool_summary"]["tool_call_count"] >= 1
    assert run_payload["token_usage"]["total_tokens"] >= 1
    assert isinstance(run_payload["trace"]["trace"], list)

    candidates = client.get(f"/internal/service/agent/candidates?run_id={run_id}", headers=_svc_headers())
    assert candidates.status_code == 200, candidates.text
    assert len(candidates.json()) == 1

    candidate_detail = client.get(
        f"/internal/service/agent/candidates/{candidate_id}",
        headers=_svc_headers(),
    )
    assert candidate_detail.status_code == 200, candidate_detail.text
    assert candidate_detail.json()["id"] == candidate_id

    steps = client.get(
        f"/internal/service/agent/runs/{run_id}/steps",
        headers=_svc_headers(),
    )
    assert steps.status_code == 200, steps.text
    steps_payload = steps.json()
    step_names = [item["step_name"] for item in steps_payload]
    assert "run_started" in step_names
    assert "graph_invoke" in step_names
    assert "candidate_saved" in step_names
    assert "review_item_created" in step_names
    assert "run_completed" in step_names
    assert all(item["duration_ms"] is not None for item in steps_payload)


def test_semantic_dedup_summary_detects_near_duplicate():
    summary, is_duplicate = summarize_dedup(
        [
            {
                "title": "В Новосибирске открыли новый мост через реку",
                "body_markdown": "Сегодня в городе открыли новый автомобильный мост через реку Обь.",
            }
        ],
        topic="Новый мост через Обь открыли в Новосибирске",
        headline="В Новосибирске открыли новый мост через Обь",
    )
    assert is_duplicate is True
    assert "near-duplicate" in summary or "exact title match" in summary


def test_agent_task_run_and_review_resolve(client: TestClient):
    task_response = client.post(
        "/internal/service/agent/tasks",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "topic_scout",
            "goal_text": "Find fresh Novosibirsk news",
            "max_candidates_per_run": 3,
            "autonomy_mode": "plan_approval",
            "created_by": "user-1",
        },
    )
    assert task_response.status_code == 200, task_response.text
    task_id = task_response.json()["id"]

    run_response = client.post(
        f"/internal/service/agent/tasks/{task_id}/run",
        headers=_svc_headers(),
    )
    assert run_response.status_code == 200, run_response.text
    run_payload = run_response.json()["run"]
    assert run_payload["id"] == run_payload["agent_run_id"]
    assert run_payload["token_usage"]["total_tokens"] >= 1
    assert run_payload["status"] == "awaiting_review"
    assert len(run_payload["candidates"]) == 3
    assert run_payload["candidates"][0]["headline"] == "Novosibirsk headline 3"

    queue_response = client.get("/internal/service/review-queue", headers=_svc_headers())
    assert queue_response.status_code == 200, queue_response.text
    queue = queue_response.json()
    assert len(queue) == 3

    resolve_response = client.post(
        f"/internal/service/review-queue/{queue[0]['id']}/resolve",
        headers=_svc_headers(),
        json={"decision": "approved", "review_action": "approve_as_is", "note": "looks good", "reviewer_id": "u1"},
    )
    assert resolve_response.status_code == 200, resolve_response.text
    resolved = resolve_response.json()
    assert resolved["status"] == "approved"
    assert resolved["decision"]["reviewer_id"] == "u1"
    assert resolved["decision"]["review_action"] == "approve_as_is"
    assert resolved["materialization"]["materialization"] == "created_content_plan_and_targets"
    assert resolved["materialization"]["content_item_id"]
    assert len(resolved["materialization"]["publication_target_ids"]) == 1

    session = SESSION_LOCAL()
    try:
        content = session.get(ContentItemOrm, resolved["materialization"]["content_item_id"])
        assert content is not None
        assert content.title in {"Novosibirsk headline 1", "Novosibirsk headline 2", "Novosibirsk headline 3"}
        plan = session.get(PublicationPlanOrm, resolved["materialization"]["publication_plan_id"])
        assert plan is not None
        target = session.get(PublicationTargetOrm, resolved["materialization"]["publication_target_ids"][0])
        assert target is not None
        candidate = session.get(ContentCandidateOrm, resolved["candidate_id"])
        assert candidate is not None
        assert candidate.status == "converted"
        fps = session.query(ContentSourceFingerprintOrm).all()
        assert len(fps) >= 1
        embeddings = session.query(ContentEmbeddingOrm).all()
        assert len(embeddings) >= 2
    finally:
        session.close()


def test_agent_task_editorial_instructions_are_stored_and_used_in_run(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    captured_prompts: list[str] = []

    def fake_invoke_json(self, *, messages, temperature=0.2):
        prompt = "\n".join(msg["content"] for msg in messages if msg["role"] == "user")
        captured_prompts.append(prompt)
        return (
            {
                "candidates": [
                    {
                        "topic": "Novosibirsk daily",
                        "headline": "Novosibirsk headline",
                        "body_markdown": "Draft body",
                        "summary": "Summary",
                        "why_now": "Now",
                        "style_fit_summary": "Fits channel",
                        "source_bundle": {"primary_sources": ["https://example.com/1"]},
                        "scores": {"relevance": 0.9},
                        "risk_flags": [],
                    }
                ]
            },
            {"total_tokens": 12},
        )

    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        fake_invoke_json,
    )

    task_response = client.post(
        "/internal/service/agent/tasks",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "topic_scout",
            "goal_text": "Find fresh Novosibirsk news",
            "editorial_instructions": "Write a short neutral digest without meta notes.",
            "max_candidates_per_run": 1,
            "autonomy_mode": "draft_approval",
            "created_by": "user-1",
        },
    )
    assert task_response.status_code == 200, task_response.text
    task_payload = task_response.json()
    assert task_payload["editorial_instructions"] == "Write a short neutral digest without meta notes."

    list_response = client.get("/internal/service/agent/tasks", headers=_svc_headers())
    assert list_response.status_code == 200, list_response.text
    listed = list_response.json()
    assert listed[0]["editorial_instructions"] == "Write a short neutral digest without meta notes."

    run_response = client.post(
        f"/internal/service/agent/tasks/{task_payload['id']}/run",
        headers=_svc_headers(),
    )
    assert run_response.status_code == 200, run_response.text
    assert captured_prompts
    assert "Find fresh Novosibirsk news" in captured_prompts[0]
    assert "Editorial instructions: Write a short neutral digest without meta notes." in captured_prompts[0]


def test_topic_scout_task_retrieval_uses_goal_text_without_editorial_instructions(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    captured_topics: list[str] = []

    def fake_collect_topic_sources(
        topic: str,
        *,
        seed_urls=None,
        max_results=None,
        preferred_domains=None,
        blocked_domains=None,
        blocked_url_patterns=None,
    ):
        captured_topics.append(topic)
        return [
            {
                "url": "https://example.com/story",
                "title": "Story",
                "text_excerpt": "Story body",
                "preview_image_url": "https://example.com/story.jpg",
                "image_urls": ["https://example.com/story.jpg"],
                "published_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "retrieval_score": 0.9,
                "retrieval_combined_score": 0.95,
            }
        ]

    monkeypatch.setattr(
        "postbridge.agent.graphs.source_package.collect_topic_sources",
        fake_collect_topic_sources,
    )
    monkeypatch.setattr("postbridge.workers.tasks.process_publication_target_task.delay", lambda *a, **k: None)

    task_response = client.post(
        "/internal/service/agent/tasks",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "topic_scout",
            "goal_text": "news of Novosibirsk",
            "editorial_instructions": "Add public images and keep the post short.",
            "max_candidates_per_run": 1,
            "autonomy_mode": "guarded_auto_publish",
            "created_by": "user-1",
        },
    )
    assert task_response.status_code == 200, task_response.text

    run_response = client.post(
        f"/internal/service/agent/tasks/{task_response.json()['id']}/run",
        headers=_svc_headers(),
    )
    assert run_response.status_code == 200, run_response.text
    assert captured_topics == ["news of Novosibirsk"]


def test_topic_scout_task_auto_publish_blocks_when_sources_and_images_are_missing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "postbridge.agent.graphs.topic_scout.collect_topic_evidence",
        lambda session, tenant_id, channel_id, topic, seed_urls=None, workspace_policy=None: [],
    )
    monkeypatch.setattr(
        "postbridge.agent.graphs.source_package.collect_topic_sources",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        lambda self, *, messages, temperature=0.2: (
            {
                "candidates": [
                    {
                        "topic": "Novosibirsk daily",
                        "headline": "Novosibirsk headline",
                        "body_markdown": "Draft body",
                        "summary": "Summary",
                        "why_now": "Now",
                        "style_fit_summary": "Fits channel",
                        "source_bundle": {},
                        "scores": {"relevance": 0.9},
                        "risk_flags": [],
                    }
                ]
            },
            {"total_tokens": 21},
        ),
    )

    task_response = client.post(
        "/internal/service/agent/tasks",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "topic_scout",
            "goal_text": "news of Novosibirsk",
            "editorial_instructions": "Add public images to each post.",
            "max_candidates_per_run": 1,
            "autonomy_mode": "guarded_auto_publish",
            "created_by": "user-1",
        },
    )
    assert task_response.status_code == 200, task_response.text

    run_response = client.post(
        f"/internal/service/agent/tasks/{task_response.json()['id']}/run",
        headers=_svc_headers(),
    )
    assert run_response.status_code == 200, run_response.text
    payload = run_response.json()["run"]
    assert payload["status"] == "awaiting_review"
    assert payload["auto_materialized"] == []
    assert len(payload["review_items"]) == 1
    assert payload["guardrail_blocks"]
    assert "blocked_risk_flag:missing_image_candidates" in payload["guardrail_blocks"][0]["reasons"]
    assert "blocked_risk_flag:no_sources" in payload["guardrail_blocks"][0]["reasons"]

    session = SESSION_LOCAL()
    try:
        assert session.query(ContentItemOrm).count() == 0
    finally:
        session.close()


def test_guarded_auto_publish_manual_approval_materializes_draft_after_guardrail_block(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "postbridge.agent.retrieval.collect_topic_sources",
        lambda *args, **kwargs: [
            {
                "url": "https://ngs.ru/news-1",
                "title": "Novosibirsk local news",
                "text_excerpt": "Fresh local news",
                "published_at": "2026-04-28T09:00:00Z",
                "source_type": "local_news",
                "image_urls": [],
                "preview_image_url": None,
            }
        ],
    )
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        lambda *args, **kwargs: (
            {
                "candidates": [
                    {
                        "topic": "Local Novosibirsk news",
                        "headline": "Heating and weather in Novosibirsk today",
                        "body_markdown": "Draft body for editor",
                        "summary": "Draft summary",
                        "why_now": "Fresh local update",
                        "style_fit_summary": "Fits local digest",
                        "source_bundle": {"primary_sources": ["https://ngs.ru/news-1"]},
                        "scores": {
                            "relevance": 0.9,
                            "freshness": 0.9,
                            "clickability": 0.8,
                            "trust": 0.6,
                            "source_quality": 0.6,
                            "source_corroboration": 1.0,
                            "source_conflict": 0.0,
                            "source_count": 1,
                        },
                        "risk_flags": ["missing_image_candidates"],
                    }
                ]
            },
            {"total_tokens": 42},
        ),
    )

    task_response = client.post(
        "/internal/service/agent/tasks",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "topic_scout",
            "goal_text": "news of Novosibirsk",
            "editorial_instructions": "Add public images to each post.",
            "max_candidates_per_run": 1,
            "autonomy_mode": "guarded_auto_publish",
            "created_by": "user-1",
        },
    )
    assert task_response.status_code == 200, task_response.text

    run_response = client.post(
        f"/internal/service/agent/tasks/{task_response.json()['id']}/run",
        headers=_svc_headers(),
    )
    assert run_response.status_code == 200, run_response.text
    payload = run_response.json()["run"]
    assert payload["status"] == "awaiting_review"
    assert payload["review_items"]

    queue = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    assert len(queue) == 1

    resolve_response = client.post(
        f"/internal/service/review-queue/{queue[0]['id']}/resolve",
        headers=_svc_headers(),
        json={"decision": "approved", "review_action": "approve_as_is", "reviewer_id": "u1"},
    )
    assert resolve_response.status_code == 200, resolve_response.text
    resolved = resolve_response.json()
    assert resolved["status"] == "approved"
    assert resolved["materialization"]["content_item_id"]
    assert resolved["materialization"]["materialization"] in {
        "created_editorial_draft_content_item",
        "created_draft_content_item",
    }

    session = SESSION_LOCAL()
    try:
        candidate = session.get(ContentCandidateOrm, resolved["candidate_id"])
        assert candidate is not None
        assert candidate.content_item_id == resolved["materialization"]["content_item_id"]
        content = session.get(ContentItemOrm, resolved["materialization"]["content_item_id"])
        assert content is not None
        assert content.status == "draft"
        assert content.title == "Heating and weather in Novosibirsk today"
    finally:
        session.close()


def test_topic_scout_task_auto_materializes_public_image_when_sources_exist(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    seeded_sources = [
        {
            "url": "https://example.com/story",
            "title": "Story",
            "text_excerpt": "Story body",
            "preview_image_url": "https://example.com/story.jpg",
            "image_urls": ["https://example.com/story.jpg"],
            "published_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "retrieval_score": 0.9,
            "retrieval_combined_score": 0.95,
        },
        {
            "url": "https://alt.example.com/story-2",
            "title": "Story 2",
            "text_excerpt": "Story body 2",
            "preview_image_url": "https://alt.example.com/story-2.jpg",
            "image_urls": ["https://alt.example.com/story-2.jpg"],
            "published_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "retrieval_score": 0.88,
            "retrieval_combined_score": 0.93,
        },
    ]
    monkeypatch.setattr(
        "postbridge.agent.graphs.topic_scout.collect_topic_evidence",
        lambda session, tenant_id, channel_id, topic, seed_urls=None, workspace_policy=None: list(seeded_sources),
    )
    monkeypatch.setattr("postbridge.workers.tasks.process_publication_target_task.delay", lambda *a, **k: None)
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        lambda self, *, messages, temperature=0.2: (
            {
                "candidates": [
                    {
                        "topic": "Novosibirsk daily",
                        "headline": "Novosibirsk headline",
                        "body_markdown": "Draft body",
                        "summary": "Summary",
                        "why_now": "Now",
                        "style_fit_summary": "Fits channel",
                        "source_bundle": {},
                        "scores": {"relevance": 0.9},
                        "risk_flags": [],
                    }
                ]
            },
            {"total_tokens": 21},
        ),
    )

    task_response = client.post(
        "/internal/service/agent/tasks",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "topic_scout",
            "goal_text": "news of Novosibirsk",
            "editorial_instructions": "Add public images to each post.",
            "max_candidates_per_run": 1,
            "autonomy_mode": "guarded_auto_publish",
            "created_by": "user-1",
        },
    )
    assert task_response.status_code == 200, task_response.text

    run_response = client.post(
        f"/internal/service/agent/tasks/{task_response.json()['id']}/run",
        headers=_svc_headers(),
    )
    assert run_response.status_code == 200, run_response.text
    payload = run_response.json()["run"]
    assert payload["status"] == "completed"
    assert len(payload["auto_materialized"]) == 1

    session = SESSION_LOCAL()
    try:
        content = session.get(ContentItemOrm, payload["auto_materialized"][0]["content_item_id"])
        assert content is not None
        assert content.media_url == "https://example.com/story.jpg"
        assert content.media_urls == ["https://example.com/story.jpg"]
    finally:
        session.close()


def test_agent_task_requires_created_by(client: TestClient):
    response = client.post(
        "/internal/service/agent/tasks",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "topic_scout",
            "goal_text": "Find fresh Novosibirsk news",
        },
    )
    assert response.status_code == 422, response.text


def test_full_manual_approval_does_not_materialize(client: TestClient):
    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Manual only",
            "autonomy_mode": "full_manual",
        },
    )
    assert response.status_code == 200, response.text
    queue = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    resolve_response = client.post(
        f"/internal/service/review-queue/{queue[0]['id']}/resolve",
        headers=_svc_headers(),
        json={"decision": "approved", "reviewer_id": "u2"},
    )
    assert resolve_response.status_code == 200, resolve_response.text
    payload = resolve_response.json()
    assert payload["status"] == "approved"
    assert payload["materialization"] == {}


def test_guarded_auto_publish_skips_review_queue_and_materializes(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    delayed: list[tuple[str, str | None]] = []

    def fake_delay(target_id: str, correlation_id: str | None = None):
        delayed.append((target_id, correlation_id))

    monkeypatch.setattr("postbridge.workers.tasks.process_publication_target_task.delay", fake_delay)
    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Autopublish this",
            "autonomy_mode": "guarded_auto_publish",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["review_items"] == []
    assert len(payload["auto_materialized"]) == 1
    assert payload["guardrail_blocks"] == []
    assert payload["auto_materialized"][0]["materialization"] == "created_and_dispatched_content_plan_and_targets"
    assert len(delayed) == 1


def test_post_copilot_guarded_auto_publish_applies_with_guardrail_notes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    delayed: list[tuple[str, str | None]] = []

    def fake_delay(target_id: str, correlation_id: str | None = None):
        delayed.append((target_id, correlation_id))

    monkeypatch.setattr("postbridge.workers.tasks.process_publication_target_task.delay", fake_delay)
    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Autopublish but only if safe",
            "autonomy_mode": "guarded_auto_publish",
            "seed_urls": ["https://example.com/a", "https://example.com/b"],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert len(payload["auto_materialized"]) == 1
    assert payload["review_items"] == []
    assert len(payload["guardrail_blocks"]) == 1
    assert len(delayed) == 1
    assert payload["guardrail_blocks"][0]["reasons"]
    queue = client.get("/internal/service/review-queue", headers=_svc_headers())
    assert queue.status_code == 200, queue.text
    assert queue.json() == []


def test_guarded_auto_publish_blocks_repeated_angle(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    delayed: list[tuple[str, str | None]] = []

    def fake_delay(target_id: str, correlation_id: str | None = None):
        delayed.append((target_id, correlation_id))

    monkeypatch.setattr("postbridge.workers.tasks.process_publication_target_task.delay", fake_delay)
    first = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Autopublish first angle",
            "autonomy_mode": "guarded_auto_publish",
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "completed"

    second = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Autopublish repeated angle",
            "autonomy_mode": "guarded_auto_publish",
        },
    )
    assert second.status_code == 200, second.text
    payload = second.json()
    assert payload["status"] == "completed"
    assert payload["guardrail_blocks"]
    reasons = payload["guardrail_blocks"][0]["reasons"]
    assert any("repeated_angle" in reason or "angle_pressure_above_threshold" in reason for reason in reasons)
    assert len(delayed) == 2


def test_guarded_auto_publish_blocks_repeated_angle_family(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    delayed: list[tuple[str, str | None]] = []

    def fake_delay(target_id: str, correlation_id: str | None = None):
        delayed.append((target_id, correlation_id))

    monkeypatch.setattr("postbridge.workers.tasks.process_publication_target_task.delay", fake_delay)
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        lambda self, *, messages, temperature=0.2: (
            {
                "topic": "Metro reaches new district",
                "headline": "Novosibirsk metro expansion reaches new district",
                "body_markdown": "Manual draft",
                "summary": "Manual summary",
                "why_now": "Timely",
                "style_fit_summary": "Good fit",
                "source_bundle": {"primary_sources": ["https://example.com/a"]},
                "scores": {"relevance": 0.95, "novelty": 0.9, "style_fit": 0.88},
                "risk_flags": [],
            },
            {"total_tokens": 42},
        ),
    )
    first = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Autopublish first angle family",
            "autonomy_mode": "guarded_auto_publish",
        },
    )
    assert first.status_code == 200, first.text

    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        lambda self, *, messages, temperature=0.2: (
            {
                "topic": "Metro reaches another district",
                "headline": "Metro expansion reaches another district in Novosibirsk",
                "body_markdown": "Manual draft",
                "summary": "Manual summary",
                "why_now": "Timely",
                "style_fit_summary": "Good fit",
                "source_bundle": {"primary_sources": ["https://example.com/a"]},
                "scores": {"relevance": 0.95, "novelty": 0.9, "style_fit": 0.88},
                "risk_flags": [],
            },
            {"total_tokens": 42},
        ),
    )
    second = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Autopublish second angle family variant",
            "autonomy_mode": "guarded_auto_publish",
        },
    )
    assert second.status_code == 200, second.text
    payload = second.json()
    assert payload["status"] == "completed"
    reasons = payload["guardrail_blocks"][0]["reasons"]
    assert any("angle_pressure_above_threshold" in reason for reason in reasons)
    assert len(delayed) == 2


def test_channel_policy_override_relaxes_guarded_auto_publish_guardrails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    delayed: list[tuple[str, str | None]] = []

    def fake_delay(target_id: str, correlation_id: str | None = None):
        delayed.append((target_id, correlation_id))

    monkeypatch.setattr("postbridge.workers.tasks.process_publication_target_task.delay", fake_delay)
    policy = client.put(
        "/internal/service/agent/policies",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "policy": {
                "min_source_quality": 0.0,
                "min_source_corroboration": 0.0,
                "max_source_conflict": 1.0,
                "blocked_risk_flags": [],
            },
        },
    )
    assert policy.status_code == 200, policy.text
    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Autopublish but allow low-evidence channels",
            "autonomy_mode": "guarded_auto_publish",
            "seed_urls": ["https://example.com/a", "https://example.com/b"],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["review_items"] == []
    assert payload["guardrail_blocks"] == []
    assert len(payload["auto_materialized"]) == 1
    assert payload["policy_resolution"]["sources"]
    listed = client.get("/internal/service/agent/policies", headers=_svc_headers())
    assert listed.status_code == 200, listed.text
    assert any(item["channel_id"] == CHANNEL for item in listed.json())
    quality = client.get(
        f"/internal/service/agent/analytics/quality?channel_id={CHANNEL}",
        headers=_svc_headers(),
    )
    assert quality.status_code == 200, quality.text
    assert quality.json()["policy_overrides"]


def test_embedding_duplicate_signal_on_similar_followup_run(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("postbridge.workers.tasks.process_publication_target_task.delay", lambda *a, **k: None)
    first = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Manual only",
            "autonomy_mode": "guarded_auto_publish",
        },
    )
    assert first.status_code == 200, first.text

    second = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Manual only",
            "autonomy_mode": "draft_approval",
        },
    )
    assert second.status_code == 200, second.text
    candidate_id = second.json()["candidates"][0]["candidate_id"]
    detail = client.get(f"/internal/service/agent/candidates/{candidate_id}", headers=_svc_headers())
    assert detail.status_code == 200, detail.text
    assert "embedding_duplicate" in detail.json()["risk_flags"]


def test_reindex_channel_embeddings_endpoint(client: TestClient):
    session = SESSION_LOCAL()
    try:
        content = ContentItemOrm(
            id=str(uuid4()),
            tenant_id=TENANT,
            author_user_id=None,
            source_type="manual",
            title="Historic post",
            body_markdown="This old post should be embedded.",
            status="draft",
        )
        session.add(content)
        session.commit()
    finally:
        session.close()

    response = client.post(
        f"/internal/service/agent/reindex/channel/{CHANNEL}",
        headers=_svc_headers(),
        json={"limit": 10, "async_mode": False},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["indexed"] >= 1

    session = SESSION_LOCAL()
    try:
        embeddings = session.query(ContentEmbeddingOrm).filter(ContentEmbeddingOrm.entity_type == "content_item").all()
        assert len(embeddings) >= 1
    finally:
        session.close()


def test_reindex_single_content_item_embedding_endpoint(client: TestClient):
    session = SESSION_LOCAL()
    try:
        content = ContentItemOrm(
            id=str(uuid4()),
            tenant_id=TENANT,
            author_user_id=None,
            source_type="manual",
            title="Single content",
            body_markdown="Single content for reindex.",
            status="draft",
        )
        session.add(content)
        session.commit()
        content_id = content.id
    finally:
        session.close()

    response = client.post(
        f"/internal/service/agent/reindex/content-items/{content_id}",
        headers=_svc_headers(),
        json={"channel_id": CHANNEL, "async_mode": False},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["indexed"] == 1


def test_reindex_channel_embeddings_supports_chunked_offset(client: TestClient):
    session = SESSION_LOCAL()
    try:
        for idx in range(3):
            session.add(
                ContentItemOrm(
                    id=str(uuid4()),
                    tenant_id=TENANT,
                    author_user_id=None,
                    source_type="manual",
                    title=f"Chunk content {idx}",
                    body_markdown=f"Chunk body {idx}",
                    status="draft",
                )
            )
        session.commit()
    finally:
        session.close()

    first = client.post(
        f"/internal/service/agent/reindex/channel/{CHANNEL}",
        headers=_svc_headers(),
        json={"limit": 2, "offset": 0, "async_mode": False},
    )
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert first_payload["status"] == "completed"
    assert first_payload["scanned_count"] == 2
    assert first_payload["has_more"] is True
    assert first_payload["next_offset"] == 2

    second = client.post(
        f"/internal/service/agent/reindex/channel/{CHANNEL}",
        headers=_svc_headers(),
        json={"limit": 2, "offset": 2, "async_mode": False},
    )
    assert second.status_code == 200, second.text
    second_payload = second.json()
    assert second_payload["status"] == "completed"
    assert second_payload["offset"] == 2
    assert second_payload["scanned_count"] >= 1


def test_agent_analytics_overview_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("postbridge.workers.tasks.process_publication_target_task.delay", lambda *a, **k: None)

    def fake_overview_invoke_json(self, *, messages, temperature=0.2):
        prompt = "\n".join(msg["content"] for msg in messages if msg["role"] == "user")
        if "Instruction: Auto publish" in prompt:
            return (
                {
                    "topic": "Auto topic",
                    "headline": "Auto headline",
                    "body_markdown": "Auto draft",
                    "summary": "Auto summary",
                    "why_now": "Timely",
                    "style_fit_summary": "Good fit",
                    "source_bundle": {"primary_sources": ["https://example.com/a"]},
                    "scores": {"relevance": 0.95, "novelty": 0.9, "style_fit": 0.88},
                    "risk_flags": [],
                },
                {"total_tokens": 42},
            )
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
        fake_overview_invoke_json,
    )
    review_run = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Inspect details",
            "autonomy_mode": "draft_approval",
        },
    )
    assert review_run.status_code == 200, review_run.text
    queue = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    resolved = client.post(
        f"/internal/service/review-queue/{queue[0]['id']}/resolve",
        headers=_svc_headers(),
        json={"decision": "approved", "reviewer_id": "u3"},
    )
    assert resolved.status_code == 200, resolved.text

    auto_run = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Auto publish",
            "autonomy_mode": "guarded_auto_publish",
        },
    )
    assert auto_run.status_code == 200, auto_run.text

    response = client.get(
        f"/internal/service/agent/analytics/overview?channel_id={CHANNEL}",
        headers=_svc_headers(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["channel_id"] == CHANNEL
    assert payload["runs_total"] == 2
    assert payload["runs_by_status"]["awaiting_review"] == 1
    assert payload["runs_by_status"]["completed"] == 1
    assert payload["review_items_total"] == 1
    assert payload["review_approved_total"] == 1
    assert payload["candidates_total"] == 2
    assert payload["converted_candidates"] == 2
    assert payload["candidate_conversion_rate"] == 1.0
    assert payload["avg_review_resolution_seconds"] is not None


def test_agent_analytics_timeseries_endpoint(client: TestClient):
    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Timeseries run",
        },
    )
    assert response.status_code == 200, response.text

    series_response = client.get(
        f"/internal/service/agent/analytics/timeseries?channel_id={CHANNEL}&days=3",
        headers=_svc_headers(),
    )
    assert series_response.status_code == 200, series_response.text
    payload = series_response.json()
    assert payload["channel_id"] == CHANNEL
    assert payload["days"] == 3
    assert len(payload["series"]) == 3
    assert any(day["runs"]["total"] >= 1 for day in payload["series"])
    assert any(day["reviews"]["created"] >= 1 for day in payload["series"])


def test_agent_cleanup_endpoint_keeps_pending_review_items(client: TestClient):
    resolved_run = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Old resolved run",
            "autonomy_mode": "draft_approval",
        },
    )
    assert resolved_run.status_code == 200, resolved_run.text
    pending_run = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Old pending run",
            "autonomy_mode": "draft_approval",
        },
    )
    assert pending_run.status_code == 200, pending_run.text

    queue = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    assert len(queue) == 2
    resolved_item = next(item for item in queue if item["agent_run_id"] == resolved_run.json()["agent_run_id"])
    pending_item = next(item for item in queue if item["agent_run_id"] == pending_run.json()["agent_run_id"])

    approve = client.post(
        f"/internal/service/review-queue/{resolved_item['id']}/resolve",
        headers=_svc_headers(),
        json={"decision": "approved", "reviewer_id": "cleanup"},
    )
    assert approve.status_code == 200, approve.text

    session = SESSION_LOCAL()
    try:
        old_ts = datetime.now(UTC) - timedelta(days=31)
        session.query(ContentEmbeddingOrm).filter(
            ContentEmbeddingOrm.entity_type == "candidate",
            ContentEmbeddingOrm.entity_id.in_([resolved_item["candidate_id"], pending_item["candidate_id"]]),
        ).update({"created_at": old_ts, "updated_at": old_ts}, synchronize_session=False)

        session.query(AgentRunOrm).filter(AgentRunOrm.id == resolved_run.json()["agent_run_id"]).update(
            {"created_at": old_ts, "updated_at": old_ts, "started_at": old_ts, "completed_at": old_ts},
            synchronize_session=False,
        )
        session.query(AgentRunOrm).filter(AgentRunOrm.id == pending_run.json()["agent_run_id"]).update(
            {"created_at": old_ts, "updated_at": old_ts, "started_at": old_ts},
            synchronize_session=False,
        )
        session.query(ReviewQueueItemOrm).filter(ReviewQueueItemOrm.id == resolved_item["id"]).update(
            {"created_at": old_ts, "resolved_at": old_ts},
            synchronize_session=False,
        )
        session.query(ReviewQueueItemOrm).filter(ReviewQueueItemOrm.id == pending_item["id"]).update(
            {"created_at": old_ts},
            synchronize_session=False,
        )
        session.commit()
    finally:
        session.close()

    cleanup_response = client.post(
        "/internal/service/agent/cleanup",
        headers=_svc_headers(),
        json={"retention_days": 30, "async_mode": False},
    )
    assert cleanup_response.status_code == 200, cleanup_response.text
    payload = cleanup_response.json()
    assert payload["deleted_runs"] == 1
    assert payload["deleted_review_items"] == 1
    assert payload["deleted_candidate_embeddings"] >= 1

    queue_after = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    assert len(queue_after) == 1
    assert queue_after[0]["id"] == pending_item["id"]


def test_agent_quality_analytics_endpoint(client: TestClient):
    scout = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "topic_scout",
            "topic_definition": "Find fresh Novosibirsk news",
            "max_candidates": 3,
            "autonomy_mode": "draft_approval",
        },
    )
    assert scout.status_code == 200, scout.text

    queue = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    assert len(queue) == 3
    approve = client.post(
        f"/internal/service/review-queue/{queue[0]['id']}/resolve",
        headers=_svc_headers(),
        json={"decision": "approved", "review_action": "approve_after_fact_check", "reviewer_id": "qa-1"},
    )
    assert approve.status_code == 200, approve.text
    reject = client.post(
        f"/internal/service/review-queue/{queue[1]['id']}/resolve",
        headers=_svc_headers(),
        json={"decision": "rejected", "review_action": "reject_conflict", "reviewer_id": "qa-2"},
    )
    assert reject.status_code == 200, reject.text

    response = client.get(
        f"/internal/service/agent/analytics/quality?channel_id={CHANNEL}",
        headers=_svc_headers(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["channel_id"] == CHANNEL
    assert payload["sources"]
    example = next(item for item in payload["sources"] if item["domain"] == "example.com")
    assert example["candidate_count"] == 3
    assert example["converted_count"] == 1
    assert example["rejected_count"] == 1
    assert example["conversion_rate"] == pytest.approx(1 / 3, rel=1e-3)
    assert example["trust_label"] in {"mixed", "trusted", "risky", "insufficient_data"}
    assert example["repeated_domain_pressure"] >= 0.0
    assert payload["source_types"]
    assert payload["source_types"][0]["trust_label"] in {"mixed", "trusted", "risky", "insufficient_data"}
    assert payload["angles"]
    assert payload["angles"][0]["candidate_count"] >= 1
    assert payload["angles"][0]["avg_alignment"] >= 0.0
    assert payload["angles"][0]["angle_family"]
    assert payload["themes"]
    assert payload["themes"][0]["theme"]
    assert payload["review_actions"]
    assert payload["review_actions"][0]["review_action"]
    assert payload["workflow_presets"]
    assert payload["workflow_presets"][0]["workflow_preset"]
    assert payload["source_diversity"]["unique_domains"] >= 1
    assert payload["source_diversity"]["concentration_label"] in {"low", "medium", "high"}
    assert payload["source_diversity"]["novelty_score"] >= 0.0
    assert payload["source_diversity"]["repeated_domain_pressure"] >= 0.0
    assert payload["source_diversity"]["top_domains"]
    assert payload["source_agreement"]["corroborated_share"] >= 0.0
    assert payload["source_agreement"]["single_source_candidates"] >= 0
    assert payload["source_agreement"]["disagreement_share"] >= 0.0
    assert payload["source_agreement"]["conflict_share"] >= 0.0
    assert "top_conflict_examples" in payload["source_agreement"]

    assert payload["models"]
    model = next(item for item in payload["models"] if item["model"] == "gpt-test")
    assert model["run_count"] == 1
    assert model["candidate_count"] == 3
    assert model["converted_count"] == 1
    assert model["rejected_count"] == 1
    assert payload["policies"]
    policy = next(item for item in payload["policies"] if item["policy"] == "draft_approval")
    assert policy["run_count"] == 1
    assert policy["candidate_count"] == 3
    assert policy["converted_count"] == 1
    assert policy["rejected_count"] == 1
    assert payload["policy_recommendations"]
    recommendation = next(item for item in payload["policy_recommendations"] if item["channel_id"] == CHANNEL)
    assert recommendation["current_policy"] == "draft_approval"
    assert recommendation["recommended_policy"] in {"draft_approval", "plan_approval", "guarded_auto_publish"}
    assert recommendation["confidence"] in {"low", "medium", "high"}
    assert recommendation["base_confidence"] in {"low", "medium", "high"}
    assert recommendation["confidence_explanation"]
    assert recommendation["rationale_weights"]["history"] >= 0.0
    assert recommendation["rationale_weights"]["quality"] >= 0.0
    assert recommendation["rationale_weights"]["recency"] >= 0.0
    assert recommendation["rationale_weights"]["source_quality"] >= 0.0
    assert recommendation["rationale_weights"]["conflict_penalty"] >= 0.0
    assert recommendation["avg_source_quality"] >= 0.0
    assert recommendation["avg_source_conflict"] >= 0.0
    assert recommendation["single_source_share"] >= 0.0
    assert payload["channel_source_quality"]
    runs = client.get("/internal/service/agent/runs", headers=_svc_headers())
    assert runs.status_code == 200, runs.text
    run_id = runs.json()[0]["id"]
    run_detail = client.get(f"/internal/service/agent/runs/{run_id}", headers=_svc_headers())
    assert run_detail.status_code == 200, run_detail.text
    candidates = client.get(f"/internal/service/agent/candidates?run_id={run_id}", headers=_svc_headers())
    assert candidates.status_code == 200, candidates.text
    candidate = candidates.json()[0]
    assert candidate["scores"]["source_quality"] >= 0.0
    assert candidate["scores"]["source_corroboration"] >= 0.0
    assert candidate["scores"]["source_freshness"] >= 0.0
    assert candidate["scores"]["source_conflict"] >= 0.0
    assert candidate["scores"]["angle_alignment"] >= 0.0


def test_agent_quality_analytics_supports_days_filter(client: TestClient):
    scout = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "topic_scout",
            "topic_definition": "Find fresh Novosibirsk news",
            "max_candidates": 3,
            "autonomy_mode": "draft_approval",
        },
    )
    assert scout.status_code == 200, scout.text
    queue = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    approve = client.post(
        f"/internal/service/review-queue/{queue[0]['id']}/resolve",
        headers=_svc_headers(),
        json={"decision": "approved", "review_action": "approve_as_is", "reviewer_id": "qa-days"},
    )
    assert approve.status_code == 200, approve.text

    session = SESSION_LOCAL()
    try:
        old_ts = datetime.now(UTC) - timedelta(days=45)
        session.query(AgentRunOrm).update(
            {"created_at": old_ts, "updated_at": old_ts, "started_at": old_ts, "completed_at": old_ts},
            synchronize_session=False,
        )
        session.query(ContentCandidateOrm).update(
            {"created_at": old_ts, "updated_at": old_ts},
            synchronize_session=False,
        )
        session.query(ReviewQueueItemOrm).update(
            {"created_at": old_ts, "resolved_at": old_ts},
            synchronize_session=False,
        )
        session.commit()
    finally:
        session.close()

    recent = client.get(
        f"/internal/service/agent/analytics/quality?channel_id={CHANNEL}&days=7",
        headers=_svc_headers(),
    )
    assert recent.status_code == 200, recent.text
    payload = recent.json()
    assert payload["days"] == 7
    assert payload["cutoff"]
    assert payload["sources"] == []


def test_agent_quality_analytics_policy_slices_include_multiple_modes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("postbridge.workers.tasks.process_publication_target_task.delay", lambda *a, **k: None)
    manual = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Manual review policy",
            "autonomy_mode": "draft_approval",
        },
    )
    assert manual.status_code == 200, manual.text
    auto = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Auto policy",
            "autonomy_mode": "guarded_auto_publish",
        },
    )
    assert auto.status_code == 200, auto.text

    response = client.get(
        f"/internal/service/agent/analytics/quality?channel_id={CHANNEL}",
        headers=_svc_headers(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    draft_policy = next(item for item in payload["policies"] if item["policy"] == "draft_approval")
    auto_policy = next(item for item in payload["policies"] if item["policy"] == "guarded_auto_publish")
    assert draft_policy["run_count"] == 1
    assert draft_policy["candidate_count"] == 1
    assert draft_policy["awaiting_review_runs"] == 1
    assert auto_policy["run_count"] == 1
    assert auto_policy["candidate_count"] == 1
    assert auto_policy["completed_runs"] == 1
    assert auto_policy["converted_count"] == 1
    channel_policy = next(
        item for item in payload["channel_policies"] if item["channel_id"] == CHANNEL and item["policy"] == "guarded_auto_publish"
    )
    assert channel_policy["run_count"] == 1
    assert channel_policy["converted_count"] == 1


def test_policy_recommendation_confidence_decays_for_stale_history(client: TestClient):
    scout = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "topic_scout",
            "topic_definition": "Find fresh Novosibirsk news",
            "max_candidates": 3,
            "autonomy_mode": "draft_approval",
        },
    )
    assert scout.status_code == 200, scout.text
    queue = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    for item in queue:
        approve = client.post(
            f"/internal/service/review-queue/{item['id']}/resolve",
            headers=_svc_headers(),
            json={"decision": "approved", "reviewer_id": "stale"},
        )
        assert approve.status_code == 200, approve.text

    session = SESSION_LOCAL()
    try:
        old_ts = datetime.now(UTC) - timedelta(days=45)
        session.query(AgentRunOrm).update(
            {"created_at": old_ts, "updated_at": old_ts, "started_at": old_ts, "completed_at": old_ts},
            synchronize_session=False,
        )
        session.commit()
    finally:
        session.close()

    response = client.get(
        f"/internal/service/agent/analytics/quality?channel_id={CHANNEL}",
        headers=_svc_headers(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    recommendation = next(item for item in payload["policy_recommendations"] if item["channel_id"] == CHANNEL)
    assert recommendation["base_confidence"] in {"medium", "high", "low"}
    assert recommendation["confidence"] in {"low", "medium", "high"}
    confidence_rank = {"low": 0, "medium": 1, "high": 2}
    assert confidence_rank[recommendation["confidence"]] <= confidence_rank[recommendation["base_confidence"]]


def test_topic_scout_body_sanitizer_removes_meta_sections():
    body = (
        "Novosibirsk update intro.\n\n"
        "### What this means\n"
        "Residents should expect route changes.\n\n"
        "### Illustration\n"
        "Use a public city photo from Unsplash.\n\n"
        "### Post format\n"
        "Short digest with 3 facts.\n"
    )
    assert _sanitize_topic_scout_body_markdown(body) == (
        "Novosibirsk update intro.\n\n"
        "### What this means\n"
        "Residents should expect route changes."
    )


def test_topic_scout_run_sanitizes_candidate_body_before_review(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        lambda self, *, messages, temperature=0.2: (
            {
                "candidates": [
                    {
                        "topic": "Novosibirsk daily",
                        "headline": "Novosibirsk headline",
                        "body_markdown": (
                            "City update.\n\n"
                            "### Иллюстрация\n"
                            "Подобрать фото города.\n\n"
                            "### Формат поста\n"
                            "Короткий дайджест."
                        ),
                        "summary": "Summary",
                        "why_now": "Now",
                        "style_fit_summary": "Fits",
                        "source_bundle": {"primary_sources": ["https://example.com/1"]},
                        "scores": {"relevance": 0.9},
                        "risk_flags": [],
                    }
                ]
            },
            {"total_tokens": 33},
        ),
    )

    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "topic_scout",
            "topic_definition": "Find fresh Novosibirsk news and add image notes",
            "max_candidates": 1,
            "autonomy_mode": "draft_approval",
        },
    )
    assert response.status_code == 200, response.text

    queue = client.get("/internal/service/review-queue", headers=_svc_headers())
    assert queue.status_code == 200, queue.text
    queue_payload = queue.json()
    assert len(queue_payload) == 1

    candidate_id = queue_payload[0]["candidate_id"]
    candidate_response = client.get(f"/internal/service/agent/candidates/{candidate_id}", headers=_svc_headers())
    assert candidate_response.status_code == 200, candidate_response.text
    candidate_payload = candidate_response.json()
    assert candidate_payload["body_markdown"] == "City update."


def test_topic_scout_auto_materializes_first_public_image_when_requested(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("postbridge.workers.tasks.process_publication_target_task.delay", lambda *a, **k: None)
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        lambda self, *, messages, temperature=0.2: (
            {
                "candidates": [
                    {
                        "topic": "Novosibirsk daily",
                        "headline": "Novosibirsk headline",
                        "body_markdown": "Draft body",
                        "summary": "Summary",
                        "why_now": "Now",
                        "style_fit_summary": "Fits",
                        "source_bundle": {},
                        "scores": {"relevance": 0.9},
                        "risk_flags": [],
                    }
                ]
            },
            {"total_tokens": 21},
        ),
    )
    monkeypatch.setattr(
        "postbridge.agent.graphs.topic_scout.collect_topic_evidence",
        lambda session, tenant_id, channel_id, topic, seed_urls=None, workspace_policy=None: [
            {
                "url": "https://example.com/story",
                "title": "Story",
                "text_excerpt": "Story",
                "preview_image_url": "https://example.com/story.jpg",
                "published_at": datetime.now(UTC).isoformat(),
                "retrieval_combined_score": 0.95,
            },
            {
                "url": "https://alt.example.com/story-2",
                "title": "Story 2",
                "text_excerpt": "Story 2",
                "preview_image_url": "https://alt.example.com/story-2.jpg",
                "published_at": datetime.now(UTC).isoformat(),
                "retrieval_combined_score": 0.9,
            }
        ],
    )

    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "topic_scout",
            "topic_definition": "Find fresh Novosibirsk news",
            "max_candidates": 1,
            "autonomy_mode": "guarded_auto_publish",
            "image_request": True,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    materialization = payload["auto_materialized"][0]

    session = SESSION_LOCAL()
    try:
        content = session.get(ContentItemOrm, materialization["content_item_id"])
        assert content is not None
        assert content.media_url == "https://example.com/story.jpg"
        assert content.media_urls == ["https://example.com/story.jpg"]
    finally:
        session.close()


def test_topic_scout_task_config_can_disable_images(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("postbridge.workers.tasks.process_publication_target_task.delay", lambda *a, **k: None)
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        lambda self, *, messages, temperature=0.2: (
            {
                "candidates": [
                    {
                        "topic": "No image topic",
                        "headline": "No image headline",
                        "body_markdown": "Draft body",
                        "summary": "Summary",
                        "why_now": "Now",
                        "style_fit_summary": "Fits",
                        "source_bundle": {},
                        "scores": {"relevance": 0.9},
                        "risk_flags": [],
                    }
                ]
            },
            {"total_tokens": 21},
        ),
    )
    monkeypatch.setattr(
        "postbridge.agent.graphs.topic_scout.collect_topic_evidence",
        lambda session, tenant_id, channel_id, topic, seed_urls=None, workspace_policy=None: [
            {
                "url": "https://example.com/story",
                "title": "Story",
                "text_excerpt": "Story",
                "preview_image_url": "https://example.com/story.jpg",
                "published_at": datetime.now(UTC).isoformat(),
                "retrieval_combined_score": 0.95,
            }
        ],
    )

    created = client.post(
        "/internal/service/agent/tasks",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "topic_scout",
            "goal_text": "Find fresh news",
            "editorial_instructions": "Please add a suitable image.",
            "max_candidates_per_run": 1,
            "autonomy_mode": "guarded_auto_publish",
            "search_image_mode": "none",
            "created_by": "tester",
        },
    )
    assert created.status_code == 200, created.text
    response = client.post(
        f"/internal/service/agent/tasks/{created.json()['id']}/run",
        headers=_svc_headers(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()["run"]
    assert payload["auto_materialized"] == []

    session = SESSION_LOCAL()
    try:
        candidate = (
            session.query(ContentCandidateOrm)
            .filter(ContentCandidateOrm.tenant_id == TENANT)
            .order_by(ContentCandidateOrm.created_at.desc())
            .first()
        )
        assert candidate is not None
        draft = json.loads(candidate.draft_json or "{}")
        assert draft.get("media_url") is None
        assert draft.get("media_urls") is None
        assert draft.get("cover_image_url") is None
    finally:
        session.close()


def test_topic_scout_task_config_generate_images_queues_media_job(
    client: TestClient,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    from postbridge.workers.media_generation_tasks import process_media_generation_job_task

    monkeypatch.setenv("MEDIA_STORAGE_TYPE", "local")
    monkeypatch.setenv("MEDIA_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("MEDIA_BASE_URL", "http://testserver/media")
    monkeypatch.setattr("postbridge.workers.tasks.process_publication_target_task.delay", lambda *a, **k: None)
    queued: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "postbridge.workers.media_generation_tasks.process_media_generation_job_task.delay",
        lambda job_id, correlation_id=None: queued.append((job_id, correlation_id)),
    )
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        lambda self, *, messages, temperature=0.2: (
            {
                "candidates": [
                    {
                        "topic": "Generated image topic",
                        "headline": "Generated image headline",
                        "body_markdown": "Draft body for generated image.",
                        "summary": "Generated image summary",
                        "why_now": "Now",
                        "style_fit_summary": "Fits",
                        "source_bundle": {},
                        "scores": {"relevance": 0.9},
                        "risk_flags": [],
                    }
                ]
            },
            {"total_tokens": 21},
        ),
    )
    monkeypatch.setattr(
        "postbridge.agent.graphs.topic_scout.collect_topic_evidence",
        lambda session, tenant_id, channel_id, topic, seed_urls=None, workspace_policy=None: [
            {
                "url": "https://example.com/story",
                "title": "Story",
                "text_excerpt": "Story",
                "published_at": datetime.now(UTC).isoformat(),
                "retrieval_combined_score": 0.95,
            },
            {
                "url": "https://alt.example.com/story-2",
                "title": "Story 2",
                "text_excerpt": "Story 2",
                "published_at": datetime.now(UTC).isoformat(),
                "retrieval_combined_score": 0.9,
            },
        ],
    )

    def fake_generate_image_bytes(
        prompt: str,
        *,
        model: str | None = None,
        correlation_id: str | None = None,
    ) -> ImageGenerationResult:
        assert "Generated image headline" in prompt
        assert "Draft body for generated image." in prompt
        assert correlation_id and correlation_id.startswith("agent-candidate:")
        return ImageGenerationResult(
            data=base64.b64decode("iVBORw0KGgo="),
            content_type="image/png",
            usage_tokens_charged=88,
        )

    monkeypatch.setattr(
        "postbridge.workers.media_generation_tasks.generate_image_bytes",
        fake_generate_image_bytes,
    )

    policy = client.put(
        "/internal/service/agent/policies",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "policy": {
                "min_source_quality": 0.0,
                "min_source_corroboration": 0.0,
                "max_source_conflict": 1.0,
                "blocked_risk_flags": [],
            },
        },
    )
    assert policy.status_code == 200, policy.text

    created = client.post(
        "/internal/service/agent/tasks",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "topic_scout",
            "goal_text": "Find fresh news",
            "max_candidates_per_run": 1,
            "autonomy_mode": "guarded_auto_publish",
            "search_image_mode": "generate",
            "created_by": "user-1",
        },
    )
    assert created.status_code == 200, created.text
    response = client.post(
        f"/internal/service/agent/tasks/{created.json()['id']}/run",
        headers=_svc_headers(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()["run"]
    materialization = payload["auto_materialized"][0]
    assert materialization["media_generation_job_id"]
    assert queued == [(materialization["media_generation_job_id"], f"agent-candidate:{materialization['candidate_id']}")]

    session = SESSION_LOCAL()
    try:
        job = session.get(MediaGenerationJobOrm, materialization["media_generation_job_id"])
        assert job is not None
        assert job.target == "media"
        assert job.requester_user_id == "user-1"
        content = session.get(ContentItemOrm, materialization["content_item_id"])
        assert content is not None
        assert content.media_url is None
    finally:
        session.close()

    result = process_media_generation_job_task.run(
        materialization["media_generation_job_id"],
        f"agent-candidate:{materialization['candidate_id']}",
    )
    assert result["status"] == "completed"

    session = SESSION_LOCAL()
    try:
        job = session.get(MediaGenerationJobOrm, materialization["media_generation_job_id"])
        assert job is not None
        assert job.url and job.url.startswith("http://testserver/media/")
        content = session.get(ContentItemOrm, materialization["content_item_id"])
        assert content is not None
        assert content.media_url == job.url
        assert content.media_urls == [job.url]
        structured = json.loads(content.body_structured_json or "{}")
        assert structured["cover_image_url"] == job.url
        assert structured["postbridge"]["cover_image_url"] == job.url
    finally:
        session.close()


def test_topic_scout_review_approval_applies_selected_public_image(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "postbridge.agent.providers.openai_compatible.OpenAICompatibleProvider.invoke_json",
        lambda self, *, messages, temperature=0.2: (
            {
                "candidates": [
                    {
                        "topic": "Novosibirsk daily",
                        "headline": "Novosibirsk headline",
                        "body_markdown": "Draft body",
                        "summary": "Summary",
                        "why_now": "Now",
                        "style_fit_summary": "Fits",
                        "source_bundle": {},
                        "scores": {"relevance": 0.9},
                        "risk_flags": [],
                    }
                ]
            },
            {"total_tokens": 21},
        ),
    )
    monkeypatch.setattr(
        "postbridge.agent.graphs.topic_scout.collect_topic_evidence",
        lambda session, tenant_id, channel_id, topic, seed_urls=None, workspace_policy=None: [
            {
                "url": "https://example.com/story",
                "title": "Story",
                "text_excerpt": "Story",
                "image_urls": ["https://example.com/story-1.jpg", "https://example.com/story-2.jpg"],
                "published_at": datetime.now(UTC).isoformat(),
                "retrieval_combined_score": 0.95,
            }
        ],
    )

    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "topic_scout",
            "topic_definition": "Find fresh Novosibirsk news",
            "max_candidates": 1,
            "autonomy_mode": "draft_approval",
            "image_request": True,
        },
    )
    assert response.status_code == 200, response.text
    queue = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    assert len(queue) == 1

    resolve = client.post(
        f"/internal/service/review-queue/{queue[0]['id']}/resolve",
        headers=_svc_headers(),
        json={
            "decision": "approved",
            "reviewer_id": "editor-1",
            "approved_image_urls": ["https://example.com/story-2.jpg"],
        },
    )
    assert resolve.status_code == 200, resolve.text
    materialization = resolve.json()["materialization"]

    session = SESSION_LOCAL()
    try:
        content = session.get(ContentItemOrm, materialization["content_item_id"])
        assert content is not None
        assert content.media_url == "https://example.com/story-2.jpg"
        assert content.media_urls == ["https://example.com/story-2.jpg"]
    finally:
        session.close()


def test_agent_cleanup_separates_trace_and_review_retention_and_cleans_orphan_fingerprints(client: TestClient):
    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Trace retention run",
            "autonomy_mode": "draft_approval",
        },
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["agent_run_id"]
    queue = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    review_item = queue[0]

    session = SESSION_LOCAL()
    try:
        old_trace_ts = datetime.now(UTC) - timedelta(days=8)
        orphan = ContentSourceFingerprintOrm(
            id=str(uuid4()),
            tenant_id=TENANT,
            channel_id=CHANNEL,
            source_url_hash="orphan-hash",
            canonical_url="https://example.com/orphan",
            source_title_hash=None,
            semantic_fingerprint=None,
            published_content_item_id=None,
            candidate_id=None,
            created_at=old_trace_ts,
        )
        session.add(orphan)
        session.query(AgentRunOrm).filter(AgentRunOrm.id == run_id).update(
            {"created_at": old_trace_ts, "updated_at": old_trace_ts, "started_at": old_trace_ts},
            synchronize_session=False,
        )
        session.query(ReviewQueueItemOrm).filter(ReviewQueueItemOrm.id == review_item["id"]).update(
            {"created_at": old_trace_ts},
            synchronize_session=False,
        )
        session.commit()
    finally:
        session.close()

    cleanup = client.post(
        "/internal/service/agent/cleanup",
        headers=_svc_headers(),
        json={
            "trace_retention_days": 7,
            "review_retention_days": 30,
            "fingerprint_retention_days": 7,
            "async_mode": False,
        },
    )
    assert cleanup.status_code == 200, cleanup.text
    payload = cleanup.json()
    assert payload["stripped_run_traces"] == 1
    assert payload["deleted_run_steps"] >= 1
    assert payload["deleted_runs"] == 0
    assert payload["deleted_review_items"] == 0
    assert payload["deleted_fingerprints"] == 1
    assert payload["sanitized_fingerprints"] == 0

    run_detail = client.get(f"/internal/service/agent/runs/{run_id}", headers=_svc_headers())
    assert run_detail.status_code == 200, run_detail.text
    assert run_detail.json()["trace"] == {}

    run_steps = client.get(f"/internal/service/agent/runs/{run_id}/steps", headers=_svc_headers())
    assert run_steps.status_code == 200, run_steps.text
    assert run_steps.json() == []

    queue_after = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    assert len(queue_after) == 1
    assert queue_after[0]["id"] == review_item["id"]


def test_agent_cleanup_can_compact_trace_and_review_payload_body(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_TRACE_COMPACTION_MODE", "summary")
    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Compaction run",
            "autonomy_mode": "draft_approval",
            "seed_urls": ["https://example.com/a"],
        },
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["agent_run_id"]
    queue = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    review_item = queue[0]

    session = SESSION_LOCAL()
    try:
        old_ts = datetime.now(UTC) - timedelta(days=8)
        session.query(AgentRunOrm).filter(AgentRunOrm.id == run_id).update(
            {"created_at": old_ts, "updated_at": old_ts, "started_at": old_ts},
            synchronize_session=False,
        )
        session.query(ReviewQueueItemOrm).filter(ReviewQueueItemOrm.id == review_item["id"]).update(
            {"created_at": old_ts},
            synchronize_session=False,
        )
        session.commit()
    finally:
        session.close()

    cleanup = client.post(
        "/internal/service/agent/cleanup",
        headers=_svc_headers(),
        json={
            "trace_retention_days": 7,
            "review_retention_days": 30,
            "review_body_retention_days": 7,
            "async_mode": False,
        },
    )
    assert cleanup.status_code == 200, cleanup.text
    payload = cleanup.json()
    assert payload["compacted_run_traces"] == 1
    assert payload["compacted_review_payloads"] == 1
    assert payload["deleted_runs"] == 0

    run_detail = client.get(f"/internal/service/agent/runs/{run_id}", headers=_svc_headers())
    assert run_detail.status_code == 200, run_detail.text
    trace = run_detail.json()["trace"]
    assert trace["trace_policy"] == "compacted"
    assert trace["trace"] == []
    assert trace["tool_summary"]

    queue_after = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    compacted_payload = queue_after[0]["review_payload"]
    assert compacted_payload["compacted"] is True
    assert "body_markdown" not in compacted_payload
    assert compacted_payload["review_hints"]
    assert compacted_payload["source_bundle"]["primary_sources"]


def test_agent_quality_analytics_includes_freshness_buckets(client: TestClient):
    response = client.post(
        "/internal/service/agent/runs",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "mode": "post_copilot",
            "user_request": "Freshness run",
            "seed_urls": ["https://example.com/fresh-1"],
        },
    )
    assert response.status_code == 200, response.text
    queue = client.get("/internal/service/review-queue", headers=_svc_headers()).json()
    approved = client.post(
        f"/internal/service/review-queue/{queue[0]['id']}/resolve",
        headers=_svc_headers(),
        json={"decision": "approved", "reviewer_id": "fresh"},
    )
    assert approved.status_code == 200, approved.text

    quality = client.get(
        f"/internal/service/agent/analytics/quality?channel_id={CHANNEL}",
        headers=_svc_headers(),
    )
    assert quality.status_code == 200, quality.text
    payload = quality.json()
    assert payload["freshness"]
    assert any(item["bucket"] in {"0-6h", "6-24h"} for item in payload["freshness"])


def test_rotate_channel_embeddings_endpoint_reindexes_stale_and_removes_orphans(client: TestClient):
    session = SESSION_LOCAL()
    try:
        content = ContentItemOrm(
            id=str(uuid4()),
            tenant_id=TENANT,
            author_user_id=None,
            source_type="manual",
            title="Rotate target",
            body_markdown="Rotate body",
            status="draft",
        )
        session.add(content)
        session.flush()
        session.add(
            ContentEmbeddingOrm(
                id=str(uuid4()),
                tenant_id=TENANT,
                channel_id=CHANNEL,
                entity_type="content_item",
                entity_id=content.id,
                model_name="old-embedding-model",
                vector_json="[0.1, 0.2, 0.3]",
                text_hash="stale-hash",
            )
        )
        session.add(
            ContentEmbeddingOrm(
                id=str(uuid4()),
                tenant_id=TENANT,
                channel_id=CHANNEL,
                entity_type="candidate",
                entity_id="missing-candidate",
                model_name="old-embedding-model",
                vector_json="[0.1, 0.2, 0.3]",
                text_hash="stale-hash",
            )
        )
        session.commit()
        content_id = content.id
    finally:
        session.close()
    response = client.post(
        f"/internal/service/agent/reindex/channel/{CHANNEL}/rotate",
        headers=_svc_headers(),
        json={"limit": 10, "async_mode": False},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["rotated"] >= 1
    assert payload["deleted_orphan_embeddings"] >= 1
    assert payload["embedding_model"] == "gpt-test"
    assert payload["compaction_policy"] == "keep_latest_per_entity"
    assert payload["vector_backend"] == "pgvector"
    assert isinstance(payload["pgvector_native"], bool)
    assert payload["stored_embeddings"] >= 0
    assert payload["content_items_total"] >= 1
    assert payload["missing_embeddings_before"] >= 0
    assert payload["stale_embeddings_before"] >= 0
    assert payload["coverage_after"] >= 0.0

    session = SESSION_LOCAL()
    try:
        embedding = session.query(ContentEmbeddingOrm).filter(
            ContentEmbeddingOrm.entity_type == "content_item",
            ContentEmbeddingOrm.entity_id == content_id,
        ).one()
        assert embedding.model_name == "gpt-test"
        assert embedding.text_hash != "stale-hash"
    finally:
        session.close()


def test_embeddings_lifecycle_endpoint_reports_model_and_text_drift(client: TestClient):
    session = SESSION_LOCAL()
    try:
        provider = session.query(LlmProviderConfigOrm).filter(LlmProviderConfigOrm.tenant_id == TENANT).one()
        provider.capabilities_json = '{"embedding_model":"embed-v2"}'
        content = ContentItemOrm(
            id=str(uuid4()),
            tenant_id=TENANT,
            author_user_id=None,
            source_type="manual",
            title="Lifecycle target",
            body_markdown="Fresh body",
            status="draft",
        )
        session.add(content)
        session.flush()
        session.add(
            ContentEmbeddingOrm(
                id=str(uuid4()),
                tenant_id=TENANT,
                channel_id=CHANNEL,
                entity_type="content_item",
                entity_id=content.id,
                model_name="old-embed-v1",
                vector_json="[0.1,0.2,0.3]",
                text_hash="stale-text-hash",
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.get(
        f"/internal/service/agent/embeddings/lifecycle?channel_id={CHANNEL}",
        headers=_svc_headers(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["target_embedding_model"] == "embed-v2"
    assert payload["vector_backend"] == "pgvector"
    assert isinstance(payload["pgvector_native"], bool)
    assert payload["stored_embeddings"] >= 1
    assert payload["stale_embeddings"] >= 1
    assert payload["stale_model_embeddings"] >= 1
    assert payload["stale_text_embeddings"] >= 1
    assert payload["channels"][0]["target_embedding_model"] == "embed-v2"
    assert payload["channels"][0]["stored_embeddings"] >= 1
    assert payload["channels"][0]["stale_model_embeddings"] >= 1
    assert payload["channels"][0]["stale_text_embeddings"] >= 1


def test_reindex_embedding_drift_endpoint_reindexes_channel_stale_embeddings(client: TestClient):
    session = SESSION_LOCAL()
    try:
        provider = session.query(LlmProviderConfigOrm).filter(LlmProviderConfigOrm.tenant_id == TENANT).one()
        provider.capabilities_json = '{"embedding_model":"embed-v3"}'
        content_ids: list[str] = []
        for idx in range(2):
            content = ContentItemOrm(
                id=str(uuid4()),
                tenant_id=TENANT,
                author_user_id=None,
                source_type="manual",
                title=f"Drift target {idx}",
                body_markdown=f"Body for drift reindex {idx}",
                status="draft",
            )
            session.add(content)
            session.flush()
            content_ids.append(content.id)
            session.add(
                ContentEmbeddingOrm(
                    id=str(uuid4()),
                    tenant_id=TENANT,
                    channel_id=CHANNEL,
                    entity_type="content_item",
                    entity_id=content.id,
                    model_name="embed-v1",
                    vector_json="[0.1,0.2,0.3]",
                    text_hash="old-hash",
                )
            )
        session.commit()
    finally:
        session.close()

    response = client.post(
        "/internal/service/agent/reindex/drift",
        headers=_svc_headers(),
        json={"async_mode": False, "channel_id": CHANNEL, "channel_limit": 10, "item_limit": 10},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["target_embedding_model"] == "embed-v3"
    assert payload["channels_scanned"] == 1
    assert payload["channels_reindexed"] == 1
    assert payload["rotated_embeddings"] >= 2
    assert payload["after"]["stale_model_embeddings"] == 0

    session = SESSION_LOCAL()
    try:
        rows = session.query(ContentEmbeddingOrm).filter(
            ContentEmbeddingOrm.entity_type == "content_item",
            ContentEmbeddingOrm.entity_id.in_(content_ids),
        ).all()
        assert len(rows) == 2
        assert {row.channel_id for row in rows} == {CHANNEL}
        assert all(row.model_name == "embed-v3" for row in rows)
    finally:
        session.close()


def test_embedding_maintenance_endpoint_prunes_orphans_and_malformed_rows(client: TestClient):
    session = SESSION_LOCAL()
    try:
        content = ContentItemOrm(
            id=str(uuid4()),
            tenant_id=TENANT,
            author_user_id=None,
            source_type="manual",
            title="Maintenance target",
            body_markdown="Valid embedding content",
            status="draft",
        )
        session.add(content)
        session.flush()
        session.add(
            ContentEmbeddingOrm(
                id=str(uuid4()),
                tenant_id=TENANT,
                channel_id=CHANNEL,
                entity_type="content_item",
                entity_id=content.id,
                model_name="gpt-test",
                vector_json="[0.1, 0.2, 0.3]",
                text_hash="valid-hash",
            )
        )
        session.add(
            ContentEmbeddingOrm(
                id=str(uuid4()),
                tenant_id=TENANT,
                channel_id=CHANNEL,
                entity_type="candidate",
                entity_id="missing-candidate",
                model_name="gpt-test",
                vector_json="[0.1, 0.2, 0.3]",
                text_hash="orphan-hash",
            )
        )
        session.add(
            ContentEmbeddingOrm(
                id=str(uuid4()),
                tenant_id=TENANT,
                channel_id=CHANNEL,
                entity_type="content_item",
                entity_id=str(uuid4()),
                model_name="gpt-test",
                vector_json="not-json",
                text_hash="bad-hash",
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.post(
        "/internal/service/agent/embeddings/maintenance",
        headers=_svc_headers(),
        json={"channel_id": CHANNEL, "async_mode": False, "prune_orphans": True, "prune_malformed": True},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["deleted_orphan_embeddings"] >= 1
    assert payload["deleted_malformed_embeddings"] >= 1
    assert payload["backend"] == "pgvector"
    assert "gpt-test" in payload["model_counts"]

    session = SESSION_LOCAL()
    try:
        rows = session.query(ContentEmbeddingOrm).all()
        assert len(rows) == 1
        assert rows[0].text_hash == "valid-hash"
    finally:
        session.close()


def test_embedding_maintenance_supports_chunked_windows(client: TestClient):
    session = SESSION_LOCAL()
    try:
        for idx in range(3):
            session.add(
                ContentEmbeddingOrm(
                    id=str(uuid4()),
                    tenant_id=TENANT,
                    channel_id=CHANNEL,
                    entity_type="candidate",
                    entity_id=f"missing-candidate-{idx}",
                    model_name="gpt-test",
                    vector_json="[0.1, 0.2, 0.3]",
                    text_hash=f"orphan-{idx}",
                )
            )
        session.commit()
    finally:
        session.close()

    first = client.post(
        "/internal/service/agent/embeddings/maintenance",
        headers=_svc_headers(),
        json={"channel_id": CHANNEL, "async_mode": False, "row_limit": 2, "offset": 0},
    )
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert first_payload["processed_rows"] == 2
    assert first_payload["has_more"] is True
    assert first_payload["next_after_id"]

    second = client.post(
        "/internal/service/agent/embeddings/maintenance",
        headers=_svc_headers(),
        json={
            "channel_id": CHANNEL,
            "async_mode": False,
            "row_limit": 2,
            "after_id": first_payload["next_after_id"],
        },
    )
    assert second.status_code == 200, second.text
    second_payload = second.json()
    assert second_payload["after_id"] == first_payload["next_after_id"]
    assert second_payload["processed_rows"] >= 1


def test_embedding_compaction_prunes_old_resolved_candidate_embeddings(client: TestClient):
    session = SESSION_LOCAL()
    try:
        _seed_agent_run(session, run_id="run-old", graph_name="topic_scout")
        _seed_agent_run(session, run_id="run-fresh", graph_name="topic_scout")
        old_candidate = ContentCandidateOrm(
            id=str(uuid4()),
            agent_run_id="run-old",
            tenant_id=TENANT,
            channel_id=CHANNEL,
            status="converted",
            headline="Old resolved candidate",
            draft_json="{}",
        )
        fresh_candidate = ContentCandidateOrm(
            id=str(uuid4()),
            agent_run_id="run-fresh",
            tenant_id=TENANT,
            channel_id=CHANNEL,
            status="proposed",
            headline="Fresh pending candidate",
            draft_json="{}",
        )
        session.add_all([old_candidate, fresh_candidate])
        session.flush()
        session.add(
            ContentEmbeddingOrm(
                id=str(uuid4()),
                tenant_id=TENANT,
                channel_id=CHANNEL,
                entity_type="candidate",
                entity_id=old_candidate.id,
                model_name="gpt-test",
                vector_json="[0.1, 0.2, 0.3]",
                text_hash="old-resolved",
            )
        )
        session.add(
            ContentEmbeddingOrm(
                id=str(uuid4()),
                tenant_id=TENANT,
                channel_id=CHANNEL,
                entity_type="candidate",
                entity_id=fresh_candidate.id,
                model_name="gpt-test",
                vector_json="[0.4, 0.5, 0.6]",
                text_hash="fresh-proposed",
            )
        )
        old_ts = datetime.now(UTC) - timedelta(days=40)
        session.query(ContentCandidateOrm).filter(ContentCandidateOrm.id == old_candidate.id).update(
            {"created_at": old_ts, "updated_at": old_ts},
            synchronize_session=False,
        )
        session.query(ContentEmbeddingOrm).filter(ContentEmbeddingOrm.entity_id == old_candidate.id).update(
            {"created_at": old_ts, "updated_at": old_ts},
            synchronize_session=False,
        )
        session.commit()
    finally:
        session.close()

    response = client.post(
        "/internal/service/agent/embeddings/compact",
        headers=_svc_headers(),
        json={"channel_id": CHANNEL, "async_mode": False, "candidate_retention_days": 30},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["compactable_candidate_count"] == 1
    assert payload["deleted_candidate_embeddings"] == 1

    session = SESSION_LOCAL()
    try:
        rows = session.query(ContentEmbeddingOrm).filter(ContentEmbeddingOrm.entity_type == "candidate").all()
        assert len(rows) == 1
        assert rows[0].text_hash == "fresh-proposed"
    finally:
        session.close()


def test_pgvector_store_roundtrip(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_VECTOR_BACKEND", "pgvector")
    session = SESSION_LOCAL()
    try:
        store = get_vector_store(session)
        store.clear()
        store.upsert_embedding(
            tenant_id=TENANT,
            channel_id=CHANNEL,
            entity_type="content_item",
            entity_id="c1",
            model_name="emb-test",
            vector=[1.0, 0.0, 0.0],
            text_hash="h1",
        )
        store.upsert_embedding(
            tenant_id=TENANT,
            channel_id=CHANNEL,
            entity_type="content_item",
            entity_id="c2",
            model_name="emb-test",
            vector=[0.5, 0.5, 0.0],
            text_hash="h2",
        )
        results = store.find_similar(
            tenant_id=TENANT,
            channel_id=CHANNEL,
            entity_type="content_item",
            vector=[1.0, 0.0, 0.0],
            top_k=2,
        )
        assert results[0]["entity_id"] == "c1"
        assert results[0]["score"] >= results[1]["score"]
    finally:
        store.clear()
        session.close()
        monkeypatch.delenv("AGENT_VECTOR_BACKEND", raising=False)
