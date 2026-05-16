"""Абстракция AI-адаптера платформы (контракт к шлюзу без ветвления в ai_content)."""

from __future__ import annotations

from typing import Any, Protocol

from postbridge.ai.schemas import GatewayAdaptRequest, GatewayTextResponse


class PlatformAiAdapter(Protocol):
    def build_adapt_request(
        self,
        *,
        source_text: str,
        title: str | None,
        platform: str,
        target_language: str | None,
        capabilities_hint: dict[str, Any],
    ) -> GatewayAdaptRequest: ...

    def post_process_adapt_response(self, response: GatewayTextResponse) -> GatewayTextResponse:
        """Детерминированная постобработка ответа шлюза под платформу (по умолчанию — тождество)."""
        ...


class DefaultPlatformAiAdapter:
    """Передаёт platform и capabilities_hint в шлюз без дополнительной логики."""

    def build_adapt_request(
        self,
        *,
        source_text: str,
        title: str | None,
        platform: str,
        target_language: str | None,
        capabilities_hint: dict[str, Any],
    ) -> GatewayAdaptRequest:
        return GatewayAdaptRequest(
            source_text=source_text,
            title=title,
            platform=platform,
            target_language=target_language,
            capabilities_hint=capabilities_hint,
        )

    def post_process_adapt_response(self, response: GatewayTextResponse) -> GatewayTextResponse:
        return response
