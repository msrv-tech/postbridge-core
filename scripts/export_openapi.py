#!/usr/bin/env python3
"""Save the public Core OpenAPI schema as JSON for CI or local checks."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _bootstrap_env() -> None:
    if not os.environ.get("DATABASE_URL", "").strip():
        raise SystemExit(
            "DATABASE_URL must be set (for example, via docker compose -f ci/docker-compose.yml run --rm test)"
        )
    os.environ.setdefault("APP_ENV", "test")
    os.environ.setdefault("REDIS_URL", "redis://redis:6379/0")
    if not os.environ.get("CREDENTIALS_ENCRYPTION_KEY", "").strip():
        from cryptography.fernet import Fernet

        os.environ["CREDENTIALS_ENCRYPTION_KEY"] = Fernet.generate_key().decode()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "openapi-core.json"
    _bootstrap_env()
    from postbridge.api.main import app

    schema = app.openapi()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
