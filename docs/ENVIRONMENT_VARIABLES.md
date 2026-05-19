# Environment Variables

The canonical Core settings list is defined in [`src/postbridge/config.py`](../src/postbridge/config.py), in `get_settings()`.

## App mode

| Variable | Default | Description |
| --- | --- | --- |
| `POSTBRIDGE_APP_MODE` | `selfhost` | Runtime mode for the shared frontend and browser-safe app API. Allowed values: `selfhost`, `saas`. In `saas` mode the browser app should use the private SaaS BFF; Core `/internal/service/*` remains server-to-server only. |
| `POSTBRIDGE_VERSION` | package version | Current application version shown in self-host Settings. Release images set this at build time. |
| `POSTBRIDGE_RELEASE_REPOSITORY` | `msrv-tech/postbridge-core` | GitHub repository used by the self-host update checker. |
| `POSTBRIDGE_CONTAINER_IMAGE` | `ghcr.io/msrv-tech/postbridge-core` | Container image name used when Settings builds the manual update command. |
| `POSTBRIDGE_SELFHOST_TENANT_ID` | `00000000-0000-4000-8000-000000000001` | Stable tenant id used by the self-host `/api/app/*` context. Ignored by SaaS BFF flows. |

## Frontend build flags

These are Vite build-time flags for `web/`. The public Core defaults are intentionally neutral; hosted SaaS deployments should pass their own values from the private deployment layer.

| Variable | Default | Description |
| --- | --- | --- |
| `VITE_BASE_PATH` | `/web/` | Base path for the built frontend. Use `/` for hosted root deployments. |
| `VITE_POSTBRIDGE_APP_MODE` | `selfhost` | Frontend mode: `selfhost` uses Core `/api/app/*`, `saas` uses the private BFF paths. |
| `VITE_YANDEX_METRIKA_COUNTER_ID` | empty | Optional analytics counter. Empty disables Metrika. |
| `VITE_TELEGRAM_BOT_NAME` | empty | Optional Telegram bot username used in UI links. Empty hides the bot link in self-host builds. |
| `VITE_MAX_BOT_URL` | empty | Optional MAX bot URL used in UI hints. |
| `VITE_BILLING_SUPPORT_EMAIL` | `support@example.com` | Optional support address shown in billing UI. |

Example template: [`.env.example`](../.env.example). For production, copy it to your deployment environment and replace all placeholder values.

## AI Gateway

| Variable | Default | Description |
| --- | --- | --- |
| `AI_GATEWAY_ENABLED` | `0` | Enables AI features when gateway credentials are configured. |
| `AI_GATEWAY_BASE_URL` | empty | OpenAI-compatible base URL, for example `https://api.openai.com/v1`. |
| `AI_GATEWAY_API_KEY` | empty | API key for the configured provider. |
| `AI_GATEWAY_TIMEOUT_SECONDS` | `300` in self-host, `60` in SaaS | Timeout for text AI requests. |
| `AI_GATEWAY_DEFAULT_MODEL` | empty | Default text model when a request does not pass one explicitly. |
| `AI_GATEWAY_DEFAULT_RESPONSE_LANGUAGE` | empty | Optional default response language for AI output. |
| `AI_IMAGE_GENERATION_MODEL` | empty | Image generation model id; Core does not fall back to the text model. |
| `AI_IMAGE_GENERATION_TIMEOUT_SECONDS` | `300` in self-host, `120` in SaaS | Timeout for image generation and generated image download. |
| `AI_IMAGE_GENERATION_SIZE` | `1536x1024` | Image size passed to the provider. |
