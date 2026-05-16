"""Общая проверка токена для internal sync/publication эндпоинтов."""

from fastapi import Request

from postbridge.config import get_settings
from postbridge.domain.errors import ValidationError

PUBLISH_TOKEN_HEADER = "X-Sync-Publish-Token"


def check_sync_publish_auth(request: Request) -> None:
    """Проверяет X-Sync-Publish-Token при заданном SYNC_PUBLISH_TOKEN."""
    expected = (get_settings().sync_publish_token or "").strip()
    if expected:
        token = request.headers.get(PUBLISH_TOKEN_HEADER)
        if token != expected:
            raise ValidationError(
                code="AUTH_UNAUTHORIZED",
                message="invalid sync publish token",
                message_key="error.auth.invalid_sync_publish_token",
                details={},
            )
