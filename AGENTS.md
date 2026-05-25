# AGENTS.md

## Repo boundaries

- `postbridge-core` is the public, world-facing repository.
- Hosted deployments may add a private product layer outside this repository.
- Prefer English for product-facing text, docs, tests, and code. Russian may appear only where already established in comments or user-provided fixtures.

## Local dev mode

- In this workspace, the default browser dev target is the hosted/SaaS flow, not self-host.
- Self-host UI checks are handled in the separate Proxmox container 115 (`postbridge-demo-check`).
- When running the Vite web dev server for this repo, do not force `VITE_POSTBRIDGE_APP_MODE=selfhost` unless explicitly working on the self-host container flow.

## Frontend UI behavior

- Do not render disabled configuration fields for features that are turned off by a user-facing toggle. Show the toggle and reveal the related inputs only after the toggle is enabled.

## Push workflow

- Treat a user request to push as a one-time authorization for that specific push only. Do not push again later unless the user explicitly asks for another push.
- GitHub Actions are configured to run on push. After every push, check the relevant GitHub Actions logs.
- A push is not complete until the required checks and deployment have succeeded.
- If a check or deployment fails after a push, inspect the logs, fix the failure, commit the fix, and push again as part of the same push request. Repeat until the deployment is green or there is a clear blocker that needs the user's decision.

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
