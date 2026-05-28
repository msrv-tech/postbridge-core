# Desktop Runtime Contract

This directory describes the platform runtime layout used by Postbridge Desktop.

Large binaries are not committed to git. Release builds assemble platform-specific archives for:

- PostgreSQL with pgvector
- Redis-compatible queue
- Core API runtime
- Core worker runtime
- web UI build

The Tauri shell supervises these components through a stable contract:

1. resolve data directory;
2. resolve local ports;
3. initialize PostgreSQL if needed;
4. start PostgreSQL;
5. start queue;
6. run migrations;
7. start Core API;
8. start worker and scheduler;
9. open the existing Postbridge web UI.

Runtime manifests live in `runtime/manifests/` and intentionally describe the target layout before the actual packaging scripts are wired up.
