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

## Secret Hygiene

- Keep `.env` out of Git.
- Use unique passwords and tokens per environment.
- Rotate credentials if they were pasted into tickets, logs, chats, or terminals shared with others.
- Do not reuse production secrets in local examples, tests, or documentation.
