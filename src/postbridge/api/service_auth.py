"""Авторизация internal service API (SaaS BFF): Bearer + X-Tenant-Id."""

from fastapi import Request

from postbridge.config import get_settings
from postbridge.domain.errors import ValidationError

TENANT_ID_HEADER = "X-Tenant-Id"


def require_service_tenant(request: Request) -> str:
    """
    Проверяет Authorization: Bearer CORE_SERVICE_TOKEN (если токен задан в env)
    и возвращает tenant_id из X-Tenant-Id.
    """
    settings = get_settings()
    expected = settings.core_service_token
    if expected:
        auth = (request.headers.get("Authorization") or "").strip()
        if auth != f"Bearer {expected}":
            raise ValidationError(
                code="AUTH_UNAUTHORIZED",
                message="invalid or missing core service token",
                message_key="error.auth.invalid_or_missing_core_service_token",
                details={},
            )
    tenant_id = (request.headers.get(TENANT_ID_HEADER) or "").strip()
    if len(tenant_id) != 36:
        raise ValidationError(
            code="VALIDATION_TENANT_ID_REQUIRED",
            message="X-Tenant-Id must be a 36-char tenant id",
            message_key="error.validation.tenant_id_required",
            details={},
        )
    return tenant_id
