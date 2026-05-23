from dataclasses import dataclass

import pytest

from postbridge.domain.errors import ExternalApiError
from postbridge.workers.tasks import process_batch_import_run_task


@dataclass
class DummyBatchImportRun:
    target_core_channel_id: str | None = None
    retry_count: int = 0


def test_process_batch_import_run_task_retryable_error_schedules_retry(monkeypatch: pytest.MonkeyPatch):
    @dataclass
    class DummySettings:
        batch_import_run_max_retries: int = 10
        batch_import_run_retry_delay_seconds: int = 5
        batch_import_run_retry_backoff_multiplier: float = 2.0
        batch_import_run_retry_max_delay_seconds: int = 60

    def fail_run(self, job_id: str, correlation_id: str | None = None) -> int:
        raise ExternalApiError(
            code="EXTERNAL_API_MAX_REQUEST_ERROR",
            message="MAX API transport error",
            source="max",
            retryable=True,
            details={"target_channel": "max/test"},
        )

    scheduled_calls: list[tuple[str, str, int]] = []
    apply_async_calls: list[dict[str, object]] = []

    def fake_get_run(self, job_id: str) -> DummyBatchImportRun:
        return DummyBatchImportRun(target_core_channel_id=None, retry_count=3)

    def fake_schedule_retry(self, job_id: str, correlation_id: str, max_retries: int) -> bool:
        scheduled_calls.append((job_id, correlation_id, max_retries))
        return True

    def fake_apply_async(*, args: list[object], countdown: int, **_kwargs: object) -> None:
        apply_async_calls.append({"args": args, "countdown": countdown})

    monkeypatch.setattr("postbridge.workers.tasks.get_settings", lambda: DummySettings())
    monkeypatch.setattr("postbridge.sync.service.SyncService.run_job", fail_run)
    monkeypatch.setattr("postbridge.workers.tasks.BatchImportRunStore.get_run", fake_get_run)
    monkeypatch.setattr(
        "postbridge.workers.tasks.BatchImportRunStore.schedule_retry", fake_schedule_retry
    )
    monkeypatch.setattr(process_batch_import_run_task, "apply_async", fake_apply_async)

    # Silence side effects (logging/metrics) while still executing branches.
    monkeypatch.setattr("postbridge.workers.tasks.log_job_retry_scheduled", lambda *_a, **_k: None)
    monkeypatch.setattr("postbridge.workers.tasks.inc_jobs_retry_scheduled", lambda: None)

    ret = process_batch_import_run_task.run("job-1", "corr-retry")
    assert ret == 0
    assert scheduled_calls == [("job-1", "corr-retry", 10)]
    assert len(apply_async_calls) == 1
    assert apply_async_calls[0]["args"] == ["job-1", "corr-retry"]
    countdown = apply_async_calls[0]["countdown"]
    assert isinstance(countdown, int)
    assert 0 <= countdown <= 60


def test_process_batch_import_run_task_retry_exhausted_raises(monkeypatch: pytest.MonkeyPatch):
    @dataclass
    class DummySettings:
        batch_import_run_max_retries: int = 1
        batch_import_run_retry_delay_seconds: int = 5
        batch_import_run_retry_backoff_multiplier: float = 2.0
        batch_import_run_retry_max_delay_seconds: int = 60

    def fail_run(self, job_id: str, correlation_id: str | None = None) -> int:
        raise ExternalApiError(
            code="EXTERNAL_API_MAX_REQUEST_ERROR",
            message="MAX API transport error",
            source="max",
            retryable=True,
            details={"target_channel": "max/test"},
        )

    exhausted_calls: list[tuple[str, str, str, int]] = []
    inc_calls: list[str] = []

    def fake_get_run(self, job_id: str) -> DummyBatchImportRun:
        return DummyBatchImportRun(target_core_channel_id=None, retry_count=9)

    def fake_schedule_retry(self, job_id: str, correlation_id: str, max_retries: int) -> bool:
        return False

    def fake_log_retry_exhausted(job_id: str, correlation_id: str, code: str, retry_count: int) -> None:
        exhausted_calls.append((job_id, correlation_id, code, retry_count))

    def fake_inc_retry_exhausted() -> None:
        inc_calls.append("inc")

    monkeypatch.setattr("postbridge.workers.tasks.get_settings", lambda: DummySettings())
    monkeypatch.setattr("postbridge.sync.service.SyncService.run_job", fail_run)
    monkeypatch.setattr("postbridge.workers.tasks.BatchImportRunStore.get_run", fake_get_run)
    monkeypatch.setattr(
        "postbridge.workers.tasks.BatchImportRunStore.schedule_retry", fake_schedule_retry
    )
    monkeypatch.setattr("postbridge.workers.tasks.log_job_retry_exhausted", fake_log_retry_exhausted)
    monkeypatch.setattr("postbridge.workers.tasks.inc_jobs_retry_exhausted", fake_inc_retry_exhausted)
    monkeypatch.setattr(process_batch_import_run_task, "apply_async", lambda *a, **k: None)

    with pytest.raises(ExternalApiError):
        process_batch_import_run_task.run("job-2", "corr-exhausted")

    assert exhausted_calls == [("job-2", "corr-exhausted", "EXTERNAL_API_MAX_REQUEST_ERROR", 9)]
    assert inc_calls == ["inc"]


def test_process_batch_import_run_task_non_retryable_error_propagates(monkeypatch: pytest.MonkeyPatch):
    def fail_run(self, job_id: str, correlation_id: str | None = None) -> int:
        raise ExternalApiError(
            code="EXTERNAL_API_MAX_REQUEST_ERROR",
            message="MAX API bad request",
            source="max",
            retryable=False,
            details={"target_channel": "max/test"},
        )

    called: list[str] = []

    def fake_get_run(self, job_id: str) -> DummyBatchImportRun:
        return DummyBatchImportRun(target_core_channel_id=None, retry_count=0)

    def fake_schedule_retry(self, *_a: object, **_k: object) -> bool:
        called.append("schedule_retry")
        return True

    monkeypatch.setattr("postbridge.sync.service.SyncService.run_job", fail_run)
    monkeypatch.setattr("postbridge.workers.tasks.BatchImportRunStore.get_run", fake_get_run)
    monkeypatch.setattr(
        "postbridge.workers.tasks.BatchImportRunStore.schedule_retry", fake_schedule_retry
    )

    with pytest.raises(ExternalApiError):
        process_batch_import_run_task.run("job-3", "corr-nonretry")

    assert called == []


def test_process_batch_import_run_task_unified_dispatch_aborts_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_run(self, job_id: str, correlation_id: str | None = None) -> int:
        raise ExternalApiError(
            code="EXTERNAL_API_TEMP",
            message="temp",
            source="max",
            retryable=True,
        )

    scheduled_calls: list[str] = []

    def fake_get_run(self, job_id: str) -> DummyBatchImportRun:
        return DummyBatchImportRun(target_core_channel_id="core-1", retry_count=0)

    def fake_schedule_retry(self, *_a: object, **_k: object) -> bool:
        scheduled_calls.append("scheduled")
        return True

    monkeypatch.setattr("postbridge.sync.service.SyncService.run_job", fail_run)
    monkeypatch.setattr("postbridge.workers.tasks.BatchImportRunStore.get_run", fake_get_run)
    monkeypatch.setattr(
        "postbridge.workers.tasks.BatchImportRunStore.schedule_retry", fake_schedule_retry
    )

    with pytest.raises(ExternalApiError):
        process_batch_import_run_task.run("job-unified", "corr-unified")

    assert scheduled_calls == []


def test_process_batch_import_run_task_retryable_error_schedules_retry_without_job_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @dataclass
    class DummySettings:
        batch_import_run_max_retries: int = 10
        batch_import_run_retry_delay_seconds: int = 5
        batch_import_run_retry_backoff_multiplier: float = 2.0
        batch_import_run_retry_max_delay_seconds: int = 20

    def fail_run(self, job_id: str, correlation_id: str | None = None) -> int:
        raise ExternalApiError(
            code="EXTERNAL_API_TEMP",
            message="temp",
            source="max",
            retryable=True,
        )

    scheduled_calls: list[tuple[str, str, int]] = []
    apply_async_calls: list[dict[str, object]] = []

    def fake_get_run(self, job_id: str) -> DummyBatchImportRun | None:
        return None

    def fake_schedule_retry(self, job_id: str, correlation_id: str, max_retries: int) -> bool:
        scheduled_calls.append((job_id, correlation_id, max_retries))
        return True

    def fake_apply_async(*, args: list[object], countdown: int, **_kwargs: object) -> None:
        apply_async_calls.append({"args": args, "countdown": countdown})

    monkeypatch.setattr("postbridge.workers.tasks.get_settings", lambda: DummySettings())
    monkeypatch.setattr("postbridge.sync.service.SyncService.run_job", fail_run)
    monkeypatch.setattr("postbridge.workers.tasks.BatchImportRunStore.get_run", fake_get_run)
    monkeypatch.setattr(
        "postbridge.workers.tasks.BatchImportRunStore.schedule_retry", fake_schedule_retry
    )
    monkeypatch.setattr(process_batch_import_run_task, "apply_async", fake_apply_async)
    monkeypatch.setattr("postbridge.workers.tasks.log_job_retry_scheduled", lambda *_a, **_k: None)
    monkeypatch.setattr("postbridge.workers.tasks.inc_jobs_retry_scheduled", lambda: None)

    ret = process_batch_import_run_task.run("job-missing", "corr-missing")
    assert ret == 0
    assert scheduled_calls == [("job-missing", "corr-missing", 10)]
    assert len(apply_async_calls) == 1
    assert apply_async_calls[0]["args"] == ["job-missing", "corr-missing"]
    countdown = apply_async_calls[0]["countdown"]
    assert isinstance(countdown, int)
    assert 0 <= countdown <= 20


def test_process_batch_import_run_task_retry_exhausted_missing_job_row_skips_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @dataclass
    class DummySettings:
        batch_import_run_max_retries: int = 1
        batch_import_run_retry_delay_seconds: int = 5
        batch_import_run_retry_backoff_multiplier: float = 2.0
        batch_import_run_retry_max_delay_seconds: int = 20

    def fail_run(self, job_id: str, correlation_id: str | None = None) -> int:
        raise ExternalApiError(
            code="EXTERNAL_API_TEMP",
            message="temp",
            source="max",
            retryable=True,
        )

    exhausted_calls: list[str] = []
    inc_calls: list[str] = []

    def fake_get_run(self, job_id: str) -> DummyBatchImportRun | None:
        return None

    def fake_schedule_retry(self, *_a: object, **_k: object) -> bool:
        return False

    monkeypatch.setattr("postbridge.workers.tasks.get_settings", lambda: DummySettings())
    monkeypatch.setattr("postbridge.sync.service.SyncService.run_job", fail_run)
    monkeypatch.setattr("postbridge.workers.tasks.BatchImportRunStore.get_run", fake_get_run)
    monkeypatch.setattr(
        "postbridge.workers.tasks.BatchImportRunStore.schedule_retry", fake_schedule_retry
    )
    monkeypatch.setattr(
        "postbridge.workers.tasks.log_job_retry_exhausted", lambda *_a, **_k: exhausted_calls.append("log")
    )
    monkeypatch.setattr(
        "postbridge.workers.tasks.inc_jobs_retry_exhausted", lambda: inc_calls.append("inc")
    )

    with pytest.raises(ExternalApiError):
        process_batch_import_run_task.run("job-exh-missing", "corr-exh-missing")

    assert exhausted_calls == []
    assert inc_calls == []
