"""Desktop runtime entrypoint wrappers.

The Tauri supervisor packages these commands as platform binaries. Keeping the
commands here avoids a second backend control surface for Desktop.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _default_alembic_ini() -> str:
    configured = os.getenv("POSTBRIDGE_ALEMBIC_INI")
    if configured:
        return configured

    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        bundled_candidate = Path(bundled_root) / "alembic.ini"
        if bundled_candidate.exists():
            return str(bundled_candidate)

    cwd_candidate = Path.cwd() / "alembic.ini"
    if cwd_candidate.exists():
        return str(cwd_candidate)

    package_root = Path(__file__).resolve().parents[2]
    repo_candidate = package_root / "alembic.ini"
    if repo_candidate.exists():
        return str(repo_candidate)

    return "alembic.ini"


def _default_alembic_script_location() -> str | None:
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        bundled_candidate = Path(bundled_root) / "alembic"
        if bundled_candidate.exists():
            return str(bundled_candidate)

    cwd_candidate = Path.cwd() / "alembic"
    if cwd_candidate.exists():
        return str(cwd_candidate)

    package_root = Path(__file__).resolve().parents[2]
    repo_candidate = package_root / "alembic"
    if repo_candidate.exists():
        return str(repo_candidate)

    return None


def _argv_with_inferred_command(argv: list[str] | None = None) -> list[str] | None:
    if argv is not None:
        return argv

    executable = Path(sys.argv[0]).stem.lower()
    command_by_suffix = {
        "postbridge-api": "api",
        "postbridge-worker": "worker",
        "postbridge-migrate": "migrate",
    }
    command = command_by_suffix.get(executable)
    if command:
        if len(sys.argv) > 1 and sys.argv[1] in {"api", "worker", "migrate"}:
            return None
        return [command, *sys.argv[1:]]
    return None


def run_api(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "postbridge.api.main:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
    return 0


def run_worker(args: argparse.Namespace) -> int:
    from postbridge.workers.celery_app import celery_app

    worker_args = ["worker", "-l", args.log_level]
    if args.beat:
        worker_args.append("-B")
    celery_app.worker_main(worker_args)
    return 0


def run_migrate(args: argparse.Namespace) -> int:
    from alembic import command
    from alembic.config import Config

    config = Config(args.config)
    script_location = _default_alembic_script_location()
    if script_location:
        config.set_main_option("script_location", script_location)
    command.upgrade(config, args.revision)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="postbridge-desktop-runtime")
    subparsers = parser.add_subparsers(dest="command", required=True)

    api = subparsers.add_parser("api", help="Run the Core FastAPI server")
    api.add_argument("--host", default=os.getenv("POSTBRIDGE_API_HOST", "127.0.0.1"))
    api.add_argument("--port", type=int, default=int(os.getenv("POSTBRIDGE_API_PORT", "8820")))
    api.add_argument("--log-level", default=os.getenv("POSTBRIDGE_LOG_LEVEL", "info"))
    api.set_defaults(func=run_api)

    worker = subparsers.add_parser("worker", help="Run the Core worker")
    worker.add_argument("--log-level", default=os.getenv("POSTBRIDGE_LOG_LEVEL", "info"))
    worker.add_argument("--beat", action=argparse.BooleanOptionalAction, default=True)
    worker.set_defaults(func=run_worker)

    migrate = subparsers.add_parser("migrate", help="Run database migrations")
    migrate.add_argument("--config", default=_default_alembic_ini())
    migrate.add_argument("--revision", default="head")
    migrate.set_defaults(func=run_migrate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(_argv_with_inferred_command(argv))
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
