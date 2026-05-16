"""Structured operational logging for postbridge-core."""

import json
import logging
import sys
from typing import Any

_LOGGER_NAME = "postbridge.operational"
_FIELDS = (
    "event",
    "job_id",
    "correlation_id",
    "status",
    "retry_count",
    "error_code",
    "retryable",
)


def _get_logger() -> logging.Logger:
    """Возвращает настроенный JSON-логгер для postbridge."""
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


class _JsonFormatter(logging.Formatter):
    """Форматтер: логи в JSON для парсинга в ELK/Loki."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for key in _FIELDS:
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        for k, v in record.__dict__.items():
            if k not in ("name", "msg", "args", "created", "filename", "funcName", "levelname", "levelno", "lineno", "module", "msecs", "pathname", "process", "processName", "relativeCreated", "stack_info", "exc_info", "exc_text", "thread", "threadName", "message", "taskName") and k not in _FIELDS:
                payload[k] = v
        return json.dumps(payload, ensure_ascii=False)


def _log(
    event: str,
    message: str,
    *,
    job_id: str | None = None,
    correlation_id: str | None = None,
    status: str | None = None,
    retry_count: int | None = None,
    error_code: str | None = None,
    retryable: bool | None = None,
    **extra: Any,
) -> None:
    """Внутренняя функция: пишет структурированный JSON-лог с event и extra-полями."""
    logger = _get_logger()
    logger.info(
        message,
        extra={
            "event": event,
            "job_id": job_id,
            "correlation_id": correlation_id,
            "status": status,
            "retry_count": retry_count,
            "error_code": error_code,
            "retryable": retryable,
            **extra,
        },
    )


def log_job_created(
    job_id: str,
    correlation_id: str,
    *,
    idempotency_dedup: bool = False,
) -> None:
    """Логирует создание sync job (или idempotency dedup)."""
    event = "job.create.dedup" if idempotency_dedup else "job.create"
    _log(
        event,
        "Sync job created" if not idempotency_dedup else "Sync job returned (idempotency dedup)",
        job_id=job_id,
        correlation_id=correlation_id,
        status="pending",
        retry_count=0,
        idempotency_dedup=idempotency_dedup,
    )


def log_job_processing_start(job_id: str, correlation_id: str) -> None:
    """Логирует старт обработки job воркером."""
    _log(
        "job.processing.start",
        "Worker started processing sync job",
        job_id=job_id,
        correlation_id=correlation_id,
        status="running",
    )


def log_job_processing_skipped(job_id: str, correlation_id: str, reason: str = "duplicate") -> None:
    """Логирует пропуск обработки job (например, дубликат)."""
    _log(
        "job.processing.skipped",
        f"Job processing skipped: {reason}",
        job_id=job_id,
        correlation_id=correlation_id,
        skip_reason=reason,
    )


def log_job_completed(
    job_id: str,
    correlation_id: str,
    processed_posts: int,
    retry_count: int = 0,
) -> None:
    """Логирует успешное завершение sync job."""
    _log(
        "job.completed",
        "Sync job completed successfully",
        job_id=job_id,
        correlation_id=correlation_id,
        status="completed",
        retry_count=retry_count,
        processed_posts=processed_posts,
    )


def log_job_failed(
    job_id: str,
    correlation_id: str,
    error_code: str,
    retryable: bool,
    retry_count: int = 0,
) -> None:
    """Логирует падение sync job с кодом ошибки и флагом retryable."""
    _log(
        "job.failed",
        "Sync job failed",
        job_id=job_id,
        correlation_id=correlation_id,
        status="failed",
        error_code=error_code,
        retryable=retryable,
        retry_count=retry_count,
    )


def log_job_retry_scheduled(
    job_id: str,
    correlation_id: str,
    retry_count: int,
) -> None:
    """Логирует планирование retry для sync job."""
    _log(
        "job.retry.scheduled",
        "Retry scheduled for sync job",
        job_id=job_id,
        correlation_id=correlation_id,
        status="pending",
        retry_count=retry_count,
    )


def log_job_retry_exhausted(
    job_id: str,
    correlation_id: str,
    error_code: str,
    retry_count: int,
) -> None:
    """Логирует исчерпание лимита retry для sync job."""
    _log(
        "job.retry.exhausted",
        "Retry limit exhausted for sync job",
        job_id=job_id,
        correlation_id=correlation_id,
        status="failed",
        error_code=error_code,
        retryable=False,
        retry_count=retry_count,
    )


def log_status_event_published(
    job_id: str,
    correlation_id: str,
    status: str,
    event_id: str,
) -> None:
    """Логирует успешную публикацию статусного события в SaaS."""
    _log(
        "status.event.published",
        "Job status event published",
        job_id=job_id,
        correlation_id=correlation_id,
        status=status,
        event_id=event_id,
    )


def log_status_event_publish_failed(
    job_id: str,
    correlation_id: str,
    status: str,
    event_id: str,
    reason: str,
) -> None:
    """Логирует ошибку публикации статусного события."""
    _log(
        "status.event.publish_failed",
        "Failed to publish job status event",
        job_id=job_id,
        correlation_id=correlation_id,
        status=status,
        event_id=event_id,
        reason=reason,
    )


def log_jobs_recovered_stuck(correlation_id: str, recovered_jobs: int) -> None:
    """Логирует восстановление зависших running-джобов."""
    _log(
        "job.recovered.stuck",
        "Recovered stuck running jobs",
        correlation_id=correlation_id,
        recovered_jobs=recovered_jobs,
    )


def log_status_event_outbox_processed(processed: int) -> None:
    """Логирует обработку батча outbox статусных событий."""
    _log(
        "status.event.outbox.processed",
        "Processed status event outbox batch",
        processed=processed,
    )


def log_status_event_outbox_skipped(
    *,
    outbox_id: int,
    event_id: str,
    reason: str,
) -> None:
    """Логирует пропуск записи outbox (например, исчерпаны retry)."""
    _log(
        "status.event.outbox.skipped",
        "Skipped status event outbox entry",
        outbox_id=outbox_id,
        event_id=event_id,
        skip_reason=reason,
    )


def log_agent_run_started(run_id: str, *, tenant_id: str, channel_id: str, mode: str) -> None:
    _log(
        "agent.run.started",
        "Agent run started",
        job_id=run_id,
        status="running",
        tenant_id=tenant_id,
        channel_id=channel_id,
        mode=mode,
    )


def log_agent_run_completed(
    run_id: str,
    *,
    tenant_id: str,
    channel_id: str,
    mode: str,
    review_count: int,
    duration_ms: int | None = None,
    token_usage_total: int | None = None,
    tool_call_count: int | None = None,
    trace_policy: str | None = None,
) -> None:
    _log(
        "agent.run.completed",
        "Agent run completed",
        job_id=run_id,
        status="completed",
        tenant_id=tenant_id,
        channel_id=channel_id,
        mode=mode,
        review_count=review_count,
        duration_ms=duration_ms,
        token_usage_total=token_usage_total,
        tool_call_count=tool_call_count,
        trace_policy=trace_policy,
    )


def log_agent_run_failed(run_id: str, *, tenant_id: str, channel_id: str, mode: str, error_code: str) -> None:
    _log(
        "agent.run.failed",
        "Agent run failed",
        job_id=run_id,
        status="failed",
        error_code=error_code,
        tenant_id=tenant_id,
        channel_id=channel_id,
        mode=mode,
    )


def log_agent_step_completed(
    run_id: str,
    *,
    tenant_id: str,
    step_name: str,
    status: str,
    duration_ms: int | None = None,
) -> None:
    _log(
        "agent.step.completed",
        "Agent run step completed",
        job_id=run_id,
        tenant_id=tenant_id,
        status=status,
        step_name=step_name,
        duration_ms=duration_ms,
    )


def log_review_item_created(review_item_id: str, *, candidate_id: str, tenant_id: str, channel_id: str) -> None:
    _log(
        "agent.review_item.created",
        "Review queue item created",
        job_id=review_item_id,
        tenant_id=tenant_id,
        channel_id=channel_id,
        candidate_id=candidate_id,
    )


def log_review_item_resolved(review_item_id: str, *, candidate_id: str, decision: str, tenant_id: str) -> None:
    _log(
        "agent.review_item.resolved",
        "Review queue item resolved",
        job_id=review_item_id,
        tenant_id=tenant_id,
        candidate_id=candidate_id,
        decision=decision,
        status=decision,
    )


def log_agent_cleanup_completed(
    *,
    tenant_id: str | None,
    retention_days: int,
    deleted_runs: int,
    deleted_review_items: int,
) -> None:
    _log(
        "agent.cleanup.completed",
        "Agent runtime cleanup completed",
        tenant_id=tenant_id,
        retention_days=retention_days,
        deleted_runs=deleted_runs,
        deleted_review_items=deleted_review_items,
    )
