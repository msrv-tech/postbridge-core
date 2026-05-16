# Recovery Runbook

This runbook covers Core batch imports, publication targets, live-sync work, and status event delivery.

## Main Signals

- `GET /metrics` exposes Prometheus metrics.
- Batch import counters use `postbridge_batch_import_runs_*`.
- Status webhook and live-sync counters use `postbridge_sync_*`.
- Publication failure classes use `postbridge_publication_failures_*`.
- Structured logs include `correlation_id`; batch import logs also include the run id as `job_id`.

## External API Failures

Symptoms:

- `postbridge_batch_import_runs_failed_total` grows with `EXTERNAL_API_*` codes.
- `postbridge_batch_import_runs_retry_scheduled_total` grows.
- Worker logs contain `job.failed` with `retryable=true`.

Checks:

- Inspect worker logs for `EXTERNAL_API`.
- Verify Telegram, MAX, VK, LinkedIn, RSS, or AI provider availability and credentials.
- Check rate limits and network restrictions.

Actions:

- Retryable errors are retried automatically according to `BATCH_IMPORT_RUN_*` settings.
- For authentication, permissions, or invalid channel errors, fix credentials and create or retry the run from a trusted backend.
- During provider incidents, pause new imports where possible and let retries drain after recovery.

## Stuck Runs Or Targets

Symptoms:

- Runs stay in `running` longer than expected.
- Publication targets stay in `pending` or `running`.
- `postbridge_batch_import_runs_recovered_stuck_total` grows.

Checks:

- `GET /internal/service/batch-import-runs/{run_id}` with `Authorization: Bearer <CORE_SERVICE_TOKEN>` and `X-Tenant-Id`.
- Database rows in `batch_import_runs`, `publication_targets`, and `batch_import_enqueued_posts`.
- Celery worker and beat health.
- Redis and database connectivity.

Actions:

- Ensure Celery worker and beat are running.
- `postbridge.sync.recover_stuck_jobs` recovers stuck batch import runs before dispatch.
- `postbridge.publication.recover_stuck_targets` recovers stuck publication targets.
- `postbridge.sync.reconcile_batch_import_runs` aggregates publication target status back into batch import runs.

## Retry Exhaustion

Symptoms:

- `postbridge_batch_import_runs_retry_exhausted_total` grows.
- Logs contain `job.retry.exhausted`.

Actions:

- Increase retry limits only for temporary provider failures.
- Fix permanent causes such as invalid credentials, missing permissions, or invalid channel identifiers.
- Re-run through a trusted backend once the cause is fixed.

## Idempotency

`POST /internal/service/batch-import-runs` supports `X-Idempotency-Key` and `idempotency_key`.

- First request returns `201`.
- Repeating the same key in the same tenant returns `200` and the existing run.
- Use a new key only when a genuinely new run is intended.

## Worker Degradation

Symptoms:

- Runs stay in `pending`.
- Logs do not show `job.processing.start`.
- Completed counters stop growing while created counters continue.

Checks:

- Celery worker ping.
- Redis queue health.
- Database connectivity.
- Worker import or configuration errors.

Actions:

- Restart workers and beat.
- Verify `REDIS_URL`, `DATABASE_URL`, and required secrets.
- Scale workers if queues are backlogged.

## Status Event Delivery

Core can deliver status events to a trusted receiver when `STATUS_EVENT_WEBHOOK_URL` is configured. The optional token is sent in `X-Core-Event-Token`.

Symptoms:

- `postbridge_sync_status_events_publish_failed_total` grows.
- Outbox rows remain `pending` or exhaust retries.
- The trusted backend lags behind Core while direct polling shows the current state.

Actions:

- Restore receiver reachability.
- Verify `STATUS_EVENT_WEBHOOK_TOKEN` on both sides.
- Inspect the `status_event_outbox` table and worker logs for `postbridge.sync.dispatch_status_event_outbox`.
- Reconcile failed outbox rows manually if retry attempts are exhausted.

## Migration And Schema Notes

For greenfield installs, run:

```bash
alembic upgrade head
```

After migration squashing, existing databases that were already on older revisions must be handled deliberately. Either keep the old migration chain for that environment until cutover, or stamp the database to the new public baseline after verifying that its schema matches:

```bash
alembic stamp 20260516_public_baseline
```

Do not run a squashed greenfield baseline blindly against production data.

## Secret Rotation

`CORE_SERVICE_TOKEN`:

1. Generate a new token.
2. Deploy the same value to Core and the trusted backend.
3. Restart both services.
4. Remove the old value after traffic is confirmed healthy.

`CREDENTIALS_ENCRYPTION_KEY`:

- Changing this key without re-encrypting rows makes existing channel credentials unreadable.
- For disposable environments, recreate channel credentials through the API.
- For production, plan an explicit re-encryption migration.
