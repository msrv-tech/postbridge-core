# AGENTS.md

## Repo boundaries

- `postbridge-core` is the public, world-facing repository.
- Hosted deployments may add a private product layer outside this repository.
- Prefer English for product-facing text, docs, tests, and code. Russian may appear only where already established in comments or user-provided fixtures.

## Running tests

- Do not assume host Python is the right way to run tests here.
- The canonical test environment for `postbridge-core` is Docker Compose CI:
  - Full run:
    - `docker compose --progress=plain -f ci/docker-compose.yml build`
    - `docker compose -f ci/docker-compose.yml run --rm test`
  - Targeted run:
    - `docker compose --progress=plain -f ci/docker-compose.yml build test`
    - `docker compose -f ci/docker-compose.yml run --rm test pytest -q tests/test_agent_api.py -k '...expr...'`
- The `test` service builds a fresh image from `ci/Dockerfile` and copies the repo into the image. After edits in `src/` or `tests/`, rebuild before running tests.
- If `pytest` is missing on the host, that is expected and not a blocker; use the Docker-based flow above.
