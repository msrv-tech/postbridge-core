"""Pydantic-схемы тела запросов/ответов AI Gateway (docs/ai-gateway.md)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GatewayAdaptRequest(BaseModel):
    source_text: str = Field(min_length=1, max_length=100_000)
    title: str | None = Field(default=None, max_length=512)
    platform: str = Field(min_length=1, max_length=32)
    target_language: str | None = Field(default=None, max_length=16)
    capabilities_hint: dict[str, Any] = Field(default_factory=dict)
    model: str | None = Field(default=None, max_length=128)


class GatewayTranslateRequest(BaseModel):
    source_text: str = Field(min_length=1, max_length=100_000)
    title: str | None = Field(default=None, max_length=512)
    target_language: str = Field(min_length=1, max_length=16)
    model: str | None = Field(default=None, max_length=128)


class GatewayChatMessage(BaseModel):
    role: str = Field(min_length=1, max_length=32)
    content: str = Field(min_length=1, max_length=100_000)


class GatewayGenerateRequest(BaseModel):
    prompt: str | None = Field(default=None, max_length=50_000)
    messages: list[GatewayChatMessage] | None = None
    target_language: str | None = Field(default=None, max_length=16)
    model: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def prompt_or_messages(self) -> GatewayGenerateRequest:
        if self.messages:
            return self
        if self.prompt is not None and str(self.prompt).strip():
            return self
        raise ValueError("Either prompt or non-empty messages is required")

    def gateway_payload(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.prompt is not None and str(self.prompt).strip():
            d["prompt"] = self.prompt.strip()
        if self.messages:
            d["messages"] = [m.model_dump() for m in self.messages]
        if self.target_language is not None:
            d["target_language"] = self.target_language
        if self.model is not None:
            d["model"] = self.model
        return d


class GatewayUsageStats(BaseModel):
    """Подмножество OpenAI usage (Chat Completions)."""

    model_config = ConfigDict(extra="ignore")

    total_tokens: int | None = Field(default=None, ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class GatewayTextResponse(BaseModel):
    title: str | None = Field(default=None, max_length=512)
    body_text: str | None = Field(default=None, max_length=100_000)
    body_json: str | None = None
    hashtags: str | None = None
    mentions: str | None = None
    link_url: str | None = Field(default=None, max_length=1024)
    warnings: list[Any] | None = None
    usage: GatewayUsageStats | None = None
    total_tokens: int | None = Field(
        default=None,
        ge=0,
        description="Плоское total_tokens на корне (OpenAI-стиль), если нет usage.total_tokens",
    )
    source_assistant_text: str | None = Field(
        default=None,
        max_length=100_000,
        description="Сырой текст ассистента до разбора JSON (чат/логи).",
    )

    @field_validator("body_text", mode="before")
    @classmethod
    def _normalize_body_text(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            t = v.strip()
            return t if t else None
        return v

    @field_validator("usage", mode="before")
    @classmethod
    def _coerce_usage_dict(cls, v: Any) -> Any:
        if v is None or isinstance(v, GatewayUsageStats):
            return v
        if isinstance(v, dict):
            return GatewayUsageStats.model_validate(v)
        return v


def gateway_raw_total_tokens(resp: GatewayTextResponse) -> int | None:
    """Сырое число из ответа шлюза без эвристики минимума 1."""
    if resp.usage is not None and resp.usage.total_tokens is not None:
        return max(0, int(resp.usage.total_tokens))
    if resp.total_tokens is not None:
        return max(0, int(resp.total_tokens))
    return None


def usage_tokens_charged_for_billing(resp: GatewayTextResponse) -> int:
    """Единица списания для internal API: reported total или минимум 1."""
    raw = gateway_raw_total_tokens(resp)
    if raw is None or raw <= 0:
        return 1
    return raw
