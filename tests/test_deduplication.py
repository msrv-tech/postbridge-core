"""Tests for global deduplication: job vs live-sync."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from postbridge.api.main import app  # noqa: E402
from postbridge.db import Base, ENGINE, SESSION_LOCAL, BatchImportRunOrm, init_db  # noqa: E402
from postbridge.domain.models import BatchImportRunStatus, PostPayload  # noqa: E402
from postbridge.models.domain import PublicationTargetOrm  # noqa: E402
from postbridge.observability.metrics import reset_for_tests  # noqa: E402
from postbridge.storage.batch_import_run_store import BatchImportRunStore  # noqa: E402
from postbridge.sync.batch_import_run_reconcile import reconcile_batch_import_runs  # noqa: E402
from postbridge.sync.service import SyncService  # noqa: E402
from postbridge.workers.tasks import process_batch_import_run_task  # noqa: E402
from tests.migration_helpers import (  # noqa: E402
    seed_max_destination_channel,
    seed_telegram_source_channel,
)

TENANT = "10000000-0000-4000-8000-000000000001"
CORE_CH = "10000000-0000-4000-8000-0000000000dd"
CORE_TG_SRC = "10000000-0000-4000-8000-0000000000ee"
LS_TENANT = "10000000-0000-4000-8000-0000000000f1"
LS_TARGET_CH = "10000000-0000-4000-8000-0000000000f2"


class FakeTelegramImporter:
    def __init__(self, posts: list[PostPayload]):
        self._posts = posts

    async def fetch_posts(
        self,
        source_channel: str,
        limit: int,
        credentials=None,
        *,
        tenant_id: str | None = None,
    ) -> list[PostPayload]:
        return self._posts[:limit]


@pytest.fixture(autouse=True)
def reset_db():
    reset_for_tests()
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    session = SESSION_LOCAL()
    session.query(BatchImportRunOrm).delete()
    seed_max_destination_channel(session, TENANT, channel_id=CORE_CH)
    seed_telegram_source_channel(session, TENANT, channel_id=CORE_TG_SRC)
    session.commit()
    session.close()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(process_batch_import_run_task, "delay", lambda *args, **kwargs: None)
    return TestClient(app)


def test_job_skips_post_already_claimed_then_enqueues_other(monkeypatch: pytest.MonkeyPatch):
    """Пост с занятым claim не получает target; второй пост уходит в очередь."""
    session = SESSION_LOCAL()
    try:
        store = BatchImportRunStore(session)
        correlation_id = str(uuid4())
        job, _ = store.create_run(
            TENANT,
            "tg/ch1",
            "max/ch1",
            requested_limit=2,
            correlation_id=correlation_id,
            source_core_channel_id=CORE_TG_SRC,
            target_core_channel_id=CORE_CH,
        )

        claimed = store.claim_publish("tg/ch1", "1", "max/ch1")
        assert claimed
        session.commit()

        monkeypatch.setattr(
            "postbridge.workers.celery_app.celery_app.send_task",
            lambda *a, **k: MagicMock(),
        )

        fake_importer = FakeTelegramImporter(
            posts=[
                PostPayload(source_post_id="1", text="first"),
                PostPayload(source_post_id="2", text="second"),
            ]
        )
        service = SyncService(session=session, fetcher=fake_importer)

        out = service.run_job(job.id, correlation_id=correlation_id)
        assert out == 0
        updated = store.get_run(job.id)
        assert updated is not None
        assert updated.status == BatchImportRunStatus.RUNNING

        tids = store.list_enqueued_target_ids(job.id)
        assert len(tids) == 1
        for tid in tids:
            tgt = session.get(PublicationTargetOrm, tid)
            assert tgt is not None
            tgt.status = "published"
        session.commit()
        reconcile_batch_import_runs(session)
        done = store.get_run(job.id)
        assert done is not None
        assert done.status == BatchImportRunStatus.COMPLETED
        assert done.processed_posts == 2
    finally:
        session.close()


def test_live_sync_skips_post_after_migration_enqueued_claim(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """После dispatch миграции claim может блокировать publish-single (dedup ledger)."""
    session = SESSION_LOCAL()
    try:
        store = BatchImportRunStore(session)
        correlation_id = str(uuid4())
        job, _ = store.create_run(
            TENANT,
            "tg/ch1",
            "max/ch1",
            requested_limit=1,
            correlation_id=correlation_id,
            source_core_channel_id=CORE_TG_SRC,
            target_core_channel_id=CORE_CH,
        )

        monkeypatch.setattr(
            "postbridge.workers.celery_app.celery_app.send_task",
            lambda *a, **k: MagicMock(),
        )
        service = SyncService(
            session=session,
            fetcher=FakeTelegramImporter(
                posts=[PostPayload(source_post_id="123", text="from job")]
            ),
        )
        service.run_job(job.id, correlation_id=correlation_id)
        session.commit()
    finally:
        session.close()

    response = client.post(
        "/internal/sync/publish-single",
        json={
            "source_channel": "tg/ch1",
            "target_channel": "max/ch1",
            "post": {"source_post_id": "123", "text": "duplicate", "media_url": None},
            "tenant_id": LS_TENANT,
            "target_core_channel_id": LS_TARGET_CH,
        },
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "source_post_id": "123"}


def test_publish_single_skips_if_already_published(client: TestClient):
    """Live-sync возвращает 200 без вызова MAX, если пост уже в published_posts."""
    session = SESSION_LOCAL()
    try:
        store = BatchImportRunStore(session)
        claimed = store.claim_publish("tg/ch99", "msg-456", "max/ch99")
        assert claimed
        session.commit()
    finally:
        session.close()

    response = client.post(
        "/internal/sync/publish-single",
        json={
            "source_channel": "tg/ch99",
            "target_channel": "max/ch99",
            "post": {"source_post_id": "msg-456", "text": "already there", "media_url": None},
            "tenant_id": LS_TENANT,
            "target_core_channel_id": LS_TARGET_CH,
        },
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "source_post_id": "msg-456"}


def test_claim_publish_returns_false_on_duplicate():
    """claim_publish возвращает False при повторной попытке (unique violation)."""
    session = SESSION_LOCAL()
    try:
        store = BatchImportRunStore(session)
        first = store.claim_publish("tg/a", "1", "max/b")
        assert first is True
        session.commit()

        session2 = SESSION_LOCAL()
        try:
            store2 = BatchImportRunStore(session2)
            second = store2.claim_publish("tg/a", "1", "max/b")
            assert second is False
        finally:
            session2.close()
    finally:
        session.close()
