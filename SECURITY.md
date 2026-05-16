# Security Policy

## Reporting Vulnerabilities

Please do not open public issues for suspected vulnerabilities.

Send a private report to the project maintainer with:

- affected version or commit;
- steps to reproduce;
- impact and affected components;
- any logs or proof-of-concept details that can be shared safely.

The maintainer should acknowledge the report, triage severity, and coordinate a fix before public disclosure.

## Secrets

Never commit `.env`, database dumps, credentials, tokens, generated sessions, or local deployment artifacts.

Before making the repository public or publishing a release, run:

```bash
git grep -I -n -P '(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16})' -- .
```

Use a dedicated secret scanner as an additional check when available.
