# Updating Self-Host

Postbridge self-host releases are distributed as GHCR images. Production installs should pin a release tag instead of building from `main`.

## Check for Updates

The self-host settings screen shows:

- the currently running version;
- the latest public GitHub release;
- release notes;
- a copyable update command when a newer version is available.

The check is read-only. Postbridge does not auto-update itself and does not require Docker socket access.

## Update

Run the command shown in Settings from the `postbridge-core` directory. It has this shape:

```bash
POSTBRIDGE_IMAGE=ghcr.io/msrv-tech/postbridge-core:v0.1.1 docker compose -f deploy/docker-compose.release.yml --env-file .env pull
POSTBRIDGE_IMAGE=ghcr.io/msrv-tech/postbridge-core:v0.1.1 docker compose -f deploy/docker-compose.release.yml --env-file .env up -d
```

The `core-migrate` service runs `alembic upgrade head` before the API and worker start.

## Back Up First

Create a database backup before updating:

```bash
docker compose -f deploy/docker-compose.release.yml --env-file .env exec postgres \
  pg_dump -U postbridge postbridge > postbridge-backup.sql
```

If your install still uses the source-build compose file, replace `deploy/docker-compose.release.yml` with `deploy/docker-compose.self-host.yml`.

## Roll Back

To roll back, run the same commands with the previous image tag:

```bash
POSTBRIDGE_IMAGE=ghcr.io/msrv-tech/postbridge-core:v0.1.0 docker compose -f deploy/docker-compose.release.yml --env-file .env pull
POSTBRIDGE_IMAGE=ghcr.io/msrv-tech/postbridge-core:v0.1.0 docker compose -f deploy/docker-compose.release.yml --env-file .env up -d
```

Database migrations may not always be reversible. Check the release notes before downgrading across major versions.
