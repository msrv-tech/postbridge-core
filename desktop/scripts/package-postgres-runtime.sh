#!/usr/bin/env bash
set -euo pipefail

cat <<'MSG'
PostgreSQL runtime packaging is not implemented yet.

The Desktop release must bundle platform-specific PostgreSQL with pgvector:
  windows-x64
  linux-x64

The packaged runtime must support initdb, local-only startup, pgvector
extension validation, backup, restore, and safe shutdown.
MSG
