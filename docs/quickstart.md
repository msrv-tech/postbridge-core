# Quick Start

This guide starts a local self-host Postbridge Core stack with Docker Compose.

## Requirements

- Docker and Docker Compose
- Git
- Open network access for the platform integrations you enable

## 1. Generate Local Bootstrap Secrets

```bash
python3 scripts/init_self_host_env.py
```

This creates a private `.env` with random persistent bootstrap secrets:

- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- `CREDENTIALS_ENCRYPTION_KEY`
- `CORE_SERVICE_TOKEN`

You do not need to manually enter integration secrets before the first start. Configure AI, Telegram, media storage, and platform credentials later only when you enable those features. Keep `.env` local and never commit it.

The generated `DATABASE_URL` uses the same PostgreSQL password as `POSTGRES_PASSWORD` and the compose host `postgres`.

## 2. Start Core

```bash
docker compose -f deploy/docker-compose.self-host.yml --env-file .env up -d --build
```

The compose file loads `.env` into the Core containers. For config validation without loading local secrets, use:

```bash
POSTBRIDGE_ENV_FILE=../.env.example docker compose -f deploy/docker-compose.self-host.yml --env-file .env.example config
```

The self-host compose file starts:

- PostgreSQL
- Redis
- database migrations
- Core API with the bundled frontend
- Core worker with beat enabled

## 3. Open the App

Open:

```text
http://127.0.0.1:8000
```

On first launch, Core redirects to the self-host setup wizard. Create the local administrator there. Optional integrations such as AI Gateway, Telegram bot, and media storage can be filled in the wizard when you need them immediately, or left empty.

After setup, sign in with the local admin credentials. The frontend runs in `selfhost` mode and talks to Core through browser-safe `/api/app/*` endpoints.

For production installs based on published images, use `deploy/docker-compose.release.yml`. The Settings page will show when a newer GitHub release is available and provide the pinned GHCR update command. See [update.md](update.md).

The built frontend is also available under:

```text
http://127.0.0.1:8000/web
```

## 4. Check Health

```bash
curl -fsS http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok"}
```

## 5. Stop the Stack

```bash
docker compose -f deploy/docker-compose.self-host.yml --env-file .env down
```

To remove the local database volume too:

```bash
docker compose -f deploy/docker-compose.self-host.yml --env-file .env down -v
```

## Troubleshooting

- If the API cannot connect to PostgreSQL, check that `DATABASE_URL` and `POSTGRES_PASSWORD` use the same password.
- If migrations fail, inspect `docker compose` logs for `core-migrate`.
- If the frontend loads but API requests fail, confirm `POSTBRIDGE_APP_MODE=selfhost`.
- If an integration behaves as if credentials are missing, configure that integration in the UI when available, or set the matching install-wide fallback in `.env` and restart `core-api` and `core-worker`.
