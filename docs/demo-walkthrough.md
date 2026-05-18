# Demo Walkthrough

This walkthrough helps new users verify that a self-host Postbridge Core stack is running and understand the main product loop.

It avoids external platform credentials so it can be used on a fresh local machine.

## 1. Start the Stack

Follow [quickstart.md](quickstart.md):

```bash
cp .env.example .env
docker compose -f deploy/docker-compose.self-host.yml --env-file .env up -d --build
```

Before starting the stack, fill the required values listed in [quickstart.md](quickstart.md), including `POSTGRES_PASSWORD`, `DATABASE_URL`, and `CREDENTIALS_ENCRYPTION_KEY`.

Open:

```text
http://127.0.0.1:8000/web
```

## 2. Bootstrap the Workspace

The self-host UI bootstraps the local workspace when needed. You can also call the browser-safe API directly:

```bash
curl -fsS -X POST http://127.0.0.1:8000/api/app/bootstrap \
  -H 'content-type: application/json' \
  -d '{"tenant_name":"Postbridge Local Demo"}'
```

Check the session:

```bash
curl -fsS http://127.0.0.1:8000/api/app/session
```

## 3. Create a Local Source

In the UI, open **Channels** and create a built-in source:

| Channel | Platform | Kind | Notes |
| --- | --- | --- | --- |
| Local Source | `postbridge` | source | Built-in content source. |

For a real external target, configure platform credentials first. See [platforms.md](platforms.md).

## 4. Create Content

Open **Content** and create a draft post.

Recommended demo text:

```markdown
# Hello from Postbridge Core

This is a local self-host demo post.
```

Save it as a draft first. Then publish or create publication targets from the editor when the target channel is ready.

## 5. Try a Bridge

Open **Channels**, start **New bridge**, choose the local Postbridge source, and choose **RSS feed** as the target.

Postbridge is a source only; it is not a bridge target. The RSS target is created automatically and stores generated feed items inside Core.

## 6. Verify Background Services

Check API health:

```bash
curl -fsS http://127.0.0.1:8000/health
```

Inspect worker logs:

```bash
docker compose -f deploy/docker-compose.self-host.yml --env-file .env logs --tail=100 core-worker
```

## 7. Stop the Demo

```bash
docker compose -f deploy/docker-compose.self-host.yml --env-file .env down
```

Remove local data too:

```bash
docker compose -f deploy/docker-compose.self-host.yml --env-file .env down -v
```

## Next Steps

- Add Telegram, MAX, VK, RSS, or LinkedIn credentials.
- Read [configuration.md](configuration.md) for environment variables.
- Read [architecture.md](architecture.md) for API and worker boundaries.
- Open a deployment help issue if the stack behaves differently on your host.
