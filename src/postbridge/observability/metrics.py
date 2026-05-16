"""Operational metrics for postbridge-core (Prometheus-compatible export)."""

import threading

_METRICS: dict[str, float] = {}
_LOCK = threading.Lock()
# Счётчики batch import run (таблица batch_import_runs).
_BATCH_RUN_COUNT_PREFIX = "postbridge_batch_import_runs_"
# Длительность и lag одного run (histogram-style sum/count).
_BATCH_RUN_TIMING_PREFIX = "postbridge_batch_import_run_"
_SYNC_MISC_PREFIX = "postbridge_sync_"
_AGENT_PREFIX = "postbridge_agent_"


def _inc(name: str, delta: float = 1.0) -> None:
    """Инкрементирует счётчик метрики (thread-safe)."""
    with _LOCK:
        _METRICS[name] = _METRICS.get(name, 0) + delta


def _observe(name: str, value: float) -> None:
    """Добавляет значение к метрике (thread-safe)."""
    with _LOCK:
        _METRICS[name] = _METRICS.get(name, 0) + value


def inc_jobs_created() -> None:
    """Инкремент: созданные batch import run."""
    _inc(f"{_BATCH_RUN_COUNT_PREFIX}created_total")


def inc_jobs_created_idempotency_dedup() -> None:
    """Инкремент: dedup по idempotency key."""
    _inc(f"{_BATCH_RUN_COUNT_PREFIX}created_idempotency_dedup_total")


def inc_jobs_completed() -> None:
    """Инкремент: успешно завершённые batch import run."""
    _inc(f"{_BATCH_RUN_COUNT_PREFIX}completed_total")


def inc_jobs_failed() -> None:
    """Инкремент: упавшие batch import run."""
    _inc(f"{_BATCH_RUN_COUNT_PREFIX}failed_total")


def inc_jobs_retry_scheduled() -> None:
    """Инкремент: запланированные retry."""
    _inc(f"{_BATCH_RUN_COUNT_PREFIX}retry_scheduled_total")


def inc_jobs_retry_exhausted() -> None:
    """Инкремент: исчерпан лимит retry."""
    _inc(f"{_BATCH_RUN_COUNT_PREFIX}retry_exhausted_total")


def observe_job_duration_seconds(duration_seconds: float) -> None:
    """Наблюдает длительность выполнения batch import run (секунды)."""
    _observe(f"{_BATCH_RUN_TIMING_PREFIX}duration_seconds_sum", duration_seconds)
    _inc(f"{_BATCH_RUN_TIMING_PREFIX}duration_seconds_count")


def observe_queue_lag_seconds(lag_seconds: float) -> None:
    """Наблюдает задержку между созданием run и стартом обработки (секунды)."""
    _observe(f"{_BATCH_RUN_TIMING_PREFIX}queue_lag_seconds_sum", lag_seconds)
    _inc(f"{_BATCH_RUN_TIMING_PREFIX}queue_lag_seconds_count")


def inc_status_events_published() -> None:
    """Инкремент: опубликованные статусные события в SaaS."""
    _inc(f"{_SYNC_MISC_PREFIX}status_events_published_total")


def inc_status_events_publish_failed() -> None:
    """Инкремент: ошибки публикации статусных событий."""
    _inc(f"{_SYNC_MISC_PREFIX}status_events_publish_failed_total")


def inc_status_events_received() -> None:
    """Инкремент: полученные статусные события (входящие)."""
    _inc(f"{_SYNC_MISC_PREFIX}status_events_received_total")


def inc_jobs_recovered_stuck() -> None:
    """Инкремент: восстановленные зависшие batch import run."""
    _inc(f"{_BATCH_RUN_COUNT_PREFIX}recovered_stuck_total")


def inc_status_events_outbox_enqueued() -> None:
    """Инкремент: добавленные в outbox статусные события."""
    _inc(f"{_SYNC_MISC_PREFIX}status_events_outbox_enqueued_total")


def inc_status_events_outbox_exhausted() -> None:
    """Инкремент: записи outbox с исчерпанными retry."""
    _inc(f"{_SYNC_MISC_PREFIX}status_events_outbox_exhausted_total")


def inc_publication_status_events_outbox_enqueued() -> None:
    _inc(f"{_SYNC_MISC_PREFIX}publication_status_events_outbox_enqueued_total")


def inc_publication_status_events_published() -> None:
    _inc(f"{_SYNC_MISC_PREFIX}publication_status_events_published_total")


def inc_publication_status_events_publish_failed() -> None:
    _inc(f"{_SYNC_MISC_PREFIX}publication_status_events_publish_failed_total")


def inc_publication_status_events_outbox_exhausted() -> None:
    _inc(f"{_SYNC_MISC_PREFIX}publication_status_events_outbox_exhausted_total")


def inc_live_publish_ok() -> None:
    """Инкремент: успешная публикация live-sync в MAX."""
    _inc(f"{_SYNC_MISC_PREFIX}live_publish_ok_total")


def inc_live_publish_failed() -> None:
    """Инкремент: ошибка публикации live-sync в MAX."""
    _inc(f"{_SYNC_MISC_PREFIX}live_publish_failed_total")


def inc_publication_failure(class_: str) -> None:
    """Инкремент: неуспех publication_target по классу ошибки (фаза 7)."""
    allowed = {"auth", "rate_limit", "validation", "network", "external_api", "other"}
    key = class_ if class_ in allowed else "other"
    _inc(f"postbridge_publication_failures_{key}_total")


def inc_agent_run_started(mode: str) -> None:
    _inc(f"{_AGENT_PREFIX}runs_started_total")
    _inc(f"{_AGENT_PREFIX}runs_started_{mode}_total")


def inc_agent_run_completed(mode: str) -> None:
    _inc(f"{_AGENT_PREFIX}runs_completed_total")
    _inc(f"{_AGENT_PREFIX}runs_completed_{mode}_total")


def inc_agent_run_failed(mode: str) -> None:
    _inc(f"{_AGENT_PREFIX}runs_failed_total")
    _inc(f"{_AGENT_PREFIX}runs_failed_{mode}_total")


def inc_agent_review_item_created() -> None:
    _inc(f"{_AGENT_PREFIX}review_items_created_total")


def inc_agent_review_item_resolved(decision: str) -> None:
    _inc(f"{_AGENT_PREFIX}review_items_resolved_total")
    _inc(f"{_AGENT_PREFIX}review_items_resolved_{decision}_total")


def observe_agent_run_duration_seconds(mode: str, duration_seconds: float) -> None:
    _observe(f"{_AGENT_PREFIX}run_duration_seconds_sum", duration_seconds)
    _inc(f"{_AGENT_PREFIX}run_duration_seconds_count")
    _observe(f"{_AGENT_PREFIX}run_duration_{mode}_seconds_sum", duration_seconds)
    _inc(f"{_AGENT_PREFIX}run_duration_{mode}_seconds_count")


def observe_agent_step_duration_seconds(step_name: str, duration_seconds: float) -> None:
    _observe(f"{_AGENT_PREFIX}step_duration_seconds_sum", duration_seconds)
    _inc(f"{_AGENT_PREFIX}step_duration_seconds_count")
    _observe(f"{_AGENT_PREFIX}step_duration_{step_name}_seconds_sum", duration_seconds)
    _inc(f"{_AGENT_PREFIX}step_duration_{step_name}_seconds_count")


def observe_agent_token_usage(mode: str, total_tokens: int) -> None:
    _observe(f"{_AGENT_PREFIX}token_usage_total", float(total_tokens))
    _observe(f"{_AGENT_PREFIX}token_usage_{mode}_total", float(total_tokens))


def observe_agent_tool_calls(count: int) -> None:
    _observe(f"{_AGENT_PREFIX}tool_calls_total", float(count))


def get_all() -> dict[str, float]:
    """Возвращает все метрики (для тестов)."""
    with _LOCK:
        return dict(_METRICS)


def export_prometheus() -> str:
    """Экспортирует метрики в формате Prometheus text exposition."""
    with _LOCK:
        lines: list[str] = []
        for name, value in sorted(_METRICS.items()):
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")
        return "\n".join(lines) if lines else "# No metrics yet\n"


def reset_for_tests() -> None:
    """Сбрасывает все метрики. Только для тестов."""
    with _LOCK:
        _METRICS.clear()
