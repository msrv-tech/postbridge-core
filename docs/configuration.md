# Configuration

Postbridge Core is configured through environment variables. Start from [.env.example](../.env.example) and keep the resulting `.env` file private.

## Required Values

For a self-host Docker Compose deployment, set:

| Variable | Purpose |
| --- | --- |
| `POSTGRES_PASSWORD` | Password used by the bundled PostgreSQL service. |
| `DATABASE_URL` | SQLAlchemy database URL. The password must match `POSTGRES_PASSWORD`. |
| `CREDENTIALS_ENCRYPTION_KEY` | Fernet key used to encrypt channel credential JSON at rest. |

Generate the Fernet key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set `CORE_SERVICE_TOKEN` only when a trusted server-side component calls `/internal/service/*`.

The self-host Docker Compose file loads `.env` into Core API and worker containers through `env_file`, so integration variables from `.env` are available at runtime.

For validation commands that should not load local secrets, override the env-file path:

```bash
POSTBRIDGE_ENV_FILE=../.env.example docker compose -f deploy/docker-compose.self-host.yml --env-file .env.example config
```

## Frontend Runtime Mode

`POSTBRIDGE_APP_MODE` controls the server runtime mode.

| Mode | Intended use | Browser API |
| --- | --- | --- |
| `selfhost` | Public open-source self-host deployment. | Core `/api/app/*` endpoints. |
| `saas` | Private hosted deployment layer. | Hosted BFF owned outside this repository. |

For local and public self-host installs, use:

```env
POSTBRIDGE_APP_MODE=selfhost
```

The browser must never receive `CORE_SERVICE_TOKEN`, `SYNC_PUBLISH_TOKEN`, database credentials, or platform secrets.

## Platform Integrations

Only configure the integrations you plan to use.

| Integration | Common variables |
| --- | --- |
| Telegram import | `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_STRING` |
| Telegram bot/publish | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_PROXY_URL` |
| MAX | `MAX_API_BASE_URL`, `MAX_API_TOKEN` |
| VK | `VK_ACCESS_TOKEN`, `VK_USER_ACCESS_TOKEN` |
| LinkedIn | `LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_AUTHOR_URN`, `LINKEDIN_API_VERSION` |
| AI Gateway | `AI_GATEWAY_ENABLED`, `AI_GATEWAY_BASE_URL`, `AI_GATEWAY_API_KEY` |
| Media storage | `MEDIA_STORAGE_TYPE`, `MEDIA_STORAGE_PATH`, `S3_*` |

See [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md) for the full reference.

## How Users Provide Secrets

For Docker Compose installs, users put server-side secrets in the private `.env` file before starting or restarting the stack:

```bash
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Then edit `.env`, keep it out of Git, and restart services that read those values:

```bash
docker compose -f deploy/docker-compose.self-host.yml --env-file .env up -d --build
```

Use environment variables for install-wide secrets:

| Area | Configure in `.env` |
| --- | --- |
| Database | `POSTGRES_PASSWORD`, matching `DATABASE_URL` |
| Encryption | `CREDENTIALS_ENCRYPTION_KEY` |
| AI | `AI_GATEWAY_ENABLED=1`, `AI_GATEWAY_BASE_URL`, `AI_GATEWAY_API_KEY`, model variables as needed |
| Telegram import | `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_STRING` |
| Telegram bot | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, `BOT_BACKEND=core_db`, `BOT_MODE=long_polling` or webhook settings |
| Email | No required SMTP secret in Core self-host today; email delivery remains optional until a mail provider adapter is configured in code |
| MAX/VK/LinkedIn fallback credentials | `MAX_API_*`, `VK_*`, `LINKEDIN_*` |
| Media | `MEDIA_STORAGE_TYPE`, local path or `S3_*` |
| Internal service access | `CORE_SERVICE_TOKEN` only when another trusted backend calls Core |

Use the UI for per-channel credentials when possible. Those values are encrypted with `CREDENTIALS_ENCRYPTION_KEY` and stored in Core as channel credentials. Environment variables are best for install-wide defaults, local development, and integrations that do not yet have a UI credential flow.

## Secret Hygiene

- Keep `.env` out of Git.
- Use unique passwords and tokens per environment.
- Rotate credentials if they were pasted into tickets, logs, chats, or terminals shared with others.
- Do not reuse production secrets in local examples, tests, or documentation.
