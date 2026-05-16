from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from postbridge.db import Base, ENGINE, SESSION_LOCAL, BatchImportRunOrm, init_db  # noqa: E402
from postbridge.domain.errors import ExternalApiError  # noqa: E402
from postbridge.storage.batch_import_run_store import BatchImportRunStore  # noqa: E402
from postbridge.workers.tasks import (  # noqa: E402
    dispatch_status_event_outbox_task,
    process_batch_import_run_task,
    recover_stuck_jobs_task,
)
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
