"""Outbox delivery for publication.target.status.changed events."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from postbridge.config import get_settings
from postbridge.integrations.status_event_client import StatusEventClient
from postbridge.observability.logging import (
    log_status_event_outbox_processed,
    log_status_event_outbox_skipped,
    log_status_event_publish_failed,
    log_status_event_published,
)
from postbridge.observability.metrics import (
    inc_publication_status_events_outbox_exhausted,
    inc_publication_status_events_publish_failed,
    inc_publication_status_events_published,
)
from postbridge.storage.publication_status_event_outbox import PublicationStatusEventOutboxStore
from postbridge.sync.status_events import retry_delay_seconds_for_status_outbox


def process_publication_status_event_outbox(store: PublicationStatusEventOutboxStore) -> int:
    """Publish pending publication status events to the configured webhook."""
    settings = get_settings()
    client = StatusEventClient()
    if not client.is_enabled():
        return 0
    now = datetime.now(UTC)
    rows = store.list_due(now=now, limit=settings.status_event_outbox_batch_size)
    if not rows:
        return 0
    processed = 0
    for row in rows:
        payload = json.loads(row.payload_json)
        target_payload = payload.get("publication_target", {})
        target_id = str(target_payload.get("id", row.publication_target_id))
        status = str(target_payload.get("status", "unknown"))
        correlation_id = row.correlation_id
        event_id = str(payload.get("event_id", row.event_id))
        try:
            client.publish_json_payload(payload, correlation_id=correlation_id)
            store.mark_sent(row.id)
            inc_publication_status_events_published()
            log_status_event_published(
                job_id=target_id,
                correlation_id=correlation_id,
                status=status,
                event_id=event_id,
            )
            processed += 1
        except Exception as exc:
            exhausted = (row.attempt_count + 1) >= settings.status_event_outbox_max_retries
            delay = retry_delay_seconds_for_status_outbox(row.attempt_count + 1)
            next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
            store.mark_failed(
                outbox_id=row.id,
                last_error=type(exc).__name__,
                next_attempt_at=next_attempt_at,
                exhausted=exhausted,
            )
            inc_publication_status_events_publish_failed()
            if exhausted:
                inc_publication_status_events_outbox_exhausted()
                log_status_event_outbox_skipped(
                    outbox_id=row.id,
                    event_id=event_id,
                    reason="retry_exhausted_publication",
                )
            log_status_event_publish_failed(
                job_id=target_id,
                correlation_id=correlation_id,
                status=status,
                event_id=event_id,
                reason=type(exc).__name__,
            )
            processed += 1
    log_status_event_outbox_processed(processed)
    return processed
