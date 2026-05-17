# Security Policy

## Supported Versions

Security fixes are provided for the latest released version of Postbridge Core and the current `main` branch.

## Reporting Vulnerabilities

Please do not open public issues for suspected vulnerabilities.

Use GitHub private vulnerability reporting when available:

```text
https://github.com/msrv-tech/postbridge-core/security/advisories/new
```

If that is not available, contact the maintainer privately and include:

- affected version or commit;
- steps to reproduce;
- impact and affected components;
- any logs or proof-of-concept details that can be shared safely.

The maintainer should acknowledge the report, triage severity, and coordinate a fix before public disclosure.

## Response Goals

- Acknowledge new reports within 7 days.
- Confirm severity and affected versions as soon as the issue is reproducible.
- Publish a fix and release notes before public disclosure whenever practical.

## Secrets

Never commit `.env`, database dumps, credentials, tokens, generated sessions, or local deployment artifacts.

Before making the repository public or publishing a release, run:

```bash
git grep -I -n -P '(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16})' -- .
```

Use a dedicated secret scanner as an additional check when available.

## Browser Boundary

The browser-facing API must not expose server-to-server credentials, database URLs, platform tokens, or encrypted credential payloads.

`CORE_SERVICE_TOKEN`, `SYNC_PUBLISH_TOKEN`, platform access tokens, and database credentials are server-side only.
