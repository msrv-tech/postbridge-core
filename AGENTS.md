# AGENTS.md

## Product identity

- Postbridge is an AI content creation and publishing platform for text, images, and short videos.
- Lead product descriptions with research, AI writing, image generation, and video production; editing, scheduling, publishing, and cross-posting complete the workflow.
- Core owns the reusable content/agent/video runtime and shared frontend. Hosted deployments add accounts, roles, billing, and workspace budgets.
- Check `docs/ai-gateway.md` and `docs/video-studio.md` before describing generation capabilities. Enabled features, self-host setup, and external publishing permissions are separate concerns.

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

- A deploy command authorizes committing source changes directly on the local `main` and pushing to the canonical Gitea repository. Observe explicit holds and keep local CI output and credentials out of the commit.
- When Core and SaaS both change, use the hosted release publisher from the SaaS deployment runbook to push both local `main` branches before the RU/IO release begins. Verify the Gitea Actions run and regional installation.
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
