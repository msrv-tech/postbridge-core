"""AI Gateway: клиенты и схемы (фаза 5)."""

from postbridge.ai.client import (
    AiGatewayClient,
    EchoAiGatewayClient,
    HttpAiGatewayClient,
    gateway_response_to_warnings_json,
)
from postbridge.ai.factory import get_ai_gateway_client
from postbridge.ai.schemas import (
    GatewayAdaptRequest,
    GatewayGenerateRequest,
    GatewayTextResponse,
    GatewayTranslateRequest,
)

__all__ = [
    "AiGatewayClient",
    "EchoAiGatewayClient",
    "GatewayAdaptRequest",
    "GatewayGenerateRequest",
    "GatewayTextResponse",
    "GatewayTranslateRequest",
    "HttpAiGatewayClient",
    "gateway_response_to_warnings_json",
    "get_ai_gateway_client",
]
