"""Фабрика клиента AI Gateway из настроек."""

from __future__ import annotations

from postbridge.ai.client import AiGatewayClient, EchoAiGatewayClient, HttpAiGatewayClient
from postbridge.config import Settings, get_settings
from postbridge.domain.errors import ConfigurationError, ValidationError


def get_ai_gateway_client(settings: Settings | None = None) -> AiGatewayClient:
    """
    Возвращает HTTP-клиент к шлюзу или Echo в тестах без URL.

    При AI_GATEWAY_ENABLED=0 вызывающий код должен не звать фабрику для продуктивных путей;
    либо проверять enabled до вызова.
    """
    s = settings or get_settings()
    if not s.ai_gateway_enabled:
        raise ValidationError(
            code="VALIDATION_AI_GATEWAY_DISABLED",
            message="AI gateway is disabled (set AI_GATEWAY_ENABLED=1)",
            details={},
        )
    if s.ai_gateway_base_url:
        return HttpAiGatewayClient(
            base_url=s.ai_gateway_base_url,
            api_key=s.ai_gateway_api_key,
            timeout_seconds=float(s.ai_gateway_timeout_seconds),
            default_model=s.ai_gateway_default_model,
        )
    if s.app_env == "test":
        return EchoAiGatewayClient()
    raise ConfigurationError(
        "AI_GATEWAY_ENABLED but AI_GATEWAY_BASE_URL is not set (required outside APP_ENV=test).",
        details={},
    )
