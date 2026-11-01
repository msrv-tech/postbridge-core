"""Editorial acceptance through the real SDK, with a local fake gateway."""
import json
from uuid import uuid4

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests.test_agent_api import CHANNEL, TENANT, reset_db
from tests.test_agent_sdk_runtime import reply
from postbridge.agent.editorial import build_post_editor, build_topic_researcher
from postbridge.agent.providers.openai_compatible import OpenAICompatibleProvider
from postbridge.agent.service import EditorialExecutionService
from postbridge.agent.storage import create_agent_task
from postbridge.db import SESSION_LOCAL
from postbridge.domain.errors import ExternalApiError, ValidationError
from postbridge.models.domain import AgentRunOrm, AgentRunStepOrm, ChannelOrm, ContentCandidateOrm, ContentItemOrm, TenantOrm


@pytest.fixture(autouse=True)
def isolated_gateway(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_USAGE_FORMAT", "gitsell")
    monkeypatch.setattr("postbridge.agent.operations.sources.collect_topic_sources", lambda *a, **k: [])
    monkeypatch.setattr("postbridge.agent.operations.sources.collect_seed_sources", lambda *a, **k: [])
    monkeypatch.setattr("postbridge.agent.operations.topics.collect_topic_evidence", lambda *a, **k: [])
    monkeypatch.setattr("postbridge.agent.operations.sources._probe_image_dimensions", lambda *a, **k: (640, 480))
    def forbidden(*args, **kwargs):
        pytest.fail("Editorial inference must use the shared SDK, not legacy provider generation")
    monkeypatch.setattr(OpenAICompatibleProvider, "invoke_json", forbidden)
    monkeypatch.setattr(OpenAICompatibleProvider, "invoke_rerank", forbidden)
    monkeypatch.setattr(OpenAICompatibleProvider, "invoke_embedding", lambda *a, **k: ([0.1, 0.2, 0.3, 0.4], {}))


def provider():
    return OpenAICompatibleProvider(base_url="https://gateway.example/api/v1", model_name="test-model", api_key="test-only")


def state(**changes):
    return {"tenant_id": TENANT, "channel_id": CHANNEL, "user_request": "Prepare a sourced post",
        "tool_trace": [], **changes}


def candidate(title="Verified draft", **changes):
    return {"headline": title, "body_markdown": "First paragraph.\n\nSecond paragraph.",
        "topic": "A topic", "summary": "Summary", "scores": {"relevance": 0.9}, **changes}


def test_post_is_submitted_through_sdk_without_an_extra_model_turn():
    requests, receipts = [], []
    def handler(request):
        requests.append(json.loads(request.content))
        return reply(None, tool=("submit_post", {"candidate": candidate()}))
    with SESSION_LOCAL() as db:
        result = build_post_editor(session=db, provider=provider(), transport=httpx2.MockTransport(handler)).invoke(
            state(), on_provider_receipt=receipts.append)
    assert len(requests) == 1
    assert result["selected_candidates"][0]["headline"] == "Verified draft"
    assert receipts[0].charged_units == 7
    assert result["sdk_tool_events"] == [{"tool_name": "submit_post", "status": "succeeded", "error_code": None}]


def test_bad_submission_is_corrected_without_stopping_after_rejected_tool():
    requests, receipts = [], []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return reply(None, tool=("submit_post", {"candidate": candidate(body_markdown="")}))
        assert "INVALID_TOOL_ARGUMENTS" in body["messages"][-1]["content"]
        return reply(None, tool=("submit_post", {"candidate": candidate()}))
    with SESSION_LOCAL() as db:
        result = build_post_editor(session=db, provider=provider(), transport=httpx2.MockTransport(handler)).invoke(
            state(), on_provider_receipt=receipts.append)
    assert len(receipts) == 2
    assert sum(item.charged_units for item in receipts) == 14
    assert result["selected_candidates"][0]["body_markdown"].startswith("First paragraph")


def test_model_cannot_invent_sources_or_attach_an_unknown_image():
    fabricated = {"primary_sources": ["https://invented.example/fact"],
        "seed_sources": [{"url": "https://invented.example/fact", "text_excerpt": "Fabrication"}]}
    def handler(_):
        return reply(None, tool=("submit_post", {"candidate": candidate(source_bundle=fabricated,
            media_url="https://invented.example/image.jpg")}))
    with SESSION_LOCAL() as db:
        result = build_post_editor(session=db, provider=provider(), transport=httpx2.MockTransport(handler)).invoke(state())
    saved = result["selected_candidates"][0]
    assert saved["source_bundle"]["primary_sources"] == []
    assert saved["source_bundle"]["seed_sources"] == []
    assert "media_url" not in saved


def test_channel_connector_secrets_are_not_sent_to_the_model():
    seen = []
    def handler(request):
        seen.append(request.content.decode())
        return reply(None, tool=("submit_post", {"candidate": candidate()}))
    with SESSION_LOCAL() as db:
        db.get(ChannelOrm, CHANNEL).config_json = '{"access_token":"connector-private-value"}'
        db.commit()
        build_post_editor(session=db, provider=provider(), transport=httpx2.MockTransport(handler)).invoke(state())
    assert "connector-private-value" not in seen[0]


def test_discovered_sources_are_reviewed_before_any_paid_inference(monkeypatch):
    monkeypatch.setattr("postbridge.agent.operations.sources.collect_topic_sources", lambda *a, **k: [
        {"url": "https://verified.example/story", "title": "Verified", "text_excerpt": "Verified facts"}])
    def forbidden(_):
        pytest.fail("Source approval must precede model dispatch")
    with SESSION_LOCAL() as db:
        result = build_post_editor(session=db, provider=provider(), transport=httpx2.MockTransport(forbidden)).invoke(
            state(require_source_approval=True))
    assert result["selected_candidates"] == []
    assert result["source_package"]["primary_sources"] == ["https://verified.example/story"]


def test_foreign_selected_draft_is_rejected_before_inference():
    with SESSION_LOCAL() as db:
        foreign = str(uuid4())
        db.add(TenantOrm(id=foreign, name="Other"))
        db.flush()
        content = ContentItemOrm(id=str(uuid4()), tenant_id=foreign, source_type="postbridge",
            title="Private draft", body_markdown="Private content", status="draft")
        db.add(content)
        db.commit()
        with pytest.raises(ValidationError) as caught:
            build_post_editor(session=db, provider=provider(),
                transport=httpx2.MockTransport(lambda _: pytest.fail("must not dispatch"))).invoke(
                    state(content_item_id=content.id))
    assert caught.value.code == "VALIDATION_CONTENT_ITEM_NOT_FOUND"


def test_topics_are_submitted_then_ranked_by_the_same_sdk_execution():
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return reply(None, tool=("submit_topics", {"candidates": [candidate("First"), candidate("Second")]}))
        output = json.loads(requests[-1]["messages"][-1]["content"])
        assert output["candidates"][0]["index"] == 0
        return reply(None, tool=("rank_topics", {"ranked": [
            {"index": 1, "score": 0.99, "reason": "Freshest"}, {"index": 0, "score": 0.2}]}))
    with SESSION_LOCAL() as db:
        result = build_topic_researcher(session=db, provider=provider(), max_candidates=2,
            transport=httpx2.MockTransport(handler)).invoke(state())
    assert len(requests) == 2
    assert result["selected_candidates"][0]["headline"] == "Second"
    assert [event["tool_name"] for event in result["sdk_tool_events"]] == ["submit_topics", "rank_topics"]


@pytest.mark.parametrize("count", [0, 1])
def test_topics_without_ranking_choices_finish_in_one_paid_turn(count):
    requests = []
    def handler(request):
        requests.append(request)
        return reply(None, tool=("submit_topics", {"candidates": [candidate() for _ in range(count)]}))
    with SESSION_LOCAL() as db:
        result = build_topic_researcher(session=db, provider=provider(), max_candidates=2,
            transport=httpx2.MockTransport(handler)).invoke(state())
    assert len(requests) == 1
    assert len(result["selected_candidates"]) == count


def test_final_text_cannot_replace_the_required_topic_ranking():
    requests = []
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return reply(None, tool=("submit_topics", {"candidates": [candidate("First"), candidate("Second")]}))
        return reply("The topics are ready")
    with SESSION_LOCAL() as db, pytest.raises(ValidationError) as caught:
        build_topic_researcher(session=db, provider=provider(), max_candidates=2,
            transport=httpx2.MockTransport(handler)).invoke(state())
    assert caught.value.code == "VALIDATION_AGENT_REQUIRED_TOOLS"


def test_out_of_order_ranking_is_rejected_and_can_be_corrected():
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        if count == 1:
            return reply(None, tool=("rank_topics", {"ranked": []}))
        if count == 2:
            assert "AGENT_RANKING_CONTEXT" in request.content.decode()
            return reply(None, tool=("submit_topics", {"candidates": [candidate()]}))
        return reply(None, tool=("rank_topics", {"ranked": [{"index": 0, "score": 0.9}]}))
    with SESSION_LOCAL() as db:
        result = build_topic_researcher(session=db, provider=provider(), max_candidates=1,
            transport=httpx2.MockTransport(handler)).invoke(state())
    assert count == 2 and len(result["selected_candidates"]) == 1


@pytest.mark.parametrize("failure", ["http_500", "timeout", "malformed_json"])
def test_provider_attempts_and_receipts_survive_failed_execution(monkeypatch, failure):
    requests = []
    def handler(request):
        requests.append(request)
        with SESSION_LOCAL() as audit:
            pending = audit.scalars(select(AgentRunStepOrm).where(AgentRunStepOrm.step_name == "sdk_provider_call")).all()
            assert len(pending) == 1 and pending[0].status == "pending"
        if failure == "http_500":
            return reply(units=11, status=500)
        if failure == "malformed_json":
            return httpx2.Response(200, content=b"not-json")
        raise httpx2.ReadTimeout("lost response", request=request)
    monkeypatch.setattr("postbridge.agent.service.build_post_editor", lambda **kw: build_post_editor(
        **kw, transport=httpx2.MockTransport(handler)))
    with SESSION_LOCAL() as db:
        with pytest.raises(ExternalApiError):
            EditorialExecutionService(db).run_once(tenant_id=TENANT, channel_id=CHANNEL, mode="post_copilot",
                user_request="Write a post", topic_definition=None, content_item_id=None, max_candidates=1)
    with SESSION_LOCAL() as audit:
        run = audit.scalar(select(AgentRunOrm))
        assert run.status == "failed"
        step = audit.scalar(select(AgentRunStepOrm).where(AgentRunStepOrm.step_name == "sdk_provider_call"))
        receipt = json.loads(step.output_json)
        assert receipt["charged_units"] == (11 if failure == "http_500" else None)
        assert receipt["status"] == ("failed" if failure == "http_500" else "unknown")
        assert json.loads(run.token_usage_json)["total_tokens"] == (11 if failure == "http_500" else 0)
    assert len(requests) == 1


def test_scheduled_editorial_task_uses_the_sdk_and_reports_usage(monkeypatch):
    from postbridge.workers import tasks
    requests, notifications = [], []
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return reply(None, tool=("submit_topics", {"candidates": [candidate()]}))
        return reply(None, tool=("rank_topics", {"ranked": [{"index": 0, "score": 0.9}]}))
    monkeypatch.setattr("postbridge.agent.service.build_topic_researcher", lambda **kw: build_topic_researcher(
        **kw, transport=httpx2.MockTransport(handler)))
    monkeypatch.setattr(tasks.publish_agent_run_usage_event_task, "delay", lambda payload, correlation: notifications.append(payload))
    with SESSION_LOCAL() as db:
        task = create_agent_task(db, tenant_id=TENANT, channel_id=CHANNEL, mode="topic_scout",
            goal_text="Find a topic", editorial_instructions=None, schedule_cron="0 10 * * *", timezone="UTC",
            max_candidates_per_run=1, autonomy_mode="draft_approval", provider_config_id=None,
            model_name=None, content_item_id=None, task_config={}, created_by="owner")
        db.commit()
        task_id = task.id
    result = tasks.run_agent_task_task.run(task_id, TENANT)
    assert result["runtime"] == "openai_agents_sdk"
    assert len(requests) == 1
    assert len(notifications) == 1
    assert notifications[0]["agent_run"]["total_tokens"] == 7


@pytest.mark.parametrize("failure", ["domain", "database"])
def test_unfinished_materialization_rolls_back_without_erasing_provider_receipt(monkeypatch, failure):
    def fail_embedding(*a, **k):
        raise ValidationError(code="TEST_EMBEDDING_FAILED", message="Embedding failed")
    def fail_database(db, **kw):
        db.add(TenantOrm(id=TENANT, name="Duplicate tenant"))
        db.flush()
    if failure == "domain":
        monkeypatch.setattr(OpenAICompatibleProvider, "invoke_embedding", fail_embedding)
    else:
        monkeypatch.setattr("postbridge.agent.service.upsert_content_embedding", fail_database)
    monkeypatch.setattr("postbridge.agent.service.build_post_editor", lambda **kw: build_post_editor(**kw,
        transport=httpx2.MockTransport(lambda _: reply(None, tool=("submit_post", {"candidate": candidate()})))))
    with SESSION_LOCAL() as db:
        with pytest.raises(ValidationError if failure == "domain" else IntegrityError):
            EditorialExecutionService(db).run_once(tenant_id=TENANT, channel_id=CHANNEL, mode="post_copilot",
                user_request="Write a post", topic_definition=None, content_item_id=None, max_candidates=1)
    with SESSION_LOCAL() as audit:
        assert audit.scalar(select(ContentCandidateOrm)) is None
        run = audit.scalar(select(AgentRunOrm))
        assert run.status == "failed"
        assert json.loads(run.token_usage_json)["total_tokens"] == 7
