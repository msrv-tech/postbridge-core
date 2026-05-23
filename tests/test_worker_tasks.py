from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from postbridge.db import Base, ENGINE, SESSION_LOCAL, BatchImportRunOrm, init_db  # noqa: E402
from postbridge.domain.errors import ExternalApiError  # noqa: E402
from postbridge.storage.batch_import_run_store import BatchImportRunStore  # noqa: E402
from postbridge.workers.tasks import (  # noqa: E402
    cleanup_agent_runtime_task,
    compact_embeddings_task,
    dispatch_status_event_outbox_task,
    maintain_embeddings_task,
    process_batch_import_run_task,
    process_due_agent_tasks_task,
    process_scheduled_postbridge_publishes_task,
    publish_agent_run_usage_event_task,
    recover_stuck_jobs_task,
    reindex_channel_embeddings_task,
    reindex_content_item_embedding_task,
    reindex_embedding_drift_task,
    recover_stuck_publication_targets_task,
    reconcile_batch_import_runs_task,
    run_agent_task_task,
    rotate_channel_embeddings_task,
)
from postbridge.models.domain import TenantOrm  # noqa: E402
from tests.migration_helpers import (  # noqa: E402
    seed_max_destination_channel,
    seed_telegram_source_channel,
)

T_TEST = "10000000-0000-4000-8000-000000000002"
CORE_CH_OUTBOX = "20000000-0000-4000-8000-0000000000aa"
CORE_TG_OUTBOX = "20000000-0000-4000-8000-0000000000bb"


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=ENGINE)
    init_db()


def test_worker_task_runs_service_with_correlation(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, str] = {}

    def fake_run(self, job_id: str, correlation_id: str | None = None) -> int:
        captured["job_id"] = job_id
        captured["correlation_id"] = correlation_id or ""
        return 3

    monkeypatch.setattr("postbridge.sync.service.SyncService.run_job", fake_run)
    result = process_batch_import_run_task.run("job-1", "corr-1")
    assert result == 3
    assert captured == {"job_id": "job-1", "correlation_id": "corr-1"}


def test_worker_retryable_error_on_unified_run_propagates_without_celery_reschedule(
    monkeypatch: pytest.MonkeyPatch,
):
    """Unified batch import run: retryable ошибка не планирует повтор process_batch_import_run_task."""

    def fail_run(self, job_id: str, correlation_id: str | None = None) -> int:
        raise ExternalApiError(
            code="EXTERNAL_API_MAX_REQUEST_ERROR",
            message="MAX API transport error",
            source="max",
            retryable=True,
            details={"target_channel": "max/test"},
        )

    called: list[str] = []

    def fake_schedule_retry(self, job_id: str, correlation_id: str, max_retries: int) -> bool:
        called.append("schedule_retry")
        return True

    monkeypatch.setattr("postbridge.sync.service.SyncService.run_job", fail_run)
    monkeypatch.setattr(
        "postbridge.workers.tasks.BatchImportRunStore.schedule_retry", fake_schedule_retry
    )

    session = SESSION_LOCAL()
    try:
        seed_max_destination_channel(session, T_TEST, channel_id=CORE_CH_OUTBOX)
        seed_telegram_source_channel(session, T_TEST, channel_id=CORE_TG_OUTBOX)
        store = BatchImportRunStore(session)
        job, _ = store.create_run(
            T_TEST,
            "tg/source",
            "max/target",
            requested_limit=1,
            correlation_id="corr-retry",
            source_core_channel_id=CORE_TG_OUTBOX,
            target_core_channel_id=CORE_CH_OUTBOX,
        )
        job_id = job.id
    finally:
        session.close()

    with pytest.raises(ExternalApiError):
        process_batch_import_run_task.run(job_id, "corr-retry")
    assert called == []


def test_recover_stuck_jobs_task_marks_running_job_as_failed(
    monkeypatch: pytest.MonkeyPatch,
):
    @dataclass
    class DummySettings:
        batch_import_run_stuck_timeout_seconds: int = 60

    session = SESSION_LOCAL()
    try:
        job = BatchImportRunOrm(
            id="job-stuck",
            tenant_id=T_TEST,
            source_channel="tg/source",
            target_channel="max/target",
            status="running",
            requested_limit=1,
            processed_posts=0,
            retry_count=0,
            idempotency_key=None,
            correlation_id="corr-old",
            started_at=datetime.now(UTC) - timedelta(minutes=5),
            created_at=datetime.now(UTC) - timedelta(minutes=10),
            updated_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        session.add(job)
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr("postbridge.workers.tasks.get_settings", lambda: DummySettings())
    recovered = recover_stuck_jobs_task.run()
    assert recovered == 1

    verify = SESSION_LOCAL()
    try:
        updated = verify.get(BatchImportRunOrm, "job-stuck")
        assert updated is not None
        assert updated.status == "failed"
        assert updated.error_code == "INTERNAL_JOB_STUCK_TIMEOUT"
        assert updated.error_retryable is True
    finally:
        verify.close()


def test_dispatch_status_event_outbox_task_delivers_pending_event(
    monkeypatch: pytest.MonkeyPatch,
):
    class DummyResponse:
        status_code = 200

    captured: dict[str, object] = {}

    def fake_post(url: str, **kwargs: object) -> DummyResponse:
        captured["url"] = url
        captured.update(kwargs)
        return DummyResponse()

    monkeypatch.setenv("STATUS_EVENT_WEBHOOK_URL", "http://saas.test/internal/core/events/status")
    monkeypatch.setenv("STATUS_EVENT_WEBHOOK_TOKEN", "secret-token")
    monkeypatch.setattr(
        "postbridge.integrations.status_event_client.requests.post",
        fake_post,
    )

    session = SESSION_LOCAL()
    try:
        seed_max_destination_channel(session, T_TEST, channel_id=CORE_CH_OUTBOX)
        seed_telegram_source_channel(session, T_TEST, channel_id=CORE_TG_OUTBOX)
        store = BatchImportRunStore(session)
        store.create_run(
            T_TEST,
            "tg/source",
            "max/target",
            requested_limit=1,
            correlation_id="corr-outbox",
            source_core_channel_id=CORE_TG_OUTBOX,
            target_core_channel_id=CORE_CH_OUTBOX,
        )
    finally:
        session.close()

    processed = dispatch_status_event_outbox_task.run()
    assert processed >= 1
    assert captured.get("url") == "http://saas.test/internal/core/events/status"
    headers = captured.get("headers")
    assert isinstance(headers, dict)
    assert headers.get("X-Core-Event-Token") == "secret-token"
    body = captured.get("json")
    assert isinstance(body, dict)
    assert body.get("contract_version") == "1.5"
    assert body.get("event_type") == "batch_import_run.status.changed"
    assert isinstance(body.get("batch_import_run"), dict)


def test_agent_embedding_task_wrappers_commit_results(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, str, str]] = []

    def fake_reindex_channel(session, *, tenant_id, channel_id, limit, offset):
        calls.append(("channel", tenant_id, channel_id))
        return {"channels_reindexed": 1, "limit": limit, "offset": offset}

    def fake_reindex_item(session, *, tenant_id, channel_id, content_item_id):
        calls.append(("item", tenant_id, content_item_id))
        return {"content_item_id": content_item_id}

    def fake_rotate(session, *, tenant_id, channel_id, limit, offset):
        calls.append(("rotate", tenant_id, channel_id))
        return {"rotated_embeddings": 2, "limit": limit, "offset": offset}

    monkeypatch.setattr("postbridge.workers.tasks.reindex_channel_content_embeddings", fake_reindex_channel)
    monkeypatch.setattr("postbridge.workers.tasks.reindex_content_item_embedding", fake_reindex_item)
    monkeypatch.setattr("postbridge.workers.tasks.rotate_channel_content_embeddings", fake_rotate)

    assert reindex_channel_embeddings_task.run("tenant-1", "channel-1", limit=5, offset=2) == {
        "channels_reindexed": 1,
        "limit": 5,
        "offset": 2,
    }
    assert reindex_content_item_embedding_task.run("tenant-1", "channel-1", "content-1") == {
        "content_item_id": "content-1"
    }
    assert rotate_channel_embeddings_task.run("tenant-1", "channel-1", limit=7, offset=3) == {
        "rotated_embeddings": 2,
        "limit": 7,
        "offset": 3,
    }
    assert calls == [
        ("channel", "tenant-1", "channel-1"),
        ("item", "tenant-1", "content-1"),
        ("rotate", "tenant-1", "channel-1"),
    ]


def test_embedding_maintenance_tasks_aggregate_tenant_summaries(monkeypatch: pytest.MonkeyPatch):
    session = SESSION_LOCAL()
    try:
        session.add_all([TenantOrm(id="tenant-a", name="A"), TenantOrm(id="tenant-b", name="B")])
        session.commit()
    finally:
        session.close()

    def fake_drift(session, **kwargs):
        return {
            "tenant_id": kwargs["tenant_id"],
            "channels_reindexed": 1,
            "rotated_embeddings": 2,
        }

    def fake_maintain(session, **kwargs):
        return {
            "tenant_id": kwargs["tenant_id"],
            "deleted_orphan_embeddings": 3,
            "deleted_malformed_embeddings": 4,
        }

    def fake_compact(session, **kwargs):
        return {
            "tenant_id": kwargs["tenant_id"],
            "deleted_candidate_embeddings": 5,
        }

    monkeypatch.setattr("postbridge.workers.tasks.reindex_embedding_drift", fake_drift)
    monkeypatch.setattr("postbridge.workers.tasks.maintain_embeddings", fake_maintain)
    monkeypatch.setattr("postbridge.workers.tasks.compact_embeddings", fake_compact)

    drift = reindex_embedding_drift_task.run(channel_limit=10, item_limit=20)
    maintain = maintain_embeddings_task.run(row_limit=10, after_id="after")
    compact = compact_embeddings_task.run(candidate_retention_days=30)

    assert drift["tenants_processed"] == 2
    assert drift["channels_reindexed"] == 2
    assert drift["rotated_embeddings"] == 4
    assert maintain["deleted_orphan_embeddings"] == 6
    assert maintain["deleted_malformed_embeddings"] == 8
    assert compact["deleted_candidate_embeddings"] == 10


def test_due_agent_and_cleanup_task_wrappers(monkeypatch: pytest.MonkeyPatch):
    delayed: list[tuple[str, str]] = []

    class Row:
        def __init__(self, id: str, tenant_id: str) -> None:
            self.id = id
            self.tenant_id = tenant_id

    def fake_list_due(_session):
        return [Row("task-1", "tenant-1"), Row("task-2", "tenant-2")]

    def fake_delay(task_id: str, tenant_id: str) -> None:
        delayed.append((task_id, tenant_id))

    def fake_cleanup(_session, **kwargs):
        return {"deleted_runs": 2, "deleted_review_items": 3, "kwargs": kwargs}

    monkeypatch.setattr("postbridge.workers.tasks.list_due_agent_tasks", fake_list_due)
    monkeypatch.setattr(run_agent_task_task, "delay", fake_delay)
    monkeypatch.setattr("postbridge.workers.tasks.cleanup_agent_runtime", fake_cleanup)

    assert process_due_agent_tasks_task.run() == 2
    assert delayed == [("task-1", "tenant-1"), ("task-2", "tenant-2")]
    result = cleanup_agent_runtime_task.run(tenant_id="tenant-1", retention_days=9)
    assert result["deleted_runs"] == 2
    assert result["deleted_review_items"] == 3
    assert result["kwargs"]["tenant_id"] == "tenant-1"
    assert result["kwargs"]["retention_days"] == 9


def test_simple_worker_wrappers_delegate_and_close(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("postbridge.workers.tasks.recover_stuck_publication_targets", lambda _s, timeout_seconds: 4)
    monkeypatch.setattr("postbridge.workers.tasks.reconcile_batch_import_runs", lambda _s: 5)
    monkeypatch.setattr("postbridge.workers.tasks.process_due_scheduled_postbridge_publishes", lambda _s: 6)

    assert recover_stuck_publication_targets_task.run() == 4
    assert reconcile_batch_import_runs_task.run() == 5
    assert process_scheduled_postbridge_publishes_task.run() == 6


def test_publish_agent_run_usage_event_task_respects_client_enabled(monkeypatch: pytest.MonkeyPatch):
    published: list[tuple[dict, str]] = []

    class DisabledClient:
        def is_enabled(self) -> bool:
            return False

        def publish_json_payload(self, payload: dict, correlation_id: str) -> None:
            published.append((payload, correlation_id))

    class EnabledClient(DisabledClient):
        def is_enabled(self) -> bool:
            return True

    monkeypatch.setattr("postbridge.workers.tasks.StatusEventClient", DisabledClient)
    assert publish_agent_run_usage_event_task.run({"event_id": "e1"}) == 0
    assert published == []

    monkeypatch.setattr("postbridge.workers.tasks.StatusEventClient", EnabledClient)
    assert publish_agent_run_usage_event_task.run({"event_id": "e2"}, "corr-2") == 1
    assert published == [({"event_id": "e2"}, "corr-2")]
