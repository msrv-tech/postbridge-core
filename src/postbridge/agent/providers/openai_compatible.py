from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from postbridge.config import get_settings
from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.models.domain import LlmProviderConfigOrm


def _strip_optional_json_fence(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_first_json_object(raw: str) -> str | None:
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw[start : index + 1]
    return None


def _parse_json_object_loose(raw: str) -> dict[str, Any] | None:
    cleaned = _strip_optional_json_fence(raw)
    candidates = [cleaned]
    extracted = _extract_first_json_object(cleaned)
    if extracted and extracted != cleaned:
        candidates.append(extracted)
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _extract_message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                text_parts.append(part["text"])
        return "\n".join(text_parts).strip()
    return ""


@dataclass(slots=True)
class OpenAICompatibleProvider:
    base_url: str
    model_name: str
    embedding_model_name: str | None = None
    api_key: str | None = None
    timeout_seconds: float = 60.0
    max_tokens: int = 2048
    provider_type: str = "openai_compatible"

    @classmethod
    def from_row(cls, row: LlmProviderConfigOrm) -> "OpenAICompatibleProvider":
        settings = get_settings()
        capabilities = _load_provider_json(row.capabilities_json)
        embedding_model_name = (
            settings.agent_llm_embedding_model
            or capabilities.get("embedding_model")
            or capabilities.get("embedding_model_name")
        )
        if not isinstance(embedding_model_name, str) or not embedding_model_name.strip():
            embedding_model_name = settings.agent_llm_default_model or row.model_name
        return cls(
            base_url=settings.agent_llm_base_url or row.base_url,
            model_name=settings.agent_llm_default_model or row.model_name,
            embedding_model_name=embedding_model_name.strip(),
            api_key=settings.agent_llm_api_key or row.api_key,
            max_tokens=max(1, int(settings.agent_llm_max_tokens)),
            provider_type=row.provider_type,
        )

    @classmethod
    def from_env(cls) -> "OpenAICompatibleProvider":
        settings = get_settings()
        if not settings.agent_llm_base_url:
            raise ConfigurationError(
                "AGENT_LLM_BASE_URL (or AI_GATEWAY_BASE_URL fallback) is required for agent runtime."
            )
        if not settings.agent_llm_default_model:
            raise ConfigurationError(
                "AGENT_LLM_DEFAULT_MODEL (or AI_GATEWAY_DEFAULT_MODEL fallback) is required for agent runtime."
            )
        return cls(
            base_url=settings.agent_llm_base_url,
            model_name=settings.agent_llm_default_model,
            embedding_model_name=settings.agent_llm_embedding_model or settings.agent_llm_default_model,
            api_key=settings.agent_llm_api_key,
            max_tokens=max(1, int(settings.agent_llm_max_tokens)),
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_to("/v1/chat/completions", payload)

    def _post_to(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}{path}"
        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout_seconds)) as client:
                response = client.post(url, json=payload, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_TIMEOUT",
                message="agent LLM request timed out",
                source="agent_llm",
                retryable=True,
                details={},
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_TRANSPORT",
                message="agent LLM transport error",
                source="agent_llm",
                retryable=True,
                details={"reason": str(exc)},
            ) from exc
        if response.status_code >= 400:
            try:
                body: Any = response.json()
            except ValueError:
                body = response.text[:2000]
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_HTTP_ERROR",
                message="agent LLM returned error status",
                source="agent_llm",
                retryable=response.status_code >= 500 or response.status_code == 429,
                details={"status_code": response.status_code, "body": body},
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
                message="agent LLM returned non-JSON body",
                source="agent_llm",
                retryable=False,
                details={},
            ) from exc
        if not isinstance(data, dict):
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
                message="agent LLM response is not an object",
                source="agent_llm",
                retryable=False,
                details={},
            )
        return data

    def invoke_text(self, *, messages: list[dict[str, str]], temperature: float = 0.2) -> tuple[str, dict[str, Any]]:
        body = self._post(
            {
                "model": self.model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": self.max_tokens,
            }
        )
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
                message="agent LLM response has no choices",
                source="agent_llm",
                retryable=False,
                details={},
            )
        first = choices[0]
        if not isinstance(first, dict):
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
                message="agent LLM choice is not an object",
                source="agent_llm",
                retryable=False,
                details={},
            )
        message = first.get("message")
        if not isinstance(message, dict):
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
                message="agent LLM message is missing",
                source="agent_llm",
                retryable=False,
                details={},
            )
        text = _extract_message_text(message)
        if not text:
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
                message="agent LLM response content is empty",
                source="agent_llm",
                retryable=False,
                details={},
            )
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return text, usage

    def invoke_json(self, *, messages: list[dict[str, str]], temperature: float = 0.2) -> tuple[dict[str, Any], dict[str, Any]]:
        json_hint = {
            "role": "system",
            "content": "Return only a valid JSON object.",
        }
        payload_messages = [json_hint, *messages]
        attempts = [self.max_tokens]
        if self.max_tokens < 4096:
            attempts.append(4096)
        if self.max_tokens < 8192:
            attempts.append(8192)
        last_error: ExternalApiError | None = None
        for max_tokens in attempts:
            body = self._post(
                {
                    "model": self.model_name,
                    "messages": payload_messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "response_format": {"type": "json_object"},
                }
            )
            choices = body.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ExternalApiError(
                    code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
                    message="agent LLM response has no choices",
                    source="agent_llm",
                    retryable=False,
                    details={},
                )
            first = choices[0]
            if not isinstance(first, dict):
                raise ExternalApiError(
                    code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
                    message="agent LLM choice is not an object",
                    source="agent_llm",
                    retryable=False,
                    details={},
                )
            message = first.get("message")
            if not isinstance(message, dict):
                raise ExternalApiError(
                    code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
                    message="agent LLM JSON content is missing",
                    source="agent_llm",
                    retryable=False,
                    details={},
                )
            text = _extract_message_text(message)
            data = _parse_json_object_loose(text)
            if data is not None:
                usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
                return data, usage
            finish_reason = str(first.get("finish_reason") or "")
            native_finish_reason = str(first.get("native_finish_reason") or "")
            if finish_reason == "length" or native_finish_reason == "max_output_tokens":
                last_error = ExternalApiError(
                    code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
                    message="agent LLM JSON content is invalid",
                    source="agent_llm",
                    retryable=False,
                    details={"reason": "truncated_json", "max_tokens": max_tokens},
                )
                continue
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
                message="agent LLM JSON content is invalid",
                source="agent_llm",
                retryable=False,
                details={},
            )
        assert last_error is not None
        raise last_error

    def invoke_embedding(self, *, text: str) -> tuple[list[float], dict[str, Any]]:
        model = self.embedding_model_name or self.model_name
        body = self._post_to(
            "/v1/embeddings",
            {
                "model": model,
                "input": text,
            },
        )
        data = body.get("data")
        if not isinstance(data, list) or not data:
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
                message="embedding response has no data",
                source="agent_llm",
                retryable=False,
                details={},
            )
        first = data[0]
        if not isinstance(first, dict):
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
                message="embedding item is not an object",
                source="agent_llm",
                retryable=False,
                details={},
            )
        vector = first.get("embedding")
        if not isinstance(vector, list) or not all(isinstance(x, (int, float)) for x in vector):
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
                message="embedding vector missing or invalid",
                source="agent_llm",
                retryable=False,
                details={},
            )
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return [float(x) for x in vector], usage

    def invoke_rerank(
        self,
        *,
        query: str,
        items: list[dict[str, Any]],
        top_k: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        messages = [
            {
                "role": "system",
                "content": (
                    "You rerank candidate items for an editorial agent. "
                    "Return strict JSON with key results. "
                    "results must be a list of objects with keys: index, score, reason."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "query": query,
                        "top_k": top_k,
                        "items": items,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        payload, usage = self.invoke_json(messages=messages, temperature=0.0)
        results = payload.get("results")
        if not isinstance(results, list):
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
                message="rerank response missing results",
                source="agent_llm",
                retryable=False,
                details={},
            )
        normalized: list[dict[str, Any]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            score = item.get("score")
            if not isinstance(idx, int) or not isinstance(score, (int, float)):
                continue
            normalized.append(
                {
                    "index": idx,
                    "score": float(score),
                    "reason": item.get("reason"),
                }
            )
        return normalized[:top_k], usage


def ensure_openai_compatible_provider(row: LlmProviderConfigOrm | None) -> OpenAICompatibleProvider:
    if row is None:
        return OpenAICompatibleProvider.from_env()
    if row.provider_type != "openai_compatible":
        raise ConfigurationError(
            f"Unsupported provider_type for V1: {row.provider_type}",
            details={"supported": ["openai_compatible"]},
        )
    return OpenAICompatibleProvider.from_row(row)


def _load_provider_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
