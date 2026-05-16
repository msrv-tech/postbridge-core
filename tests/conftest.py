"""Общие настройки окружения для pytest (до импорта postbridge в тестовых модулях)."""

from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet

if not os.environ.get("DATABASE_URL", "").strip():
    raise RuntimeError(
        "DATABASE_URL must be set. Run: docker compose -f ci/docker-compose.yml run --rm test"
    )

if not os.environ.get("CREDENTIALS_ENCRYPTION_KEY", "").strip():
    os.environ["CREDENTIALS_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

os.environ.setdefault("REDIS_URL", "redis://redis:6379/0")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")
os.environ["APP_ENV"] = "test"
os.environ.setdefault("MAX_API_BASE_URL", "")
os.environ.setdefault("MAX_API_TOKEN", "")
# Предсказуемый токен для internal API; не использовать setdefault — иначе ломает прогон при CORE_SERVICE_TOKEN в shell.
os.environ["CORE_SERVICE_TOKEN"] = "svc-test-secret"


@pytest.fixture(autouse=True)
def _dispose_sqlalchemy_engine_around_test():
    """Сброс engine вокруг теста (NullPool + dispose; закрывайте Session в тестах)."""
    from postbridge.db import ENGINE

    ENGINE.dispose()
    yield
    ENGINE.dispose()
