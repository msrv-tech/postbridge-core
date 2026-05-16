# OpenAPI

FastAPI generates the OpenAPI schema at runtime.

## Local Export

With the API running:

```bash
curl -sS http://127.0.0.1:8000/openapi.json -o openapi-core.json
```

Many internal routes are marked with `include_in_schema=False`; they are intentionally excluded from the public schema.

## CI

The `.github/workflows/ci.yml` workflow runs `ci/docker-compose.yml`. The test container runs `pytest`, exports `ci/out/openapi-core.json`, and uploads it as the `openapi-core` artifact.

Local CI-compatible run:

```bash
docker compose -f ci/docker-compose.yml run --rm test
```
