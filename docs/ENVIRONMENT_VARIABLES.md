# Environment Variables

The canonical Core settings list is defined in [`src/postbridge/config.py`](../src/postbridge/config.py), in `get_settings()`.

## App mode

| Variable | Default | Description |
| --- | --- | --- |
| `POSTBRIDGE_APP_MODE` | `selfhost` | Runtime mode for the shared frontend and browser-safe app API. Allowed values: `selfhost`, `saas`. In `saas` mode the browser app should use the private SaaS BFF; Core `/internal/service/*` remains server-to-server only. |
| `POSTBRIDGE_SELFHOST_TENANT_ID` | `00000000-0000-4000-8000-000000000001` | Stable tenant id used by the self-host `/api/app/*` context. Ignored by SaaS BFF flows. |

## Frontend build flags

These are Vite build-time flags for `web/`. The public Core defaults are intentionally neutral; hosted SaaS deployments should pass their own values from the private deployment layer.

| Variable | Default | Description |
| --- | --- | --- |
| `VITE_BASE_PATH` | `/web/` | Base path for the built frontend. Use `/` for hosted root deployments. |
| `VITE_POSTBRIDGE_APP_MODE` | `selfhost` | Frontend mode: `selfhost` uses Core `/api/app/*`, `saas` uses the private BFF paths. |
| `VITE_YANDEX_METRIKA_COUNTER_ID` | empty | Optional analytics counter. Empty disables Metrika. |
| `VITE_TELEGRAM_BOT_NAME` | `postbridge_bot` | Optional Telegram bot username used in UI links. |
| `VITE_MAX_BOT_URL` | empty | Optional MAX bot URL used in UI hints. |
| `VITE_BILLING_SUPPORT_EMAIL` | `support@example.com` | Optional support address shown in billing UI. |

Example template: [`.env.example`](../.env.example). For production, copy it to your deployment environment and replace all placeholder values.
