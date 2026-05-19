#!/usr/bin/env bash
# Deploy postbridge-core with Docker Compose.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_DIR="${POSTBRIDGE_DEPLOY_COMPOSE_DIR:-$ROOT_DIR}"
COMPOSE_FILE="${POSTBRIDGE_DEPLOY_COMPOSE_FILE:-docker-compose.yml}"
ENV_FILE="${POSTBRIDGE_DEPLOY_ENV_FILE:-$COMPOSE_DIR/.env}"

cd "$COMPOSE_DIR"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "ERROR: compose file not found: $COMPOSE_DIR/$COMPOSE_FILE" >&2
  exit 1
fi

echo "Deploy compose: $COMPOSE_DIR/$COMPOSE_FILE"
echo "Deploy env file: $ENV_FILE"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)" > "$ENV_FILE"
fi
if ! grep -q "POSTGRES_PASSWORD=[^[:space:]]" "$ENV_FILE" 2>/dev/null; then
  if grep -q "^POSTGRES_PASSWORD=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 16)/" "$ENV_FILE"
  else
    echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)" >> "$ENV_FILE"
  fi
fi

compose() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

services="$(compose config --services)"
has_service() {
  printf '%s\n' "$services" | grep -qx "$1"
}

postgres_service=""
for candidate in postgres db; do
  if has_service "$candidate"; then
    postgres_service="$candidate"
    break
  fi
done

redis_service=""
if has_service redis; then
  redis_service="redis"
fi

api_service=""
for candidate in core-api api; do
  if has_service "$candidate"; then
    api_service="$candidate"
    break
  fi
done

worker_service=""
for candidate in core-worker worker; do
  if has_service "$candidate"; then
    worker_service="$candidate"
    break
  fi
done

if [[ -z "$postgres_service" || -z "$api_service" ]]; then
  echo "ERROR: compose stack must define postgres/db and core-api/api services." >&2
  exit 1
fi

echo "Starting dependencies..."
deps=("$postgres_service")
if [[ -n "$redis_service" ]]; then
  deps+=("$redis_service")
fi
compose up -d "${deps[@]}"

echo "Waiting for Postgres..."
ready=0
for _ in $(seq 1 120); do
  if compose exec -T "$postgres_service" pg_isready -U postbridge -d postbridge; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "$ready" != 1 ]]; then
  echo "ERROR: Postgres did not become ready within 4 minutes." >&2
  compose logs "$postgres_service" --tail 80 >&2 || true
  exit 1
fi

build_services=("$api_service")
if [[ -n "$worker_service" ]]; then
  build_services+=("$worker_service")
fi

echo "Building Core images..."
compose build "${build_services[@]}"

echo "Running migrations..."
compose run --rm --no-deps "$api_service" alembic -c /app/alembic.ini upgrade head

echo "Starting Core services..."
compose up -d --no-build "${build_services[@]}"

if has_service telegram-bot && compose ps --services --filter status=running | grep -qx telegram-bot; then
  echo "Restarting telegram bot..."
  compose up -d --no-build telegram-bot
fi

echo "Core deploy OK"
