# Self-Host

Minimal stack for running Core without a hosted backend: Postgres, Redis, HTTP API, Celery worker, and Celery Beat.

## Quick Start

1. Copy `.env.example` to `.env`.
2. Set `CREDENTIALS_ENCRYPTION_KEY` (Fernet; see [credentials-encryption.md](credentials-encryption.md)).
3. Set `POSTGRES_PASSWORD` and make sure `DATABASE_URL` uses the same password. Keep the database host as `postgres` for the provided compose stack.
4. Set `CORE_SERVICE_TOKEN` if a trusted backend calls the internal Service API.
5. Set `STATUS_EVENT_WEBHOOK_*` if Core should deliver status events to a hosted receiver.

```bash
docker compose -f deploy/docker-compose.self-host.yml --env-file .env up -d --build
```

- API: `http://localhost:8000`
- Web UI: `http://localhost:8000/web`
- Health: `GET /health`, when enabled by the application.
- Metrics: `GET /metrics`

Migrations are run by the one-shot `core-migrate` service when the stack starts.

## Web UI

Core includes the shared Vite frontend workspace in `web/`. The Docker image builds it into `web/dist` and serves it at `/web`.

In self-host mode, the frontend uses only browser-safe `/api/app/*` endpoints:

- `GET /api/app/runtime-config`
- `GET /api/app/session`
- `POST /api/app/bootstrap`
- channels, content, agent tasks/runs, analytics, and review queue endpoints

In `POSTBRIDGE_APP_MODE=saas`, the Core web shell does not call product APIs directly; hosted product screens should be served by the private SaaS BFF.

Local frontend build:

```bash
cd web
VITE_BASE_PATH=/web/ VITE_POSTBRIDGE_APP_MODE=selfhost npm ci
VITE_BASE_PATH=/web/ VITE_POSTBRIDGE_APP_MODE=selfhost npm run build
```

## OpenAPI

See [openapi/README.md](openapi/README.md).

## Scheduled Postbridge Publishing

For drafts with `scheduled_publish_at` in `postbridge_extra` to move to `published` and enqueue live-sync work, both the Celery worker and Celery Beat must be running. Beat schedules `postbridge.postbridge.process_scheduled_publishes` every 60 seconds by default. `POSTBRIDGE_DEFAULT_TIMEZONE` can set the server default IANA timezone.

## Limits

- External integrations such as Telegram, MAX, VK, LinkedIn, and AI providers require their own secrets in `.env`.
- The internal Service API is for trusted server-to-server callers only.
- The browser-safe self-host API is exposed under `/api/app/*`.

## Public Container Image

Release tags publish a Docker image:

```text
ghcr.io/msrv-tech/postbridge-core:v0.1.0
```

Compose examples build locally by default so contributors can test changes without publishing an image.
