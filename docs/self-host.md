# Self-Host

Minimal stack for running Core without a hosted backend: Postgres, Redis, HTTP API, Celery worker, and Celery Beat.

## Quick Start

1. Generate a private `.env` with local random bootstrap secrets.
2. Start the stack.
3. Open the first-run setup wizard and create the local administrator.
4. Optionally add integration secrets in the wizard when they are needed immediately, or leave them empty.

```bash
python3 scripts/init_self_host_env.py
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
- `POST /api/app/auth/login`
- channels, content, agent tasks/runs, analytics, and review queue endpoints

In `POSTBRIDGE_APP_MODE=saas`, the Core web shell does not call product APIs directly; hosted product screens should be served by the private SaaS BFF.

Local frontend build:

```bash
cd web
VITE_BASE_PATH=/web/ VITE_POSTBRIDGE_APP_MODE=selfhost npm ci
VITE_BASE_PATH=/web/ VITE_POSTBRIDGE_APP_MODE=selfhost npm run build
```

On first launch, unauthenticated users are redirected to `/web/setup`. The wizard stores a local admin username and password hash encrypted at rest, then returns a browser session token. After that, self-host API calls require that token. If the database already has a self-host tenant but no local admin, the same wizard is used once to finish the transition.

## OpenAPI

See [openapi/README.md](openapi/README.md).

## Scheduled Postbridge Publishing

For drafts with `scheduled_publish_at` in `postbridge_extra` to move to `published` and enqueue live-sync work, both the Celery worker and Celery Beat must be running. Beat schedules `postbridge.postbridge.process_scheduled_publishes` every 60 seconds by default. `POSTBRIDGE_DEFAULT_TIMEZONE` can set the server default IANA timezone.

## Optional Integrations

- Postbridge source and RSS output work without external platform secrets.
- AI requires an AI gateway key only when AI features are enabled.
- Telegram import requires Telegram API credentials only when Telegram history import is used.
- Telegram publishing or bot onboarding requires a Telegram bot token only when Telegram is used.
- MAX, VK, and LinkedIn require their own credentials only when those platforms are used.
- Media storage can stay disabled unless upload or generated image storage is needed.

## Limits

- The internal Service API is for trusted server-to-server callers only.
- The browser-safe self-host API is exposed under `/api/app/*`.

## Public Container Image

Release tags publish a Docker image:

```text
ghcr.io/msrv-tech/postbridge-core:v0.1.0
```

Use [update.md](update.md) for the GHCR-based update flow. The source-build compose file remains available for contributors who need to test local changes before publishing an image.
