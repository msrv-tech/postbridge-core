from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from postbridge.agent import service
from postbridge.agent.storage import create_agent_task
from postbridge.domain.errors import ExternalApiError
from postbridge.infrastructure.crypto.credentials import encrypt_credential_secret
from postbridge.models.domain import (AgentRunOrm, BridgeOrm, ChannelCredentialOrm, ChannelOrm, ContentItemOrm,
    PublicationTargetOrm, ReviewQueueItemOrm)
from postbridge.services.publication_target_executor import PublicationTargetExecutor
from postbridge.services.video_publication import fingerprint
from postbridge.workers import tasks, video_tasks
from tests.test_video_studio import state


def setup(state, monkeypatch, *, chair_x=265, render_failure=False):
    db, tenant, _, _, _ = state
    source, target, other = str(uuid4()), str(uuid4()), str(uuid4())
    db.add(ChannelOrm(id=source, tenant_id=tenant, platform="postbridge", kind="source",
                      title="Editorial", external_id="pb/ws-fixture", status="connected"))
    for channel, handle in ((target, "approved_fixture"), (other, "other_fixture")):
        db.add(ChannelOrm(id=channel, tenant_id=tenant, platform="telegram", kind="destination",
                          title=handle, external_id="@" + handle, status="connected"))
    db.flush()
    db.add(ChannelCredentialOrm(id=str(uuid4()), tenant_id=tenant, channel_id=target,
        auth_type="bot", encrypted_secret=encrypt_credential_secret('{"bot_token": "fixture"}'), status="active"))
    bridges = []
    for channel in (target, other):
        bridge = BridgeOrm(id=uuid4().hex, tenant_id=tenant, source_channel_id=source, target_channel_id=channel,
                           settings_json={}, saas_user_id="owner", status="active", mode="live_sync")
        bridges.append(bridge)
        db.add(bridge)
    db.flush()
    task = create_agent_task(db, tenant_id=tenant, channel_id=source, mode="topic_scout", goal_text="A fresh home office",
        editorial_instructions="Design a workable room", schedule_cron="0 9 * * *", timezone="Asia/Novosibirsk",
        max_candidates_per_run=1, autonomy_mode="draft_approval", provider_config_id=None,
        model_name=None, content_item_id=None, created_by="owner",
        task_config={"editorial_photo": {"bridge_id": bridges[0].id, "settings_hash": fingerprint({}),
            "confirmed": True, "language": "en"}})
    due = datetime.now(UTC) - timedelta(minutes=1)
    task.next_run_at = due
    db.commit()
    monkeypatch.setattr(tasks, "SESSION_LOCAL", video_tasks.SESSION_LOCAL)
    monkeypatch.setattr(tasks.publish_agent_run_usage_event_task, "delay", lambda *_: None)
    monkeypatch.setattr(service, "find_default_provider", lambda *_, **__: object())
    monkeypatch.setattr(service, "ensure_openai_compatible_provider", lambda _: SimpleNamespace(
        model_name="fixture", provider_type="test", embedding_model_name=None, invoke_embedding=lambda **_: ([], {})))
    def invoke(state, **_):
        assert state["search_image_mode"] == "none" and state["editorial_photo"] is True
        return {**state, "selected_candidates": [{"headline": "Room for work", "body_markdown": "Pull the chair out before checking the route.",
            "layout": {"room_width": 430, "room_depth": 380, "door_x": 155, "door_width": 94, "passage_end_y": 242,
                "furniture": [{"kind": "chair", "x": chair_x, "y": 208, "width": 55, "depth": 55},
                              {"kind": "desk", "x": chair_x, "y": 273, "width": 120, "depth": 60}]}}]}
    monkeypatch.setattr(service, "build_topic_researcher", lambda **_: SimpleNamespace(invoke=invoke))
    if render_failure:
        from postbridge.services import editorial_photo
        def fail(*_):
            raise OSError("storage unavailable")
        monkeypatch.setattr(editorial_photo, "render_layout", fail)
    return task, due, target


def test_scheduled_album_uploads_both_managed_images_and_replay_is_safe(state, monkeypatch):
    db, tenant, *_ = state
    task, due, selected = setup(state, monkeypatch)
    result = tasks.run_agent_task_task.run(task.id, tenant, due.isoformat())
    assert result["status"] == "completed"
    assert tasks.run_agent_task_task.run(task.id, tenant, due.isoformat()) == {}
    assert db.scalar(select(func.count()).select_from(AgentRunOrm)) == 1
    db.expire_all()
    target = db.scalar(select(PublicationTargetOrm))
    content = db.get(ContentItemOrm, result["auto_materialized"][0]["content_item_id"])
    assert target.channel_id == selected and content.status == "draft"
    assert len(content.media_urls) == 2 and "90 cm" in content.body_markdown

    requests = []
    def handle(request):
        requests.append(request)
        assert request.url.path.endswith("/sendMediaGroup")
        assert request.headers["Content-Type"].startswith("multipart/form-data")
        assert b"@approved_fixture" in request.content
        assert b'"media": "attach://photo0"' in request.content and b'"media": "attach://photo1"' in request.content
        assert request.content.count(b"\x89PNG\r\n\x1a\n") == 2
        assert b"90 cm" in request.content
        return httpx.Response(200, json={"ok": True, "result": [{"message_id": 101}, {"message_id": 102}]})
    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **_: client(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(tasks.process_publication_target_task, "delay", lambda target_id, corr:
        PublicationTargetExecutor(db).run(target_id, correlation_id=corr))
    assert tasks.process_due_agent_tasks_task.run() == 0
    assert tasks.process_due_agent_tasks_task.run() == 0
    db.refresh(target)
    db.refresh(content)
    assert len(requests) == 1
    assert target.status == "published" and target.external_url == "https://t.me/approved_fixture/101"
    assert content.status == "published"
    from postbridge.agent.tools import list_recent_publications
    history = list_recent_publications(db, tenant_id=tenant, channel_id=task.channel_id, include_editorial_albums=True)
    assert [row["content_item_id"] for row in history] == [content.id]


@pytest.mark.parametrize("chair_x,render_failure", [(187, False), (265, True)])
def test_unsafe_working_clearance_or_render_failure_keeps_review_draft(state, monkeypatch, chair_x, render_failure):
    db, tenant, *_ = state
    task, due, _ = setup(state, monkeypatch, chair_x=chair_x, render_failure=render_failure)
    result = tasks.run_agent_task_task.run(task.id, tenant, due.isoformat())
    assert result["status"] == "awaiting_review"
    review = db.scalar(select(ReviewQueueItemOrm))
    item = db.get(ContentItemOrm, json.loads(review.review_payload_json)["content_item_id"])
    assert item.status == "draft" and not item.media_urls
    assert db.scalar(select(PublicationTargetOrm)) is None


def test_lost_telegram_album_acknowledgement_is_held_without_a_second_send(state, monkeypatch):
    db, tenant, *_ = state
    task, due, _ = setup(state, monkeypatch)
    tasks.run_agent_task_task.run(task.id, tenant, due.isoformat())
    target = db.scalar(select(PublicationTargetOrm))
    calls = []
    def lose_receipt(request):
        calls.append(request)
        raise httpx.ReadTimeout("fixture: provider outcome unknown", request=request)
    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **_: client(transport=httpx.MockTransport(lose_receipt)))
    executor = PublicationTargetExecutor(db)
    with pytest.raises(ExternalApiError):
        executor.run(target.id)
    assert executor.run(target.id) == 0
    db.refresh(target)
    assert target.status == "unknown" and target.error_code == "PUBLICATION_DELIVERY_UNKNOWN"
    assert len(calls) == 1
