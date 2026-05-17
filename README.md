# postbridge-core

[![CI](https://github.com/msrv-tech/postbridge-core/actions/workflows/ci.yml/badge.svg)](https://github.com/msrv-tech/postbridge-core/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Open-source publishing, migration, and automation engine for Postbridge Core.

`postbridge-core` contains the public runtime:

- channel fetchers and publishers for Telegram, MAX, VK, RSS/Zen, LinkedIn publishing, and the built-in Postbridge source;
- batch import runs and publication targets;
- live-sync delivery, status outbox, and worker recovery loops;
- the shared React frontend with two runtime modes: `selfhost` and `saas`;
- browser-safe self-host API under `/api/app/*`;
- server-to-server service API under `/internal/service/*`.

The service API is protected by `CORE_SERVICE_TOKEN` and is intended for trusted backends. The browser must never receive that token.

## Platforms

| Source | Target | Notes |
| --- | --- | --- |
| `postbridge` | - | Built-in content source backed by Core `content_items`. |
| `telegram` | `telegram` | Import through Telethon, publish through Bot API. |
| `max` | `max` | Import and publish through MAX API. |
| `vk` | `vk` | Import and publish through VK wall APIs. |
| `rss` / `zen` | `rss` / `zen` | RSS import and feed-oriented publishing flow. |
| - | `linkedin` | Organic post publishing through LinkedIn Posts API. |

See [docs/platforms.md](docs/platforms.md) for credentials and platform-specific behavior.

## Frontend Modes

The frontend lives in `web/` and is built with Vite.

- `VITE_POSTBRIDGE_APP_MODE=selfhost`: the browser uses Core `/api/app/*`.
- `VITE_POSTBRIDGE_APP_MODE=saas`: the browser uses a hosted BFF API owned by the private deployment layer.

Core serves the self-host app without exposing internal service credentials.

## Local Run

1. Copy `.env.example` to `.env` and fill the required secrets.
   Generate unique values for `POSTGRES_PASSWORD`, `DATABASE_URL`, `CREDENTIALS_ENCRYPTION_KEY`, and any platform credentials you enable.
2. Start the stack:

```bash
docker compose up -d --build
```

3. Apply migrations:

```bash
docker compose exec api alembic upgrade head
```

Default local ports:

- Core API: `http://127.0.0.1:8000`
- Core API compatibility port: `http://127.0.0.1:8010`
- Redis: `6380`
- Postgres: `5433`

## Self-Host Compose

For a standalone Core stack:

```bash
cp .env.example .env
docker compose -f deploy/docker-compose.self-host.yml --env-file .env up -d --build
```

The self-host compose file runs Postgres, Redis, migrations, API, and worker.

## Tests

The canonical test environment is Docker Compose CI:

```bash
docker compose --progress=plain -f ci/docker-compose.yml build
docker compose -f ci/docker-compose.yml run --rm test
```

Focused runs:

```bash
docker compose --progress=plain -f ci/docker-compose.yml build test
docker compose -f ci/docker-compose.yml run --rm test pytest -q tests/test_app_public_api.py -k '...'
```

## Migrations

```bash
alembic upgrade head
```

Production schema is not created at runtime. Apply migrations before serving traffic.

## Deployment

Core deployments should:

1. build the Core API and worker images;
2. start Postgres and Redis;
3. run `alembic upgrade head`;
4. restart Core API and worker services;
5. serve the bundled frontend from the Core API image or from a separate static host.

The provided GitHub Actions workflow deploys only after CI passes on `main` and targets a trusted self-hosted runner labeled `prod-core`.

## Documentation

- [docs/app-api.md](docs/app-api.md)
- [CHANGELOG.md](CHANGELOG.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [docs/ENVIRONMENT_VARIABLES.md](docs/ENVIRONMENT_VARIABLES.md)
- [docs/i18n.md](docs/i18n.md)
- [docs/recovery-runbook.md](docs/recovery-runbook.md)
- [docs/telegram-proxy.md](docs/telegram-proxy.md)

## Security

Do not commit `.env`, local databases, credentials, or generated build artifacts. Run a secret scan before publishing forks or changing repository visibility.

Use [SECURITY.md](SECURITY.md) for vulnerability reporting.
