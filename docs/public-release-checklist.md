# Public Release Checklist

Use this checklist before changing repository visibility or publishing a public mirror.

## Required

- Confirm the intended open-source license and add `LICENSE`.
- Publish from the single-root `public-baseline` branch or another clean branch with no old private history.
- Run the full Docker CI test suite.
- Run a secret scan against the exact branch that will be published.
- Confirm no local artifacts are tracked:
  - `.env`
  - `.venv/`
  - `.vscode/`
  - `web/node_modules/`
  - `web/dist/`
  - local databases and dumps
- Confirm public docs do not link to private repositories, internal infrastructure, or private planning documents.
- Confirm production databases are not migrated with the greenfield squashed baseline unless they are intentionally stamped after schema verification.

## Recommended

- Create a new public repository or force-push the clean branch to the public default branch.
- Protect the default branch after the first clean publish.
- Rotate any secrets that may have existed in old private history before public exposure.
- Keep the private hosted layer as a separate repository or private deployment overlay.
- Re-run CI after the first public push.

## Current Baseline

The clean public branch is expected to contain one root commit:

```bash
git log --oneline --max-count=3 public-baseline
```

The tree should match the reviewed `main` tree:

```bash
git rev-parse main^{tree} public-baseline^{tree}
```
