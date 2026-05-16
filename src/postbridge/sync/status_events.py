from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from postbridge.config import get_settings
from postbridge.integrations.status_event_client import StatusEvent, StatusEventClient
from postbridge.observability.logging import (
    log_status_event_outbox_processed,
    log_status_event_outbox_skipped,
    log_status_event_publish_failed,
    log_status_event_published,
)
from postbridge.observability.metrics import (
    inc_status_events_outbox_exhausted,
    inc_status_events_publish_failed,
    inc_status_events_published,
)
from postbridge.storage.batch_import_run_store import BatchImportRunStore


def process_status_event_outbox(store: BatchImportRunStore) -> int:
    """Process pending status event outbox rows."""
    settings = get_settings()
    client = StatusEventClient()
    if not client.is_enabled():
        return 0
    now = datetime.now(UTC)
    rows = store.list_due_status_events_outbox(
        now=now,
        limit=settings.status_event_outbox_batch_size,
    )
    if not rows:
        return 0
    processed = 0
    for row in rows:
        payload = json.loads(row.payload_json)
        event = StatusEvent(
            event_id=str(payload["event_id"]),
            contract_version=str(payload["contract_version"]),
            event_type=str(payload["event_type"]),
            occurred_at=str(payload["occurred_at"]),
            batch_import_run=payload["batch_import_run"],
        )
        run_payload = payload.get("batch_import_run", {})
        run_id = str(run_payload.get("id", row.batch_import_run_id))
        status = str(run_payload.get("status", "unknown"))
        correlation_id = str(run_payload.get("correlation_id", row.correlation_id))
        try:
            client.publish(event, correlation_id=correlation_id)
            store.mark_status_event_outbox_sent(row.id)
            inc_status_events_published()
            log_status_event_published(
                job_id=run_id,
                correlation_id=correlation_id,
                status=status,
                event_id=event.event_id,
            )
            processed += 1
        except Exception as exc:
            exhausted = (row.attempt_count + 1) >= settings.status_event_outbox_max_retries
            delay = retry_delay_seconds_for_status_outbox(row.attempt_count + 1)
            next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
            store.mark_status_event_outbox_failed(
                outbox_id=row.id,
                last_error=type(exc).__name__,
                next_attempt_at=next_attempt_at,
                exhausted=exhausted,
            )
            inc_status_events_publish_failed()
            if exhausted:
                inc_status_events_outbox_exhausted()
                log_status_event_outbox_skipped(
                    outbox_id=row.id,
                    event_id=event.event_id,
                    reason="retry_exhausted",
                )
            log_status_event_publish_failed(
                job_id=run_id,
                correlation_id=correlation_id,
                status=status,
                event_id=event.event_id,
                reason=type(exc).__name__,
            )
            processed += 1
    log_status_event_outbox_processed(processed)
    return processed


def retry_delay_seconds_for_status_outbox(attempt: int) -> int:
    """Compute exponential backoff delay before the next attempt."""
    settings = get_settings()
    base = settings.status_event_outbox_retry_delay_seconds
    factor = settings.status_event_outbox_backoff_multiplier
    max_delay = settings.status_event_outbox_retry_max_delay_seconds
    return min(int(base * (factor ** max(attempt - 1, 0))), max_delay)
