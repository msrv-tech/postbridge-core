# Postbridge Desktop

Postbridge Desktop is the native shell for the full local self-host edition of Postbridge.

This scaffold starts with a native runtime supervisor. The product UI and backend logic remain in the existing `web/` and `src/postbridge/` code. Desktop-specific code should stay focused on:

- local runtime startup and shutdown;
- PostgreSQL + pgvector management;
- Redis-compatible queue management;
- API, worker, and scheduler supervision;
- logs and diagnostics;
- backup, restore, and updates.

## Development

Requirements:

- Node.js 20.18+ for the desktop web shell;
- Rust and Cargo for the native Tauri shell;
- system Tauri dependencies for the target OS.

Install dependencies:

```bash
npm --prefix desktop install
```

Run the Tauri shell:

```bash
npm --prefix desktop run dev
```

Build the frontend assets used by Tauri:

```bash
npm --prefix desktop run check
```

Build the native app:

```bash
npm --prefix desktop run build
```

## Runtime Status

Current milestone:

- Tauri app scaffold;
- runtime command contract;
- process supervisor for packaged runtime binaries;
- platform runtime manifests;
- data directory initialization;
- runtime manifests for Windows and Linux.

Next milestone:

- package Core API, worker, migrations, PostgreSQL + pgvector, and a Redis-compatible queue as release artifacts;
- add first-run runtime initialization for PostgreSQL databases and secrets;
- open the bundled web UI after the runtime health check passes.
