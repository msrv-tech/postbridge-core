"""Исполнение publication_target: claim, publisher, идемпотентность."""

from uuid import uuid4

import pytest

from postbridge.db import Base, ENGINE, SESSION_LOCAL, BatchImportRunOrm, init_db  # noqa: E402
from postbridge.models.domain import (  # noqa: E402
    ChannelOrm,
    ContentItemOrm,
    PublicationPlanOrm,
    PublicationTargetOrm,
    RenderVariantOrm,
    TenantOrm,
)
from postbridge.services.publication_target_executor import (  # noqa: E402
    PUBLICATION_TARGET_PENDING,
    PUBLICATION_TARGET_PUBLISHED,
    PUBLICATION_TARGET_PUBLISHING,
    PublicationTargetExecutor,
    claim_publication_target_pending,
)
from postbridge.services.publication_planning import create_content_with_plan_and_targets  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    session = SESSION_LOCAL()
    session.query(BatchImportRunOrm).delete()
    session.commit()
    session.close()
    yield


def _seed_chain_with_max_channel() -> tuple[str, str]:
    session = SESSION_LOCAL()
    tenant_id = str(uuid4())
    session.add(TenantOrm(id=tenant_id, name="t"))
    session.flush()
    ch_id = str(uuid4())
    session.add(
        ChannelOrm(
            id=ch_id,
            tenant_id=tenant_id,
            platform="max",
            kind="destination",
            title="Max ch",
            external_id="chat-42",
            status="connected",
        )
    )
    session.commit()
    session.close()

    session = SESSION_LOCAL()
    result = create_content_with_plan_and_targets(
        session,
        tenant_id=tenant_id,
        channel_ids=[ch_id],
        title="Hi",
        body_markdown="Body",
        target_status=PUBLICATION_TARGET_PENDING,
    )
    session.commit()
    tid = result.publication_target_ids[0]
    session.close()
    return tenant_id, tid


class _FakePublisher:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def publish_post(self, target_channel, post, credentials=None):
        self.calls.append((target_channel, post, credentials))
        return "msg-ext-1"


def test_executor_passes_media_from_content_columns():
    session = SESSION_LOCAL()
    tenant_id = str(uuid4())
    session.add(TenantOrm(id=tenant_id, name="t"))
    session.flush()
    ch_id = str(uuid4())
    session.add(
        ChannelOrm(
            id=ch_id,
            tenant_id=tenant_id,
            platform="max",
            kind="destination",
            title="Max ch",
            external_id="chat-99",
            status="connected",
        )
    )
    session.commit()
    result = create_content_with_plan_and_targets(
        session,
        tenant_id=tenant_id,
        channel_ids=[ch_id],
        title="Hi",
        body_markdown="Body",
        media_url="https://example.com/a.jpg",
        media_urls=["https://example.com/b.jpg"],
        target_status=PUBLICATION_TARGET_PENDING,
    )
    session.commit()
    target_id = result.publication_target_ids[0]
    session.close()

    fake = _FakePublisher()
    session = SESSION_LOCAL()
    executor = PublicationTargetExecutor(session=session, publisher=fake)
    assert executor.run(target_id) == 1
    session.close()
    assert len(fake.calls) == 1
    post = fake.calls[0][1]
    assert post.media_url == "https://example.com/a.jpg"
    assert post.media_urls == ["https://example.com/b.jpg"]


def test_executor_publishes_and_sets_published():
    _, target_id = _seed_chain_with_max_channel()
    fake = _FakePublisher()
    session = SESSION_LOCAL()
    executor = PublicationTargetExecutor(session=session, publisher=fake)
    assert executor.run(target_id) == 1
    session.close()

    verify = SESSION_LOCAL()
    t = verify.get(PublicationTargetOrm, target_id)
    assert t is not None
    assert t.status == PUBLICATION_TARGET_PUBLISHED
    assert t.external_post_id == "msg-ext-1"
    assert t.published_at is not None
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == "chat-42"
    verify.close()


def test_executor_idempotent_when_already_published():
    _, target_id = _seed_chain_with_max_channel()
    fake = _FakePublisher()
    session = SESSION_LOCAL()
    executor = PublicationTargetExecutor(session=session, publisher=fake)
    assert executor.run(target_id) == 1
    assert executor.run(target_id) == 0
    session.close()
    assert len(fake.calls) == 1


def test_second_worker_cannot_claim_same_target():
    _, target_id = _seed_chain_with_max_channel()
    fake = _FakePublisher()
    s1 = SESSION_LOCAL()
    assert claim_publication_target_pending(s1, target_id) is True
    s1.close()

    s2 = SESSION_LOCAL()
    ex = PublicationTargetExecutor(session=s2, publisher=fake)
    assert ex.run(target_id) == 0
    assert len(fake.calls) == 0
    s2.close()

    s3 = SESSION_LOCAL()
    t = s3.get(PublicationTargetOrm, target_id)
    assert t is not None
    assert t.status == PUBLICATION_TARGET_PUBLISHING
    s3.close()


def test_dispatch_endpoint_enqueues(monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        "postbridge.api.internal_auth.get_settings",
        lambda: SimpleNamespace(sync_publish_token="secret"),
    )

    _, target_id = _seed_chain_with_max_channel()
    delayed: list = []

    def fake_delay(*_a, **_k):
        delayed.append(True)

    monkeypatch.setattr(
        "postbridge.api.publication_internal.process_publication_target_task.delay",
        fake_delay,
    )

    from postbridge.api.main import app  # noqa: E402

    client = TestClient(app)
    r = client.post(
        f"/internal/publication-targets/{target_id}/dispatch",
        headers={"X-Sync-Publish-Token": "secret"},
    )
    assert r.status_code == 200
    assert r.json() == {"status": "enqueued", "target_id": target_id}
    assert delayed == [True]
