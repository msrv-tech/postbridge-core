"""Классификация ошибок публикации для метрик (фаза 7)."""

from __future__ import annotations

from postbridge.domain.errors import ExternalApiError, PostbridgeError, ValidationError


def classify_publication_failure(exc: PostbridgeError) -> str:
    """Возвращает метку для счётчика postbridge_publication_failures_*_total."""
    c = exc.code
    if isinstance(exc, ValidationError) or c.startswith("VALIDATION_"):
        return "validation"
    if "RATE" in c or "TOO_MANY" in c or "429" in c:
        return "rate_limit"
    if (
        "AUTH" in c
        or "CREDENTIAL" in c
        or c.startswith("AUTH_")
        or "TOKEN" in c
        or "FORBIDDEN" in c
    ):
        return "auth"
    if "TIMEOUT" in c or "NETWORK" in c or "CONNECTION" in c or "DNS" in c:
        return "network"
    if isinstance(exc, ExternalApiError):
        return "external_api"
    return "other"
