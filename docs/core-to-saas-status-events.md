# Core Status Event Delivery

Core can publish status events to a trusted hosted backend through a webhook. The webhook receiver is deployment-specific; Core only owns the event producer and retry/outbox behavior.

## Flow

1. When a `batch_import_runs` status changes, Core writes an event to `status_event_outbox`.
2. The periodic Celery task `postbridge.sync.dispatch_status_event_outbox` reads pending events.
3. The worker sends `POST STATUS_EVENT_WEBHOOK_URL`.
4. The receiver should deduplicate by `event_id` and update its own orchestration state by `batch_import_run.id`.

Progress-only updates to `processed_posts` do not create webhook events. Hosted backends should poll `GET /internal/service/batch-import-runs/{run_id}` if they need progress between status transitions.

## Core Environment

| Variable | Description |
| --- | --- |
| `STATUS_EVENT_WEBHOOK_URL` | Full receiver URL, for example `https://app.example.com/internal/core/events/status`. |
| `STATUS_EVENT_WEBHOOK_TOKEN` | Optional token sent as `X-Core-Event-Token`. |
| `STATUS_EVENT_WEBHOOK_TIMEOUT_SECONDS` | HTTP timeout, default `5`. |
| `STATUS_EVENT_OUTBOX_*` | Batch size, retry, and backoff settings. See [config.py](../src/postbridge/config.py). |

If `STATUS_EVENT_WEBHOOK_URL` is empty, events remain in the outbox and no delivery is attempted.

## Receiver Contract

If token protection is enabled, the receiver should require `X-Core-Event-Token` to match the configured shared value.

Request headers:

- `X-Correlation-Id`
- `X-Contract-Version`
- `X-Core-Event-Token` when configured

## Metrics

- `postbridge_sync_status_events_published_total`
- `postbridge_sync_status_events_publish_failed_total`
- `postbridge_sync_status_events_outbox_enqueued_total`
- `postbridge_sync_status_events_outbox_exhausted_total`

See [recovery-runbook.md](recovery-runbook.md) for operational checks.
