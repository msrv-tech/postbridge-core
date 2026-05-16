"""Unified migration path: publication_target + reconcile."""

from unittest.mock import MagicMock

import pytest

from postbridge.db import Base, ENGINE, BatchImportRunOrm, SESSION_LOCAL, init_db  # noqa: E402
from postbridge.domain.models import BatchImportRunStatus, PostPayload  # noqa: E402
from postbridge.models.domain import PublicationTargetOrm  # noqa: E402
from postbridge.observability.metrics import reset_for_tests  # noqa: E402
from postbridge.storage.batch_import_run_store import BatchImportRunStore  # noqa: E402
from postbridge.sync.batch_import_run_reconcile import reconcile_batch_import_runs  # noqa: E402
from postbridge.sync.service import SyncService  # noqa: E402
from tests.migration_helpers import (  # noqa: E402
    seed_max_destination_channel,
    seed_telegram_source_channel,
)

TENANT = "20000000-0000-4000-8000-000000000099"
CORE_MAX_CH = "20000000-0000-4000-8000-0000000000c1"
CORE_TG_SRC = "20000000-0000-4000-8000-0000000000c2"


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
    ):
        return self._posts[:limit]


@pytest.fixture(autouse=True)
def reset_db():
    reset_for_tests()
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    yield


def test_unified_path_enqueues_targets_and_reconcile_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    session = SESSION_LOCAL()
    try:
        ch_id = seed_max_destination_channel(session, TENANT, channel_id=CORE_MAX_CH)
        seed_telegram_source_channel(session, TENANT, channel_id=CORE_TG_SRC)
        session.commit()
        store = BatchImportRunStore(session)
        job, _ = store.create_run(
            tenant_id=TENANT,
            source_channel="tg/src",
            target_channel="max/tgt",
            requested_limit=5,
            correlation_id="corr-u1",
            source_core_channel_id=CORE_TG_SRC,
            target_core_channel_id=ch_id,
            source_platform="telegram",
            target_platform="max",
        )
        session.commit()

        sent_tasks: list[tuple[str, str]] = []

        def fake_send_task(name: str, args=None, **kwargs):
            sent_tasks.append((name, args[0] if args else ""))
            return MagicMock()

        monkeypatch.setattr(
            "postbridge.workers.celery_app.celery_app.send_task",
            fake_send_task,
        )

        posts = [PostPayload(source_post_id="m1", text="hello unified")]
        service = SyncService(session=session, fetcher=FakeTelegramImporter(posts))
        out = service.run_job(job.id, correlation_id="corr-u1")
        assert out == 0

        session.expire_all()
        run_orm = session.get(BatchImportRunOrm, job.id)
        assert run_orm is not None
        assert run_orm.status == BatchImportRunStatus.RUNNING.value
        assert run_orm.batch_import_dispatch_enqueued_at is not None
        assert len(sent_tasks) == 1
        assert sent_tasks[0][0] == "postbridge.publication.process_target"

        tids = store.list_enqueued_target_ids(job.id)
        assert len(tids) == 1
        tid = tids[0]
        tgt = session.get(PublicationTargetOrm, tid)
        assert tgt is not None
        tgt.status = "published"
        session.commit()

        n = reconcile_batch_import_runs(session)
        assert n == 1
        run_orm = session.get(BatchImportRunOrm, job.id)
        assert run_orm.status == BatchImportRunStatus.COMPLETED.value
        assert run_orm.processed_posts == 1
    finally:
        session.close()


def test_unified_empty_posts_marks_completed_immediately() -> None:
    session = SESSION_LOCAL()
    try:
        ch_id = seed_max_destination_channel(session, TENANT, channel_id=CORE_MAX_CH)
        seed_telegram_source_channel(session, TENANT, channel_id=CORE_TG_SRC)
        session.commit()
        store = BatchImportRunStore(session)
        job, _ = store.create_run(
            tenant_id=TENANT,
            source_channel="tg/src",
            target_channel="max/tgt",
            requested_limit=5,
            correlation_id="corr-u2",
            source_core_channel_id=CORE_TG_SRC,
            target_core_channel_id=ch_id,
        )
        session.commit()

        service = SyncService(session=session, fetcher=FakeTelegramImporter([]))
        service.run_job(job.id, correlation_id="corr-u2")

        run_orm = session.get(BatchImportRunOrm, job.id)
        assert run_orm.status == BatchImportRunStatus.COMPLETED.value
        assert run_orm.processed_posts == 0
    finally:
        session.close()
