# Architecture

Postbridge Core contains the public runtime for content import, adaptation, publishing, and self-host administration.

## Components

| Component | Responsibility |
| --- | --- |
| FastAPI app | Public health routes, browser-safe app API, internal service API, integration endpoints. |
| Worker | Celery tasks for imports, publication targets, retries, status events, and recovery loops. |
| PostgreSQL | Durable state: channels, credentials, content, plans, targets, jobs, status outbox. |
| Redis | Celery broker and runtime coordination. |
| React frontend | Shared UI built in `web/`, served by Core for self-host deployments. |
| Integrations | Telegram, MAX, VK, RSS/Zen, LinkedIn, AI gateway, and media storage adapters. |

## API Boundaries

Core has two main API surfaces:

| Surface | Path | Audience |
| --- | --- | --- |
| Browser-safe app API | `/api/app/*` | The self-host frontend. |
| Internal service API | `/internal/service/*` | Trusted server-side callers only. |

The internal service API is protected by `CORE_SERVICE_TOKEN`. It is not a browser API.

## Data Flow

1. A source channel imports or creates content.
2. Core stores canonical content in `content_items`.
3. Publication planning creates render variants and publication targets.
4. Workers publish targets through platform adapters.
5. Status changes are stored locally and can be dispatched through the status-event outbox.

## Frontend Modes

The same frontend codebase supports two runtime modes:

- `selfhost`: product screens call Core `/api/app/*` directly.
- `saas`: hosted product screens are expected to call a private BFF outside this repository.

This keeps the public Core deployable while preserving a clean server-to-server boundary for hosted deployments.

## Migrations

The public repository starts from a squashed Alembic baseline:

```text
20260516_public_baseline
```

New schema changes should be added as incremental migrations after that baseline.

