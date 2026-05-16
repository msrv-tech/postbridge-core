from dataclasses import dataclass, field
from typing import Any


ErrorSource = str


@dataclass(slots=True)
class PostbridgeError(Exception):
    """Базовое доменное исключение с единым форматом полей."""

    code: str
    message: str
    message_key: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    source: ErrorSource = "core"
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, correlation_id: str) -> dict[str, Any]:
        """Преобразует ошибку в словарь для API-ответа."""
        from postbridge.i18n import get_i18n

        i18n = get_i18n()
        localized = (
            i18n.translate(
                self.message_key,
                params=self.params,
                default=self.message,
            )
            if self.message_key
            else self.message
        )
        return {
            "code": self.code,
            "message": localized,
            "message_key": self.message_key,
            "params": self.params,
            "details": self.details,
            "source": self.source,
            "retryable": self.retryable,
            "correlation_id": correlation_id,
        }


class ConfigurationError(PostbridgeError):
    """Выбрасывается при отсутствии обязательной конфигурации."""

    def __init__(
        self,
        message: str,
        *,
        message_key: str | None = None,
        params: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            code="INTERNAL_CONFIGURATION_ERROR",
            message=message,
            message_key=message_key,
            params=params or {},
            source="core",
            retryable=False,
            details=details or {},
        )


class ValidationError(PostbridgeError):
    """Выбрасывается при ошибке валидации запроса или сущности."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        message_key: str | None = None,
        params: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            code=code,
            message=message,
            message_key=message_key,
            params=params or {},
            source="core",
            retryable=False,
            details=details or {},
        )


class ExternalApiError(PostbridgeError):
    """Выбрасывается при ошибке вызова Telegram/MAX API."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        message_key: str | None = None,
        params: dict[str, Any] | None = None,
        source: ErrorSource,
        retryable: bool,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            code=code,
            message=message,
            message_key=message_key,
            params=params or {},
            source=source,
            retryable=retryable,
            details=details or {},
        )


class InternalError(PostbridgeError):
    """Выбрасывается при неожиданных внутренних сбоях."""

    def __init__(
        self,
        message: str,
        *,
        message_key: str | None = None,
        params: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            code="INTERNAL_UNEXPECTED_ERROR",
            message=message,
            message_key=message_key,
            params=params or {},
            source="core",
            retryable=False,
            details=details or {},
        )
