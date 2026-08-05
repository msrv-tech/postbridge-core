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

## Release Updates

Self-host Settings checks `POSTBRIDGE_RELEASE_REPOSITORY` for the latest GitHub Release and compares it to `POSTBRIDGE_VERSION`. Published GHCR images set `POSTBRIDGE_VERSION` at build time. The UI only displays the available update and a pinned GHCR command; it does not auto-update the server.

Use `POSTBRIDGE_CONTAINER_IMAGE` if you publish a forked image under a different registry path.

## Platform Integrations

Only configure the integrations you plan to use.

| Integration | Common variables |
| --- | --- |
| Telegram import | `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_STRING` |
| Telegram bot/publish | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_PROXY_URL` |
| MAX | `MAX_API_BASE_URL`, `MAX_API_TOKEN` |
| VK | `VK_ACCESS_TOKEN`, `VK_USER_ACCESS_TOKEN` |
| LinkedIn | `LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_AUTHOR_URN`, `LINKEDIN_API_VERSION` |
| Facebook Pages | `FACEBOOK_PAGE_ACCESS_TOKEN`, `FACEBOOK_PAGE_ID`, `META_GRAPH_API_VERSION` |
| Instagram Business | `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID`, `META_GRAPH_API_VERSION` |
| X | `X_ACCESS_TOKEN` |
| Bluesky | `BLUESKY_IDENTIFIER`, `BLUESKY_APP_PASSWORD`, `BLUESKY_SERVICE_URL` |
| Mastodon | `MASTODON_ACCESS_TOKEN`, `MASTODON_INSTANCE_URL`, `MASTODON_VISIBILITY` |
| AI Gateway | `AI_GATEWAY_ENABLED`, `AI_GATEWAY_BASE_URL`, `AI_GATEWAY_API_KEY` |
| Media storage | `MEDIA_STORAGE_TYPE`, `MEDIA_STORAGE_PATH`, `S3_*` |

See [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md) for the full reference.

## How Users Provide Secrets

For Docker Compose installs, users generate a private `.env` file before starting the stack:

```bash
python3 scripts/init_self_host_env.py
```

The generated file contains random persistent bootstrap secrets and is safe to use as-is for the first launch. Keep it out of Git. If you later edit `.env`, restart services that read those values:

```bash
docker compose -f deploy/docker-compose.self-host.yml --env-file .env up -d --build
```

Use the first-run setup wizard for the local administrator and for optional integration secrets that are available there. Use environment variables for bootstrap secrets and install-wide fallbacks:

| Area | Configure in `.env` |
| --- | --- |
| Database | `POSTGRES_PASSWORD`, matching `DATABASE_URL` |
| Encryption | `CREDENTIALS_ENCRYPTION_KEY` |
| AI | `AI_GATEWAY_ENABLED=1`, `AI_GATEWAY_BASE_URL`, `AI_GATEWAY_API_KEY`, model variables as needed |
| Telegram import | `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_STRING` |
| Telegram bot | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, `BOT_BACKEND=core_db`, `BOT_MODE=long_polling` or webhook settings |
| Email | No required SMTP secret in Core self-host today; email delivery remains optional until a mail provider adapter is configured in code |
| MAX/VK/LinkedIn/global publisher fallback credentials | `MAX_API_*`, `VK_*`, `LINKEDIN_*`, `FACEBOOK_*`, `INSTAGRAM_*`, `X_*`, `BLUESKY_*`, `MASTODON_*` |
| Media | `MEDIA_STORAGE_TYPE`, local path or `S3_*` |
| Internal service access | `CORE_SERVICE_TOKEN` only when another trusted backend calls Core |

Use the UI for the first-run local admin, wizard-supported install secrets, and per-channel credentials when possible. Those values are encrypted with `CREDENTIALS_ENCRYPTION_KEY` and stored in Core. Environment variables are best for bootstrap values, local development, install-wide defaults, and integrations that do not yet have a UI credential flow.

Optional integrations are needed only when the matching feature is used:

| Feature | Secret needed |
| --- | --- |
| Postbridge source + RSS demo | none beyond generated bootstrap `.env` |
| AI text adaptation, agents, image generation | AI gateway settings |
| Telegram import | Telegram API ID/hash and session |
| Telegram publishing or bot onboarding | Telegram bot token and username |
| MAX, VK, LinkedIn, Facebook, Instagram, X, Bluesky, Mastodon | platform credentials for the selected platform |
| Uploaded/generated media persistence | local media storage or S3 settings |

## Secret Hygiene

- Keep `.env` out of Git.
- Use unique passwords and tokens per environment.
- Rotate credentials if they were pasted into tickets, logs, chats, or terminals shared with others.
- Do not reuse production secrets in local examples, tests, or documentation.
