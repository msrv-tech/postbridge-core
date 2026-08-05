# Platforms

Postbridge Core uses a fetcher/publisher registry selected by `source_platform` and `target_platform`.

## Registry

| Key | Fetcher | Publisher | Notes |
| --- | --- | --- | --- |
| `postbridge` | PostbridgeWorkspaceFetcher | - | Built-in Core content source. It cannot be used as a target. |
| `telegram` | TelegramFetcher | TelegramPublisher | History import through Telethon; publishing through Bot API. |
| `max` | MaxFetcher | MaxPublisher | Import and publish through MAX API. |
| `vk` | VKFetcher | VKPublisher | Import and publish through VK wall APIs. |
| `rss` | RssFetcher | RssPublisher | Generic RSS import and feed publishing. |
| `zen` | ZenFetcher | ZenPublisher | RSS-based Zen import/publishing helper. |
| `linkedin` | - | LinkedInPublisher | Organic posts through LinkedIn Posts API: text, images, video, PDF/document. |
| `facebook` | - | FacebookPublisher | Facebook Page text posts through Meta Graph API. |
| `instagram` | - | InstagramPublisher | Instagram Business single-image/video publishing through Meta Graph API. |
| `x` | - | XPublisher | Text and media posts through X API v2. |
| `bluesky` | - | BlueskyPublisher | Text and image posts through AT Protocol XRPC. |
| `mastodon` | - | MastodonPublisher | Text and media statuses through a configured Mastodon instance. |

## Credentials

- **telegram:** `api_id`, `api_hash`, `session_string` (env: TELEGRAM_*)
- **max:** `base_url`, `token` (env: MAX_API_*)
- **vk:** `access_token` (env: VK_ACCESS_TOKEN)
- **linkedin:** `access_token`, optional `author_urn`, optional `api_version` (env fallback: `LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_AUTHOR_URN`, `LINKEDIN_API_VERSION`)
- **facebook:** `page_access_token`, optional `page_id`, optional `graph_api_version` (env fallback: `FACEBOOK_PAGE_ACCESS_TOKEN`, `FACEBOOK_PAGE_ID`, `META_GRAPH_API_VERSION`)
- **instagram:** `access_token`, optional `instagram_user_id`, optional `graph_api_version` (env fallback: `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID`, `META_GRAPH_API_VERSION`)
- **x:** `access_token` (env fallback: `X_ACCESS_TOKEN`)
- **bluesky:** `identifier`, `app_password`, optional `service_url` (env fallback: `BLUESKY_IDENTIFIER`, `BLUESKY_APP_PASSWORD`, `BLUESKY_SERVICE_URL`)
- **mastodon:** `access_token`, optional `instance_url`, optional `visibility` (env fallback: `MASTODON_ACCESS_TOKEN`, `MASTODON_INSTANCE_URL`, `MASTODON_VISIBILITY`)

Self-host Core exposes helper endpoints that are also suitable for a SaaS BFF contract:

- `POST /api/app/credentials/oauth/authorize-url`
- `POST /api/app/credentials/oauth/token`
- `POST /api/app/credentials/meta/pages`
- `POST /api/app/credentials/platform/validate`
- `POST /api/app/credentials/platform/manual`

The OAuth helpers build provider authorization URLs and exchange authorization codes when the corresponding app credentials are configured. `credentials/meta/pages` discovers Facebook Pages and linked Instagram Business accounts from a Meta user token. `credentials/platform/validate` verifies manual publishing credentials against the provider without storing plaintext. `credentials/platform/manual` stores validated account/Page credentials encrypted in Core.

These endpoints do not replace provider app setup, app review, redirect URI registration, or scope approval. Before those external steps are complete, Core can still validate manually created tokens, store encrypted channel credentials, and publish through the provider APIs supported below.

## Limits

- **VK:** requires an access token with `wall`, `offline`, or relevant group permissions.
- **LinkedIn:** source import is not supported yet. Media publishing requires LinkedIn OAuth scopes for the selected author (`w_member_social` or `w_organization_social`).
- **Facebook:** source import is not supported yet. Publishing supports text, single photo, single video, and multi-photo Page feed posts. Publishing requires a Page access token with the permissions approved for Page content publishing.
- **Instagram:** source import is not supported yet. Publishing supports single image/video and image carousel containers with status polling. Publishing requires an Instagram professional account connected to a Page and publicly reachable media URLs.
- **X:** source import is not supported yet. Publishing requires an OAuth access token with post write permission; media uploads require media write permission.
- **Bluesky:** source import is not supported yet. Use an app password, not the account password. Image embeds are uploaded as AT Protocol blobs.
- **Mastodon:** source import is not supported yet. Instance-specific character and media limits may differ from Core's default 500-character rule limit.
