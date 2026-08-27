"""Отложенная публикация Postbridge в Core: сервис и постановка live-sync в очередь."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from postbridge.db import Base, ENGINE, SESSION_LOCAL, init_db
from postbridge.models.domain import AgentRunOrm, BridgeOrm, ChannelOrm, ContentItemOrm, TenantOrm
from postbridge.services import postbridge_scheduled_publish as sched_mod
from postbridge.services.postbridge_scheduled_publish import (
    process_due_scheduled_postbridge_publishes,
    try_publish_scheduled_postbridge_item,
)
from postbridge.services.live_sync_publish_service import ingest_live_sync_publication
from postbridge.services.publication_target_executor import PublicationTargetExecutor
from postbridge.services.postbridge_workspace_content import _load_extra
from postbridge.workers.tasks import process_scheduled_postbridge_publishes_task


@pytest.fixture(autouse=True)
def reset_db() -> None:
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    yield


def _past_schedule_iso(now: datetime) -> str:
    t = (now - timedelta(hours=2)).replace(microsecond=0, second=0)
    m = t.minute % 5
    if m:
        t = t.replace(minute=t.minute - m)
    return t.astimezone(UTC).isoformat()


def _seed_scheduled_draft(
    *,
    with_source_channel: bool = True,
    with_bridge: bool = True,
    bridge_settings: dict | None = None,
) -> tuple[str, str, str | None, str | None]:
    session = SESSION_LOCAL()
    tenant_id = str(uuid4())
    session.add(TenantOrm(id=tenant_id, name="t"))
    session.flush()
    src_id = str(uuid4())
    tgt_id = str(uuid4())
    post_id = str(uuid4())
    session.add(
        ChannelOrm(
            id=src_id,
            tenant_id=tenant_id,
            platform="postbridge",
            kind="source",
            title="PB",
            external_id="pb/ws-test",
            status="connected",
        )
    )
    session.add(
        ChannelOrm(
            id=tgt_id,
            tenant_id=tenant_id,
            platform="max",
            kind="destination",
            title="MAX",
            external_id="max-ext-1",
            status="connected",
        )
    )
    session.flush()
    if with_bridge:
        session.add(
            BridgeOrm(
                id=str(uuid4()),
                tenant_id=tenant_id,
                saas_user_id="u1",
                source_channel_id=src_id,
                target_channel_id=tgt_id,
                status="active",
                mode="live_sync",
                settings_json=bridge_settings,
            )
        )
    now = datetime.now(UTC)
    extra: dict = {
        "scheduled_publish_at": _past_schedule_iso(now),
        "saas_workspace_id": "ws-saas",
        "content_plain": "plain body",
        "summary": "S",
        "cta": "Go",
        "link_url": "https://x.test",
    }
    if with_source_channel:
        extra["live_sync_source_core_channel_id"] = src_id
    body = json.dumps({"postbridge_extra": extra}, ensure_ascii=True)
    session.add(
        ContentItemOrm(
            id=post_id,
            tenant_id=tenant_id,
            source_type="postbridge",
            title="T",
            body_markdown="# md",
            body_structured_json=body,
            status="draft",
        )
    )
    session.commit()
    session.close()
    return tenant_id, post_id, src_id if with_source_channel else None, tgt_id


def test_try_publish_makes_published_and_builds_jobs() -> None:
    _tenant_id, post_id, _src, _tgt = _seed_scheduled_draft()
    session = SESSION_LOCAL()
    try:
        now = datetime.now(UTC)
        ok, jobs = try_publish_scheduled_postbridge_item(
            session, content_id=post_id, now_utc=now
        )
        assert ok is True
        assert len(jobs) == 1
        assert jobs[0].source_channel == "pb/ws-test"
        assert jobs[0].target_channel == "max-ext-1"
        assert jobs[0].workspace_id == "ws-saas"
        assert jobs[0].post["text"]
        assert jobs[0].post["source_post_id"] == post_id
        session.commit()
    finally:
        session.close()

    session = SESSION_LOCAL()
    try:
        row = session.get(ContentItemOrm, post_id)
        assert row is not None
        assert row.status == "published"
        ex = _load_extra(row.body_structured_json)
        assert "scheduled_publish_at" not in ex
        assert ex.get("published_at")
    finally:
        session.close()


def test_process_due_publishes_and_enqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_scheduled_draft()
    queued: list[dict] = []
    monkeypatch.setattr(
        sched_mod,
        "queue_live_sync_publish",
        lambda **kw: queued.append(dict(kw)),
    )
    session = SESSION_LOCAL()
    try:
        n = process_due_scheduled_postbridge_publishes(session, batch_size=10)
        assert n == 1
    finally:
        session.close()
    assert len(queued) == 1


def test_process_due_commits_bridge_adaptation_agent_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, _post_id, _src, tgt_id = _seed_scheduled_draft(
        bridge_settings={"adaptation_mode": "ai_auto"},
    )
    monkeypatch.setattr(
        "postbridge.services.bridge_adaptation._default_generator",
        lambda **_kw: ("Adapted MAX text", {"total_tokens": 9}),
    )
    monkeypatch.setattr(sched_mod, "queue_live_sync_publish", lambda **_kw: None)

    session = SESSION_LOCAL()
    try:
        assert process_due_scheduled_postbridge_publishes(session, batch_size=10) == 1
    finally:
        session.close()

    session = SESSION_LOCAL()
    try:
        rows = list(
            session.scalars(
                select(AgentRunOrm).where(
                    AgentRunOrm.tenant_id == tenant_id,
                    AgentRunOrm.channel_id == tgt_id,
                    AgentRunOrm.graph_name == "bridge_adapt",
                )
            ).all()
        )
        assert len(rows) == 1
        assert rows[0].status == "completed"
        assert rows[0].token_usage_json == '{"total_tokens": 9}'
    finally:
        session.close()


def test_process_due_uses_ai_settings_from_json_string_and_preserves_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, _post_id, _src, tgt_id = _seed_scheduled_draft(
        bridge_settings=json.dumps({"adaptation_mode": "ai_auto"}, ensure_ascii=True),
    )
    monkeypatch.setattr(
        "postbridge.services.bridge_adaptation._default_generator",
        lambda **_kw: ("Adapted MAX text", {"total_tokens": 9}),
    )
    queued: list[dict] = []
    monkeypatch.setattr(
        sched_mod,
        "queue_live_sync_publish",
        lambda **kw: queued.append(dict(kw)),
    )

    session = SESSION_LOCAL()
    try:
        assert process_due_scheduled_postbridge_publishes(session, batch_size=10) == 1
    finally:
        session.close()

    assert len(queued) == 1
    assert queued[0]["post"]["text"] == "Adapted MAX text\n\nhttps://x.test"
    assert queued[0]["post"]["link_url"] == "https://x.test"

    session = SESSION_LOCAL()
    try:
        rows = list(
            session.scalars(
                select(AgentRunOrm).where(
                    AgentRunOrm.tenant_id == tenant_id,
                    AgentRunOrm.channel_id == tgt_id,
                    AgentRunOrm.graph_name == "bridge_adapt",
                )
            ).all()
        )
        assert len(rows) == 1
        assert rows[0].status == "completed"
    finally:
        session.close()


def test_scheduled_ai_text_survives_live_sync_ingest_and_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, _post_id, _src, tgt_id = _seed_scheduled_draft(
        bridge_settings={"adaptation": {"mode": "ai_auto"}},
    )
    monkeypatch.setattr(
        "postbridge.services.bridge_adaptation._default_generator",
        lambda **_kw: ("Adapted MAX text", {"total_tokens": 9}),
    )
    queued: list[dict] = []
    monkeypatch.setattr(
        sched_mod,
        "queue_live_sync_publish",
        lambda **kw: queued.append(dict(kw)),
    )

    session = SESSION_LOCAL()
    try:
        assert process_due_scheduled_postbridge_publishes(session, batch_size=10) == 1
    finally:
        session.close()

    assert len(queued) == 1
    job = queued[0]

    session = SESSION_LOCAL()
    try:
        ing = ingest_live_sync_publication(
            session,
            tenant_id=tenant_id,
            target_core_channel_id=tgt_id,
            source_channel=job["source_channel"],
            target_channel=job["target_channel"],
            target_platform=job["target_platform"],
            post=job["post"],
            correlation_id="test-scheduled",
        )
    finally:
        session.close()

    class _CapturingPublisher:
        def __init__(self) -> None:
            self.posts = []

        def publish_post(self, target_channel, post, credentials=None):
            self.posts.append(post)
            return "msg-1"

    fake = _CapturingPublisher()
    session = SESSION_LOCAL()
    try:
        assert ing.target_id is not None
        assert PublicationTargetExecutor(session=session, publisher=fake).run(ing.target_id) == 1
    finally:
        session.close()

    assert len(fake.posts) == 1
    assert fake.posts[0].text == "Adapted MAX text\n\nhttps://x.test"


def test_process_due_idempotent_second_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_scheduled_draft()
    queued: list[dict] = []
    monkeypatch.setattr(
        sched_mod,
        "queue_live_sync_publish",
        lambda **kw: queued.append(dict(kw)),
    )
    session = SESSION_LOCAL()
    try:
        assert process_due_scheduled_postbridge_publishes(session, batch_size=10) == 1
        assert process_due_scheduled_postbridge_publishes(session, batch_size=10) == 0
    finally:
        session.close()
    assert len(queued) == 1


def test_publish_without_source_skips_fanout(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_scheduled_draft(with_source_channel=False)
    queued: list[dict] = []
    monkeypatch.setattr(
        sched_mod,
        "queue_live_sync_publish",
        lambda **kw: queued.append(dict(kw)),
    )
    session = SESSION_LOCAL()
    try:
        n = process_due_scheduled_postbridge_publishes(session, batch_size=10)
        assert n == 1
    finally:
        session.close()
    assert queued == []


def test_celery_task_invokes_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_scheduled_draft()
    queued: list[dict] = []
    monkeypatch.setattr(
        sched_mod,
        "queue_live_sync_publish",
        lambda **kw: queued.append(dict(kw)),
    )
    assert process_scheduled_postbridge_publishes_task() == 1
    assert len(queued) == 1


def test_process_due_rolls_back_when_live_sync_queue_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _tenant_id, post_id, _src, _tgt = _seed_scheduled_draft()

    def fail_queue(**kwargs):
        _ = kwargs
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(sched_mod, "queue_live_sync_publish", fail_queue)
    session = SESSION_LOCAL()
    try:
        assert process_due_scheduled_postbridge_publishes(session, batch_size=10) == 0
    finally:
        session.close()

    session = SESSION_LOCAL()
    try:
        row = session.get(ContentItemOrm, post_id)
        assert row is not None
        assert row.status == "draft"
        ex = _load_extra(row.body_structured_json)
        assert ex.get("scheduled_publish_at")
        assert "published_at" not in ex
    finally:
        session.close()
