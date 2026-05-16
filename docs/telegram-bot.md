# Telegram Bot

Postbridge now uses a thin bot runtime in `postbridge-core`.

Telegram is currently the first platform adapter on top of that runtime model.

The intent is to keep the runtime shape reusable for future bot adapters on other platforms while keeping each platform-specific integration isolated in its own adapter.

## Product role

The Telegram bot is intentionally minimal:

- Telegram auth and account linking
- channel attach
- redirect to the web app for setup, migration, and AI workflows
- live-sync runtime for attached channels

The bot is not a full product surface anymore.

## Runtime model

There is one Telegram bot app with two configuration dimensions:

- `BOT_MODE=webhook|long_polling`
- `BOT_BACKEND=saas|core_db`

Examples:

- SaaS: `BOT_MODE=webhook`, `BOT_BACKEND=saas`
- self-hosted: `BOT_MODE=long_polling`, `BOT_BACKEND=core_db`

Backend meaning:

- `saas`
  - Telegram deep-link auth is enabled
  - channel attach is registered through SaaS internal endpoints
  - web handoff points to SaaS workspace screens
- `core_db`
  - there is no SaaS auth flow
  - live-sync routing is resolved directly from Core `bridges` and `channels`
  - the bot acts as a lightweight connector for self-hosted installs

## Behavior

### Auth

- In SaaS mode, the website can start Telegram login/linking via deep link.
- The bot completes the deep-link flow and sends the user back to the website.

### Channel attach

- The user adds the bot to a Telegram channel as an administrator.
- The bot confirms the attach.
- The bot sends a link to the relevant web screen.

### Web handoff

Commands such as `/start`, `/help`, `/add`, `/sync`, `/plan`, `/max`, `/vk`, `/rss` do not run product setup inside Telegram.

They redirect the user to the website instead.

### Live-sync

The bot still processes:

- `channel_post`
- `edited_channel_post`
- media groups

and forwards them through the shared live-sync queue.

## Localization

Telegram user-facing text now goes through the shared i18n layer in `postbridge.i18n`.

- bundled locales: `en`, `ru`
- locale comes from the Core-wide `POSTBRIDGE_DEFAULT_LOCALE` setting
- message text is addressed by stable keys, not inline strings

See [i18n.md](i18n.md) for the shared localization contract that future bot adapters and web surfaces should reuse.

## Architectural intent

The bot runtime is designed around three separations:

- platform adapter
  - Telegram-specific events, auth, attach semantics, media extraction
- transport mode
  - webhook or long polling
- backend profile
  - SaaS-backed or Core-DB-backed routing

This keeps Telegram-specific logic reusable across deployment modes without keeping separate legacy bot applications.

In code, the current foundation is split into:

- shared contracts and models
  - `postbridge.botkit.interfaces`
  - `postbridge.botkit.models`
- shared runtime helpers
  - `postbridge.botkit.*`
- Telegram adapter
  - `postbridge.botkit.platforms.telegram.*`

## Entrypoint

For polling mode:

```bash
postbridge-telegram-bot
```

For webhook mode:

- run the Core API process
- the webhook is mounted by `postbridge.api.main`
