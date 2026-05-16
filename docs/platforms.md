# Platforms

Postbridge Core uses a fetcher/publisher registry selected by `source_platform` and `target_platform`.

## Registry

| Key | Fetcher | Publisher | Notes |
| --- | --- | --- | --- |
| `postbridge` | PostbridgeWorkspaceFetcher | - | Built-in Core content source. |
| `telegram` | TelegramFetcher | TelegramPublisher | History import through Telethon; publishing through Bot API. |
| `max` | MaxFetcher | MaxPublisher | Import and publish through MAX API. |
| `vk` | VKFetcher | VKPublisher | Import and publish through VK wall APIs. |
| `rss` | RssFetcher | RssPublisher | Generic RSS import and feed publishing. |
| `zen` | ZenFetcher | ZenPublisher | RSS-based Zen import/publishing helper. |
| `linkedin` | - | LinkedInPublisher | Organic posts through LinkedIn Posts API: text, images, video, PDF/document. |

## Credentials

- **telegram:** `api_id`, `api_hash`, `session_string` (env: TELEGRAM_*)
- **max:** `base_url`, `token` (env: MAX_API_*)
- **vk:** `access_token` (env: VK_ACCESS_TOKEN)
- **linkedin:** `access_token`, optional `author_urn`, optional `api_version` (env fallback: `LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_AUTHOR_URN`, `LINKEDIN_API_VERSION`)

## Limits

- **VK:** requires an access token with `wall`, `offline`, or relevant group permissions.
- **LinkedIn:** source import is not supported yet. Media publishing requires LinkedIn OAuth scopes for the selected author (`w_member_social` or `w_organization_social`).
