# Quick Start

This guide starts a local self-host Postbridge Core stack with Docker Compose.

## Requirements

- Docker and Docker Compose
- Git
- Open network access for the platform integrations you enable

## 1. Configure Environment

```bash
cp .env.example .env
```

Fill at least these values:

- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- `CREDENTIALS_ENCRYPTION_KEY`
- `CORE_SERVICE_TOKEN`

Generate strong random values. Keep `.env` local and never commit it.

`DATABASE_URL` must use the same PostgreSQL password as `POSTGRES_PASSWORD`.

## 2. Start Core

```bash
docker compose -f deploy/docker-compose.self-host.yml --env-file .env up -d --build
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

The frontend runs in `selfhost` mode and talks to Core through browser-safe `/api/app/*` endpoints.

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
