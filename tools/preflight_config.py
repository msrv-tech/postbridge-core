from __future__ import annotations

import os
import sys

from postbridge.config import get_settings, validate_base_settings
from postbridge.domain.errors import ConfigurationError


def _require(name: str, value: str | None) -> None:
    if value is None or not value.strip():
        raise ConfigurationError(f"{name} must be set for non-dev rollout.")


def main() -> int:
    settings = get_settings()
    validate_base_settings(settings)
    app_env = settings.app_env.lower()
    if app_env not in {"dev", "test"}:
        _require("STATUS_EVENT_WEBHOOK_URL", settings.status_event_webhook_url)
        _require("STATUS_EVENT_WEBHOOK_TOKEN", settings.status_event_webhook_token)
        _require("MAX_API_BASE_URL", settings.max_api_base_url)
        _require("MAX_API_TOKEN", settings.max_api_token)
        _require("TELEGRAM_API_ID", settings.telegram_api_id)
        _require("TELEGRAM_API_HASH", settings.telegram_api_hash)
    print(
        "core config preflight OK",
        f"app_env={settings.app_env}",
        f"database_url={settings.database_url}",
        sep=" | ",
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConfigurationError as exc:
        print(f"core config preflight FAILED: {exc.message}", file=sys.stderr)
        raise SystemExit(1) from exc
