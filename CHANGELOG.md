# Changelog

All notable changes to Postbridge Core will be documented in this file.

This project follows a practical release-note format based on Keep a Changelog.
Version numbers should use semantic versioning once public releases begin.

## Unreleased

No unreleased changes yet.

## 0.1.2 - 2026-05-19

### Added

- Public baseline for the open-source Core repository.
- Shared frontend runtime modes for self-host and hosted deployments.
- Docker Compose based CI test environment.
- Security policy, contribution guide, and public release checklist.
- Self-host first-run setup flow with local administrator creation.
- Self-host update checks against public GitHub Releases and GHCR update commands.

### Changed

- Alembic history starts from a squashed public baseline migration.
- Production Core deploy now runs through the SaaS production compose boundary.
- CI avoids duplicate Dependabot pull request runs.
- Frontend dependencies, Python dependencies, Docker base images, and GitHub Actions were updated.

### Fixed

- Legacy Alembic bridge revision support for production databases created before the public baseline.
- Vite 8 manual chunking compatibility for the frontend build.
