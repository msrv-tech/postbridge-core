"""Завершение batch import run после publication_target (unified path)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from postbridge.db import BatchImportEnqueuedPostOrm, BatchImportRunOrm
from postbridge.domain.errors import ValidationError
from postbridge.domain.models import BatchImportRunStatus
from postbridge.models.domain import PublicationTargetOrm
from postbridge.observability.logging import log_job_completed, log_job_failed
from postbridge.observability.metrics import (
    inc_jobs_completed,
    inc_jobs_failed,
    observe_job_duration_seconds,
)
from postbridge.storage.batch_import_run_store import BatchImportRunStore


def reconcile_batch_import_runs(session: Session) -> int:
    """Обновляет running unified runs по статусам targets. Возвращает число run с терминальным переходом."""
    runs = list(
        session.scalars(
            select(BatchImportRunOrm).where(
                BatchImportRunOrm.status == BatchImportRunStatus.RUNNING.value,
                BatchImportRunOrm.target_core_channel_id.is_not(None),
                BatchImportRunOrm.batch_import_dispatch_enqueued_at.is_not(None),
            )
        ).all()
    )
    store = BatchImportRunStore(session)
    n = 0
    for run in runs:
        if _reconcile_one_run(session, store, run):
            n += 1
    return n


def _reconcile_one_run(session: Session, store: BatchImportRunStore, run: BatchImportRunOrm) -> bool:
    run_id = run.id
    target_ids = store.list_enqueued_target_ids(run_id)
    corr = run.correlation_id or "unknown"

    if not target_ids:
        processed = store.count_successful_deliveries(run_id)
        store.mark_completed(run_id, processed_posts=processed)
        _finish_metrics_and_log(run, corr)
        inc_jobs_completed()
        log_job_completed(run_id, corr, processed, run.retry_count)
        return True

    pending = 0
    published = 0
    failed = 0
    first_code: str | None = None
    first_msg: str | None = None
    for tid in target_ids:
        t = session.get(PublicationTargetOrm, tid)
        if t is None:
            continue
        if t.status in ("pending", "publishing"):
            pending += 1
        elif t.status == "published":
            published += 1
        elif t.status == "failed":
            failed += 1
            if first_code is None:
                first_code = t.error_code or "PUBLICATION_TARGET_FAILED"
                first_msg = t.error_message or first_code

    store.update_run_progress(run_id, published)
    if pending > 0:
        return False

    if failed > 0:
        err = ValidationError(
            code=first_code or "PUBLICATION_TARGET_FAILED",
            message=first_msg or "one or more publication targets failed",
            details={"run_id": run_id, "failed_targets": failed},
        )
        store.mark_failed(run_id, err, corr)
        inc_jobs_failed()
        log_job_failed(run_id, corr, err.code, err.retryable, run.retry_count)
        return True

    # Dedup-skip: строки в batch_import_enqueued_posts с publication_target_id=NULL.
    skips_ok = store.count_successful_deliveries(run_id)
    total_ok = published + skips_ok
    store.mark_completed(run_id, processed_posts=total_ok)
    _finish_metrics_and_log(run, corr)
    inc_jobs_completed()
    log_job_completed(run_id, corr, total_ok, run.retry_count)
    return True


def _finish_metrics_and_log(run: BatchImportRunOrm, corr: str) -> None:
    started = run.started_at
    if started is not None:
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        duration_sec = (datetime.now(UTC) - started).total_seconds()
        observe_job_duration_seconds(duration_sec)


def try_release_batch_import_published_post_claim(session: Session, target_id: str) -> None:
    """Снимает claim published_posts для импортированного поста, если target окончательно упал.

    Источник истины — строка в batch_import_enqueued_posts (связь run ↔ target), без парсинга meta_json.
    """
    row = session.scalar(
        select(BatchImportEnqueuedPostOrm).where(
            BatchImportEnqueuedPostOrm.publication_target_id == target_id
        )
    )
    if row is None:
        return
    run = session.get(BatchImportRunOrm, row.batch_import_run_id)
    if run is None:
        return
    store = BatchImportRunStore(session)
    store.release_claim(run.source_channel, row.source_post_id, run.target_channel)
    session.commit()
