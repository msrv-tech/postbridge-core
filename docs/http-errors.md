# HTTP API: Correlation and Error Format

This applies to Core internal service API routes (`/internal/service/...`) and worker-facing error responses.

## Correlation

- Service endpoints accept `X-Correlation-Id`; Core generates one when the header is missing.
- Responses include `X-Correlation-Id` when relevant.
- Error envelopes always include `correlation_id`.

## Unified error envelope

```json
{
  "code": "STRING_CODE",
  "message": "human readable message",
  "details": {},
  "source": "core|telegram|max",
  "retryable": false,
  "correlation_id": "trace-id"
}
```

## HTTP Mapping

| Code / prefix | HTTP |
| --- | --- |
| `VALIDATION_MIGRATION_RUN_NOT_FOUND` | 404 |
| `VALIDATION_*` | 422 |
| `AUTH_*` | 403 |
| `EXTERNAL_API_*` | 502 |
| `INTERNAL_*` | 500 |

## Retry and Idempotency

- Workers schedule retries only when `retryable=true` and `retry_count < BATCH_IMPORT_RUN_MAX_RETRIES`.
- `POST /internal/service/batch-import-runs` supports idempotent creation through `X-Idempotency-Key` or `idempotency_key` in the body. The first response is `201`; a repeat with the same key returns `200` and the same run.

Relevant environment variables include `BATCH_IMPORT_RUN_MAX_RETRIES`, `BATCH_IMPORT_RUN_RETRY_DELAY_SECONDS`, `STATUS_EVENT_WEBHOOK_URL`, and `CORE_SERVICE_TOKEN`. See [recovery-runbook.md](recovery-runbook.md).

## Observability

- `GET /metrics` exposes Prometheus metrics; metric names are listed in [metrics.md](metrics.md).
- Runbook: [recovery-runbook.md](recovery-runbook.md).
