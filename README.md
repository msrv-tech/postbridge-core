# postbridge-core

[![CI](https://github.com/msrv-tech/postbridge-core/actions/workflows/ci.yml/badge.svg)](https://github.com/msrv-tech/postbridge-core/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Open-source publishing, migration, and automation engine for Postbridge Core.

Postbridge Core helps teams import content, prepare publication plans, and deliver posts across connected channels from a self-hosted stack.

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

See [docs/quickstart.md](docs/quickstart.md) for the complete self-host walkthrough.

1. Copy `.env.example` to `.env` and fill the required secrets.
   Generate unique values for `POSTGRES_PASSWORD`, `DATABASE_URL`, `CREDENTIALS_ENCRYPTION_KEY`, and any platform credentials you enable.
2. Start the stack:

```bash
docker compose -f deploy/docker-compose.self-host.yml --env-file .env up -d --build
```

3. Open the app:

```text
http://127.0.0.1:8000/web
```

The self-host compose file runs migrations automatically through the `core-migrate` service.

Default self-host ports:

- Core API: `http://127.0.0.1:8000`
- Web UI: `http://127.0.0.1:8000/web`

## Demo

After the stack is running, follow [docs/demo-walkthrough.md](docs/demo-walkthrough.md) to bootstrap a local workspace, create demo channels, and inspect the content-to-bridge flow without external platform credentials.

## Container Image

Release tags publish a public Docker image to GitHub Container Registry:

```text
ghcr.io/msrv-tech/postbridge-core:v0.1.1
```

Compose examples build locally by default so contributors can test changes before publishing images.

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

## Releases

Version tags such as `v0.1.0` publish a Docker image to GitHub Container Registry:

```text
ghcr.io/msrv-tech/postbridge-core
```

## Documentation

- [docs/quickstart.md](docs/quickstart.md)
- [docs/demo-walkthrough.md](docs/demo-walkthrough.md)
- [docs/configuration.md](docs/configuration.md)
- [docs/self-host.md](docs/self-host.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/saas-vs-self-host.md](docs/saas-vs-self-host.md)
- [docs/ENVIRONMENT_VARIABLES.md](docs/ENVIRONMENT_VARIABLES.md)
- [docs/app-api.md](docs/app-api.md)
- [docs/i18n.md](docs/i18n.md)
- [docs/recovery-runbook.md](docs/recovery-runbook.md)
- [docs/telegram-proxy.md](docs/telegram-proxy.md)
- [CHANGELOG.md](CHANGELOG.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [SUPPORT.md](SUPPORT.md)
- [ROADMAP.md](ROADMAP.md)

## Security

Do not commit `.env`, local databases, credentials, or generated build artifacts. Run a secret scan before publishing forks or changing repository visibility.

Use [SECURITY.md](SECURITY.md) for vulnerability reporting.
