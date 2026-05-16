# Core Metrics

`GET /metrics` exports Prometheus text metrics.

**Batch import runs** use the `postbridge_batch_import_runs_` prefix. Duration and queue lag are exposed as `postbridge_batch_import_run_duration_seconds_*` and `postbridge_batch_import_run_queue_lag_seconds_*`.

**Sync, webhooks, and live publishing** use the `postbridge_sync_` prefix.

Operational context: [recovery-runbook.md](recovery-runbook.md).

| Name fragment | Meaning |
| --- | --- |
| `postbridge_batch_import_runs_created_total` | Created runs. |
| `postbridge_batch_import_runs_created_idempotency_dedup_total` | Existing run returned by idempotency key. |
| `postbridge_batch_import_runs_completed_total` | Completed runs. |
| `postbridge_batch_import_runs_failed_total` | Failed runs. |
| `postbridge_batch_import_runs_retry_scheduled_total` | Scheduled retries. |
| `postbridge_batch_import_runs_retry_exhausted_total` | Retry limit exhausted. |
| `postbridge_batch_import_runs_recovered_stuck_total` | Stuck `running` runs recovered automatically. |
| `postbridge_batch_import_run_duration_seconds_*` | Run duration. |
| `postbridge_batch_import_run_queue_lag_seconds_*` | Queue lag before processing starts. |
| `postbridge_sync_status_events_*`, `postbridge_sync_status_events_outbox_*` | Status event webhook delivery. |
| `postbridge_sync_live_publish_ok_total` / `postbridge_sync_live_publish_failed_total` | Internal live-sync publish results. |

Publication failures are grouped under `postbridge_publication_failures_` with labels such as `auth`, `rate_limit`, `validation`, `network`, `external_api`, and `other`.
