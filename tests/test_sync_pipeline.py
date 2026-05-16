from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import inspect  # noqa: E402

from postbridge.db import Base, ENGINE, BatchImportFetchedPostOrm, SESSION_LOCAL, BatchImportRunOrm, init_db  # noqa: E402
from postbridge.domain.errors import ExternalApiError, ValidationError  # noqa: E402
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

T_TEST_TENANT = "10000000-0000-4000-8000-000000000001"
CORE_MAX_CH = "10000000-0000-4000-8000-000000000099"
CORE_TG_SRC = "10000000-0000-4000-8000-000000000088"


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


class FailingTelegramImporter:
    async def fetch_posts(
        self,
        source_channel: str,
        limit: int,
        credentials=None,
        *,
        tenant_id: str | None = None,
    ) -> list[PostPayload]:
        raise ExternalApiError(
            code="EXTERNAL_API_TELEGRAM_FETCH_ERROR",
            message="Telegram API request failed",
            source="telegram",
            retryable=True,
            details={"source_channel": source_channel, "limit": limit},
        )


@pytest.fixture(autouse=True)
def reset_db():
    reset_for_tests()
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    session = SESSION_LOCAL()
    session.query(BatchImportRunOrm).delete()
    seed_max_destination_channel(session, T_TEST_TENANT, channel_id=CORE_MAX_CH)
    seed_telegram_source_channel(session, T_TEST_TENANT, channel_id=CORE_TG_SRC)
    session.commit()
    session.close()


def _mock_send_task(monkeypatch: pytest.MonkeyPatch):
    def fake_send_task(name: str, args=None, **kwargs):
        return MagicMock()

    monkeypatch.setattr(
        "postbridge.workers.celery_app.celery_app.send_task",
        fake_send_task,
    )


def _complete_all_enqueued_targets(session, store: BatchImportRunStore, job_id: str) -> None:
    for tid in store.list_enqueued_target_ids(job_id):
        tgt = session.get(PublicationTargetOrm, tid)
        if tgt is not None:
            tgt.status = "published"
    session.commit()


def test_pipeline_unified_completes_after_reconcile(monkeypatch: pytest.MonkeyPatch):
    session = SESSION_LOCAL()
    try:
        store = BatchImportRunStore(session)
        correlation_id = str(uuid4())
        job, _ = store.create_run(
            T_TEST_TENANT,
            "tg/source",
            "max/target",
            requested_limit=5,
            correlation_id=correlation_id,
            source_core_channel_id=CORE_TG_SRC,
            target_core_channel_id=CORE_MAX_CH,
        )
        _mock_send_task(monkeypatch)
        service = SyncService(
            session=session,
            fetcher=FakeTelegramImporter(
                posts=[
                    PostPayload(source_post_id="1", text="first"),
                    PostPayload(source_post_id="2", text="second"),
                ]
            ),
        )
        out = service.run_job(job.id, correlation_id=correlation_id)
        assert out == 0
        updated = store.get_run(job.id)
        assert updated is not None
        assert updated.status == BatchImportRunStatus.RUNNING
        assert updated.correlation_id == correlation_id

        _complete_all_enqueued_targets(session, store, job.id)
        n = reconcile_batch_import_runs(session)
        assert n == 1
        final = store.get_run(job.id)
        assert final is not None
        assert final.status == BatchImportRunStatus.COMPLETED
        assert final.processed_posts == 2
    finally:
        session.close()


def test_pipeline_passes_channel_credentials_to_importer(monkeypatch: pytest.MonkeyPatch):
    _mock_send_task(monkeypatch)
    session = SESSION_LOCAL()
    try:
        store = BatchImportRunStore(session)
        correlation_id = str(uuid4())
        job, _ = store.create_run(
            T_TEST_TENANT,
            "tg/source",
            "max/target",
            requested_limit=1,
            correlation_id=correlation_id,
            source_core_channel_id=CORE_TG_SRC,
            target_core_channel_id=CORE_MAX_CH,
        )

        received_telegram_creds = []

        class CapturingTelegramImporter(FakeTelegramImporter):
            async def fetch_posts(
                self,
                source_channel: str,
                limit: int,
                credentials=None,
                *,
                tenant_id: str | None = None,
            ):
                received_telegram_creds.append(credentials)
                return self._posts[:limit]

        service = SyncService(
            session=session,
            fetcher=CapturingTelegramImporter(
                posts=[PostPayload(source_post_id="1", text="x")]
            ),
        )
        service.run_job(job.id, correlation_id=correlation_id)
        assert len(received_telegram_creds) == 1
        assert received_telegram_creds[0] is not None
        assert received_telegram_creds[0].api_id == "12345"
    finally:
        session.close()


def test_create_run_rejects_unknown_target_core_channel():
    session = SESSION_LOCAL()
    try:
        store = BatchImportRunStore(session)
        with pytest.raises(ValidationError) as ei:
            store.create_run(
                T_TEST_TENANT,
                "tg/source",
                "max/target",
                requested_limit=1,
                correlation_id="c1",
                source_core_channel_id=CORE_TG_SRC,
                target_core_channel_id="00000000-0000-4000-8000-000000000000",
            )
        assert ei.value.code == "VALIDATION_CHANNEL_NOT_FOUND"
    finally:
        session.close()


def test_init_db_table_exists():
    init_db()
    table_names = inspect(ENGINE).get_table_names()
    assert BatchImportRunOrm.__tablename__ in table_names
    assert BatchImportFetchedPostOrm.__tablename__ in table_names


def test_second_process_sync_skips_when_job_not_pending(monkeypatch: pytest.MonkeyPatch):
    session = SESSION_LOCAL()
    try:
        store = BatchImportRunStore(session)
        correlation_id = str(uuid4())
        job, _ = store.create_run(
            T_TEST_TENANT,
            "tg/source",
            "max/target",
            requested_limit=1,
            correlation_id=correlation_id,
            source_core_channel_id=CORE_TG_SRC,
            target_core_channel_id=CORE_MAX_CH,
        )
        _mock_send_task(monkeypatch)
        service = SyncService(
            session=session,
            fetcher=FakeTelegramImporter(
                posts=[PostPayload(source_post_id="1", text="first")]
            ),
        )
        first = service.run_job(job.id, correlation_id=correlation_id)
        second = service.run_job(job.id, correlation_id=correlation_id)
        assert first == 0
        assert second == 0
    finally:
        session.close()


def test_retry_schedule_increments_retry_count_and_requeues_pending():
    session = SESSION_LOCAL()
    try:
        store = BatchImportRunStore(session)
        correlation_id = str(uuid4())
        job, _ = store.create_run(
            T_TEST_TENANT,
            "tg/source",
            "max/target",
            requested_limit=1,
            correlation_id=correlation_id,
            source_core_channel_id=CORE_TG_SRC,
            target_core_channel_id=CORE_MAX_CH,
        )
        service = SyncService(
            session=session,
            fetcher=FailingTelegramImporter(),
        )
        with pytest.raises(ExternalApiError):
            service.run_job(job.id, correlation_id=correlation_id)
        failed_job = store.get_run(job.id)
        assert failed_job is not None
        assert failed_job.status == BatchImportRunStatus.FAILED
        assert failed_job.error_retryable is True

        scheduled = store.schedule_retry(
            job_id=job.id,
            correlation_id=correlation_id,
            max_retries=2,
        )
        retried_job = store.get_run(job.id)
        assert scheduled is True
        assert retried_job is not None
        assert retried_job.status == BatchImportRunStatus.PENDING
        assert retried_job.retry_count == 1
        assert retried_job.error_code is None
    finally:
        session.close()


def test_telegram_external_error_is_persisted_with_retryable_flag():
    session = SESSION_LOCAL()
    try:
        store = BatchImportRunStore(session)
        correlation_id = str(uuid4())
        job, _ = store.create_run(
            T_TEST_TENANT,
            "tg/source",
            "max/target",
            requested_limit=1,
            correlation_id=correlation_id,
            source_core_channel_id=CORE_TG_SRC,
            target_core_channel_id=CORE_MAX_CH,
        )
        service = SyncService(
            session=session,
            fetcher=FailingTelegramImporter(),
        )
        with pytest.raises(ExternalApiError):
            service.run_job(job.id, correlation_id=correlation_id)
        updated = store.get_run(job.id)
        assert updated is not None
        assert updated.error_code == "EXTERNAL_API_TELEGRAM_FETCH_ERROR"
        assert updated.error_source == "telegram"
        assert updated.error_retryable is True
    finally:
        session.close()


def test_batch_import_run_without_target_core_channel_fails_in_worker():
    session = SESSION_LOCAL()
    try:
        store = BatchImportRunStore(session)
        correlation_id = str(uuid4())
        job, _ = store.create_run(
            T_TEST_TENANT,
            "tg/source",
            "max/target",
            requested_limit=1,
            correlation_id=correlation_id,
            source_core_channel_id=CORE_TG_SRC,
            target_core_channel_id=CORE_MAX_CH,
        )
        run_orm = session.get(BatchImportRunOrm, job.id)
        assert run_orm is not None
        run_orm.target_core_channel_id = None
        session.commit()

        service = SyncService(
            session=session,
            fetcher=FakeTelegramImporter(
                posts=[PostPayload(source_post_id="1", text="x")]
            ),
        )
        with pytest.raises(ValidationError) as ei:
            service.run_job(job.id, correlation_id=correlation_id)
        assert ei.value.code == "VALIDATION_MIGRATION_REQUIRES_TARGET_CORE_CHANNEL"
        failed = store.get_run(job.id)
        assert failed is not None
        assert failed.status == BatchImportRunStatus.FAILED
    finally:
        session.close()


def test_run_job_skips_fetch_when_posts_already_stored(monkeypatch: pytest.MonkeyPatch):
    session = SESSION_LOCAL()
    try:
        store = BatchImportRunStore(session)
        correlation_id = str(uuid4())
        job, _ = store.create_run(
            T_TEST_TENANT,
            "tg/source",
            "max/target",
            requested_limit=2,
            correlation_id=correlation_id,
            source_core_channel_id=CORE_TG_SRC,
            target_core_channel_id=CORE_MAX_CH,
        )
        posts = [
            PostPayload(source_post_id="1", text="first"),
            PostPayload(source_post_id="2", text="second"),
        ]
        store.store_fetched_posts(job.id, posts)

        fetch_count = 0

        class CountingTelegramImporter(FakeTelegramImporter):
            async def fetch_posts(
                self,
                source_channel: str,
                limit: int,
                credentials=None,
                *,
                tenant_id: str | None = None,
            ):
                nonlocal fetch_count
                fetch_count += 1
                return await super().fetch_posts(
                    source_channel, limit, credentials, tenant_id=tenant_id
                )

        _mock_send_task(monkeypatch)
        service = SyncService(
            session=session,
            fetcher=CountingTelegramImporter(posts=[]),
        )
        service.run_job(job.id, correlation_id=correlation_id)
        assert fetch_count == 0
    finally:
        session.close()


def test_store_fetched_posts_and_list_posts_for_publish():
    session = SESSION_LOCAL()
    try:
        store = BatchImportRunStore(session)
        correlation_id = str(uuid4())
        job, _ = store.create_run(
            T_TEST_TENANT,
            "tg/source",
            "max/target",
            requested_limit=3,
            correlation_id=correlation_id,
            source_core_channel_id=CORE_TG_SRC,
            target_core_channel_id=CORE_MAX_CH,
        )

        posts = [
            PostPayload(source_post_id="1", text="first"),
            PostPayload(source_post_id="2", text="second"),
            PostPayload(source_post_id="3", text="third"),
        ]
        store.store_fetched_posts(job.id, posts)

        to_publish = store.list_posts_for_publish(job.id)
        assert len(to_publish) == 3
        assert [p.source_post_id for p in to_publish] == ["1", "2", "3"]

        store.insert_enqueued_skip(batch_import_run_id=job.id, source_post_id="2")
        to_publish = store.list_posts_for_publish(job.id)
        assert len(to_publish) == 2
        assert [p.source_post_id for p in to_publish] == ["1", "3"]
    finally:
        session.close()
