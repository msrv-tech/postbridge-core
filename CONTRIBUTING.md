# Contributing

Thanks for taking the time to improve Postbridge Core.

## Development

Use the Docker Compose CI environment for tests:

```bash
docker compose --progress=plain -f ci/docker-compose.yml build
docker compose -f ci/docker-compose.yml run --rm test
```

For frontend-only changes:

```bash
cd web
npm ci
npm run check:i18n
npm run build
npm run test:e2e
npm run test:e2e:saas
```

## Guidelines

- Keep browser-facing Core code safe for public self-host deployments.
- Do not expose `CORE_SERVICE_TOKEN` or other server-to-server credentials to the browser.
- Prefer English for product-facing text, docs, tests, and code.
- Put frontend user-facing copy in `web/src/i18n/catalogs.js`; do not add hardcoded localized strings in components.
- Keep private hosted deployment logic outside this repository.
- Include focused tests for behavioral changes.
- Do not add private deployment URLs, customer data, production tokens, local databases, or generated sessions.
- Keep self-host behavior working without any private hosted services.

## Migrations

The public repository starts from a squashed greenfield Alembic baseline. New schema changes should be added as normal incremental migrations after `20260516_public_baseline`.
