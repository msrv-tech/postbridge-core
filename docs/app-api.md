# Browser-safe App API

This API is intended for the shared Core frontend.

It is separate from `/internal/service/*`:

- `/api/app/*` is safe for browser-facing frontend code.
- `/internal/service/*` is server-to-server only and requires `CORE_SERVICE_TOKEN`.
- The browser must never receive `CORE_SERVICE_TOKEN`.

## Runtime config

`GET /api/app/runtime-config`

Returns non-secret runtime configuration:

```json
{
  "app_mode": "selfhost",
  "api": {
    "base_path": "/api/app"
  },
  "i18n": {
    "default_locale": "en",
    "locale_locked": false
  },
  "features": {
    "billing": {"enabled": false},
    "workspaces": {"enabled": false},
    "multi_tenant": {"enabled": false},
    "managed_credentials": {"enabled": false},
    "local_auth": {"enabled": true},
    "agent": {"enabled": true},
    "media_generation": {"enabled": true},
    "review_queue": {"enabled": true}
  }
}
```

In `POSTBRIDGE_APP_MODE=saas`, the shared frontend should use the private SaaS BFF for authenticated product calls. Core still exposes `/api/app/runtime-config` as a safe capability surface, but `/internal/service/*` remains private to server-side callers.

## Session

`GET /api/app/session`

Returns the local self-host session context. V1 uses a single local admin identity over the self-host tenant:

```json
{
  "app_mode": "selfhost",
  "bootstrapped": true,
  "authenticated": true,
  "user": {
    "id": "local-admin",
    "display_name": "Local Admin",
    "role": "admin"
  },
  "tenant": {
    "id": "00000000-0000-4000-8000-000000000001",
    "name": "Postbridge Self-host",
    "created_at": "2026-05-13T00:00:00+00:00",
    "updated_at": "2026-05-13T00:00:00+00:00"
  }
}
```

Before bootstrap, `bootstrapped` and `authenticated` are `false`.

## Bootstrap

`POST /api/app/bootstrap`

Creates the default self-host tenant if it does not exist. The operation is idempotent.

Request:

```json
{
  "tenant_name": "Postbridge Self-host"
}
```

Response shape is the same as `GET /api/app/session`.

## Channels

Channels are scoped to the self-host tenant.

`GET /api/app/channels`

Optional filters:

- `platform`
- `kind`
- `status`

Response:

```json
{
  "items": [
    {
      "id": "channel-id",
      "tenant_id": "00000000-0000-4000-8000-000000000001",
      "platform": "telegram",
      "kind": "source",
      "title": "Telegram Source",
      "external_id": "@source",
      "status": "connected",
      "config": {},
      "capabilities": {},
      "created_at": "2026-05-13T00:00:00+00:00",
      "updated_at": "2026-05-13T00:00:00+00:00"
    }
  ]
}
```

## Content Items

Content item endpoints expose the self-host editor surface for `source_type=postbridge`.

`GET /api/app/content-items`

Optional filters:

- `status`: `draft` or `published`
- `limit`: `1..200`, default `50`
- `offset`: default `0`

`POST /api/app/content-items`

Creates a draft or published content item.

Request:

```json
{
  "title": "Draft title",
  "content_md": "Markdown body",
  "content_plain": "Plain body",
  "media_url": null,
  "media_urls": null,
  "summary": "Short summary",
  "link_url": null,
  "cta": null,
  "tags": ["news"],
  "author": null,
  "cover_image_url": null,
  "status": "draft",
  "scheduled_publish_at": null,
  "live_sync_source_core_channel_id": null
}
```

`published` content requires non-empty `content_md`.

`scheduled_publish_at` requires:

- `status=draft`
- a future timezone-aware datetime on the 5-minute UTC grid
- `live_sync_source_core_channel_id` pointing to a self-host `postbridge` source channel

`GET /api/app/content-items/{content_id}`

Returns one content item.

`PATCH /api/app/content-items/{content_id}`

Updates editable fields, status, schedule, and live-sync source.

`DELETE /api/app/content-items/{content_id}`

Deletes one content item and returns `204`.
```
```

`POST /api/app/channels`

Creates a channel without credentials. Credential management is intentionally separate because it handles encrypted secrets.

Request:

```json
{
  "platform": "telegram",
  "kind": "source",
  "title": "Telegram Source",
  "external_id": "@source",
  "status": "connected",
  "config": {},
  "capabilities": {}
}
```

`GET /api/app/channels/{channel_id}`

Returns one channel.

`DELETE /api/app/channels/{channel_id}`

Deletes one channel and returns `204`.

## Channel Credentials

Credential endpoints are separate from channel CRUD because they handle encrypted secrets.

`GET /api/app/channels/{channel_id}/credential`

Returns metadata only. It never returns plaintext secrets.

```json
{
  "id": "credential-id",
  "channel_id": "channel-id",
  "auth_type": "api_key",
  "status": "active",
  "has_secret": true,
  "expires_at": null,
  "created_at": "2026-05-13T00:00:00+00:00",
  "updated_at": "2026-05-13T00:00:00+00:00"
}
```

If no credential exists, `has_secret` is `false`.

`PUT /api/app/channels/{channel_id}/credential`

Encrypts and stores the supplied secret JSON.

Request:

```json
{
  "auth_type": "api_key",
  "status": "active",
  "secret": {
    "token": "secret-token"
  }
}
```

Response shape is the same metadata-only shape as `GET`.

`DELETE /api/app/channels/{channel_id}/credential`

Deletes stored credentials and returns `204`.

## Bridges

Bridges connect a source channel to a target channel. In self-host mode the owner identity is the local admin.

`GET /api/app/bridges`

Optional filters:

- `mode`
- `status`
- `source_channel_id`
- `target_channel_id`

Response:

```json
{
  "items": [
    {
      "id": "bridge-id",
      "tenant_id": "00000000-0000-4000-8000-000000000001",
      "owner_user_id": "local-admin",
      "source_channel_id": "source-channel-id",
      "target_channel_id": "target-channel-id",
      "status": "active",
      "mode": "live_sync",
      "settings": {},
      "created_at": "2026-05-13T00:00:00+00:00",
      "updated_at": "2026-05-13T00:00:00+00:00"
    }
  ]
}
```

`POST /api/app/bridges`

Request:

```json
{
  "source_channel_id": "source-channel-id",
  "target_channel_id": "target-channel-id",
  "mode": "live_sync",
  "status": "active",
  "settings": {}
}
```

Allowed `mode` values:

- `live_sync`
- `migration`

Allowed `status` values:

- `active`
- `paused`
- `error`

`GET /api/app/bridges/{bridge_id}`

Returns one bridge.

`PATCH /api/app/bridges/{bridge_id}`

Updates `status` and/or `settings`.

`DELETE /api/app/bridges/{bridge_id}`

Deletes one bridge and returns `204`.

`GET /api/app/bridges/live-sync-targets?source_channel_id=...`

Returns active `live_sync` targets for a source channel:

```json
{
  "items": [
    {
      "bridge_id": "bridge-id",
      "target_channel_id": "target-channel-id",
      "platform": "max",
      "external_id": "max-1",
      "bridge_settings": {}
    }
  ]
}
```

## Content Items

Content item endpoints are the self-host editor contract for Postbridge-authored posts.

`GET /api/app/content-items`

Optional filters:

- `status`
- `limit`
- `offset`

`POST /api/app/content-items`

Request:

```json
{
  "title": "Launch note",
  "content_md": "Post body",
  "content_plain": "Post body",
  "summary": "Short summary",
  "tags": ["release"],
  "status": "draft"
}
```

Allowed `status` values:

- `draft`
- `published`

Scheduling requires a Postbridge source channel and a future UTC time on the five-minute grid.

`GET /api/app/content-items/{content_id}`

Returns one content item.

`PATCH /api/app/content-items/{content_id}`

Updates content fields, publishing state, and scheduling fields.

`DELETE /api/app/content-items/{content_id}`

Deletes one content item and returns `204`.

## AI Editor

AI editor endpoints let the self-host frontend generate drafts, refine an existing draft, create channel-specific variants, and read editor chat history.

`POST /api/app/content-items/generate`

Creates a new draft when `content_item_id` is omitted, or refines the existing draft when `content_item_id` is present. This requires AI gateway configuration to be enabled on the Core instance.

Request:

```json
{
  "prompt": "Write a launch post",
  "messages": [
    {
      "role": "user",
      "content": "Make the current draft shorter"
    }
  ],
  "model": "text-model",
  "target_language": "en",
  "content_item_id": "content-id"
}
```

Response:

```json
{
  "operation": "generate",
  "content_item_id": "content-id",
  "publication_plan_id": null,
  "render_variant_ids": [],
  "publication_target_ids": [],
  "usage_tokens_charged": 1,
  "generated_title": "Generated",
  "generated_body_markdown": "Generated draft body"
}
```

`POST /api/app/content-items/{content_id}/adapt`

Creates an AI render variant for a target channel.

Request:

```json
{
  "channel_id": "target-channel-id",
  "target_language": "en",
  "model": "text-model"
}
```

`POST /api/app/content-items/{content_id}/translate`

Creates a translated AI render variant for a target channel.

Request:

```json
{
  "channel_id": "target-channel-id",
  "target_language": "de",
  "model": "text-model"
}
```

Adapt and translate responses:

```json
{
  "operation": "adapt",
  "content_item_id": "content-id",
  "channel_id": "target-channel-id",
  "render_variant_id": "render-variant-id",
  "previous_render_variant_id": null,
  "usage_tokens_charged": 1
}
```

`GET /api/app/content-items/{content_id}/ai-chat`

Returns persisted AI editor messages and events.

`DELETE /api/app/content-items/{content_id}/ai-chat`

Clears AI editor history for one content item and returns:

```json
{
  "deleted": 2
}
```

## Agent

Agent endpoints expose the self-host post copilot, topic scout, scheduled task controls, run history, candidates, policies, analytics, and editor timeline.

`GET /api/app/agent/tasks`

Returns configured agent tasks.

`POST /api/app/agent/tasks`

Creates an agent task.

Request:

```json
{
  "channel_id": "editorial-channel-id",
  "mode": "topic_scout",
  "goal_text": "Find timely product engineering topics",
  "editorial_instructions": "Prefer practical examples",
  "schedule_cron": "0 9 * * 1-5",
  "timezone": "UTC",
  "max_candidates_per_run": 5,
  "autonomy_mode": "draft_approval",
  "provider_config_id": null,
  "model_name": null,
  "content_item_id": null,
  "task_config": {},
  "search_image_mode": "none",
  "seed_urls": [],
  "require_source_approval": false,
  "created_by": "local-admin"
}
```

`POST /api/app/agent/tasks/{task_id}/pause`

Pauses one task.

`POST /api/app/agent/tasks/{task_id}/resume`

Resumes one task.

`DELETE /api/app/agent/tasks/{task_id}`

Archives one task.

`POST /api/app/agent/tasks/{task_id}/run`

Runs one task immediately.

`GET /api/app/agent/runs`

Returns recent agent runs.

`POST /api/app/agent/runs`

Runs the agent once.

Request:

```json
{
  "channel_id": "editorial-channel-id",
  "mode": "post_copilot",
  "user_request": "Improve this draft",
  "topic_definition": null,
  "content_item_id": "content-id",
  "max_candidates": 1,
  "autonomy_mode": "draft_approval",
  "image_request": false,
  "seed_urls": [],
  "approved_image_urls": [],
  "require_source_approval": false
}
```

`GET /api/app/agent/runs/{run_id}`

Returns one agent run.

`GET /api/app/agent/runs/{run_id}/steps`

Returns run steps.

`GET /api/app/agent/candidates`

Optional filter:

- `run_id`

`GET /api/app/agent/candidates/{candidate_id}`

Returns one content candidate.

`GET /api/app/agent/content-items/{content_item_id}/timeline`

Returns the editor timeline for one content item:

```json
{
  "content_item_id": "content-id",
  "content_item": {},
  "events": [],
  "latest_run": null,
  "session_status": "idle"
}
```

`POST /api/app/agent/content-items/{content_item_id}/messages`

Sends one post copilot message for an existing content item.

Request:

```json
{
  "channel_id": "editorial-channel-id",
  "user_request": "Make this draft clearer",
  "autonomy_mode": "draft_approval",
  "image_request": false,
  "seed_urls": [],
  "approved_image_urls": [],
  "require_source_approval": false
}
```

Response contains the created `run` and updated `timeline`.

`GET /api/app/agent/analytics/overview`

Returns aggregate agent analytics. Optional filter:

- `channel_id`

`GET /api/app/agent/analytics/timeseries`

Returns daily agent analytics. Optional filters:

- `channel_id`
- `days`

`GET /api/app/agent/analytics/quality`

Returns review quality analytics. Optional filters:

- `channel_id`
- `days`

`GET /api/app/agent/policies`

Returns agent policies. Optional filter:

- `channel_id`

`PUT /api/app/agent/policies`

Creates or updates a tenant-level or channel-level policy.

Request:

```json
{
  "channel_id": "editorial-channel-id",
  "policy": {
    "autonomy_mode": "draft_approval",
    "blocked_topics": []
  }
}
```

## Review Queue

Review queue endpoints expose agent proposals that need manual approval.

`GET /api/app/review-queue`

Optional filter:

- `status`

`GET /api/app/review-queue/{review_item_id}`

Returns one review item.

`POST /api/app/review-queue/{review_item_id}/resolve`

Request:

```json
{
  "decision": "approved",
  "review_action": "approve_as_is",
  "note": "Looks good",
  "reviewer_id": "local-admin",
  "approved_seed_urls": [],
  "approved_image_urls": []
}
```

## Publication Targets

Publication target endpoints let the self-host frontend publish an existing content item to selected channels and track per-channel status.

`GET /api/app/content-items/{content_id}/publication-targets`

Optional filter:

- `status`

`POST /api/app/content-items/{content_id}/publication-targets`

Request:

```json
{
  "channel_ids": ["target-channel-id"],
  "dispatch": false,
  "scheduled_at": null
}
```

Response:

```json
{
  "content_item_id": "content-id",
  "publication_plan_id": "plan-id",
  "render_variant_ids": ["render-variant-id"],
  "publication_target_ids": ["publication-target-id"],
  "dispatched_target_ids": []
}
```

`GET /api/app/publication-targets/{target_id}`

Returns one target status:

```json
{
  "id": "publication-target-id",
  "content_item_id": "content-id",
  "channel_id": "target-channel-id",
  "channel_title": "MAX Target",
  "platform": "max",
  "status": "pending",
  "scheduled_at": null,
  "published_at": null,
  "external_post_id": null,
  "external_url": null,
  "error_code": null,
  "error_message": null,
  "retry_count": 0
}
```

`POST /api/app/publication-targets/{target_id}/dispatch`

Enqueues one target for processing and returns:

```json
{
  "status": "enqueued",
  "target_id": "publication-target-id"
}
```

## Media

Media endpoints let the self-host frontend upload images and manage background image generation jobs.

`POST /api/app/media/upload`

Accepts multipart form data with a single `file` field. Only image content types are accepted.

Response:

```json
{
  "media_asset_id": "media-asset-id",
  "url": "https://example.test/media/asset.png"
}
```

`POST /api/app/media/generation-jobs`

Queues an AI image generation job. This requires AI gateway configuration to be enabled on the Core instance.

Request:

```json
{
  "target": "cover",
  "title": "Launch note",
  "summary": "Short summary",
  "content_md": "Post body",
  "prompt": "Optional user prompt",
  "style_prompt": "Optional style override",
  "model": "image-model",
  "content_item_id": "content-id"
}
```

Allowed `target` values:

- `cover`
- `media`

Response status is `202`:

```json
{
  "id": "job-id",
  "tenant_id": "00000000-0000-4000-8000-000000000001",
  "requester_user_id": "local-admin",
  "content_item_id": "content-id",
  "target": "cover",
  "status": "pending",
  "url": null,
  "media_asset_id": null,
  "prompt": null,
  "usage_tokens_charged": null,
  "error_code": null,
  "error_message": null
}
```

`GET /api/app/media/generation-jobs`

Returns recent media generation jobs.

`GET /api/app/media/generation-jobs/{job_id}`

Returns one media generation job.
