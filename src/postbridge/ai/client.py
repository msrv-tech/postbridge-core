"""HTTP-клиент и echo-реализация AI Gateway (только OpenAI-совместимые пути)."""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator, Protocol

import httpx

from postbridge.ai.json_generate_reply import postprocess_generate_chat_json
from postbridge.ai.schemas import (
    GatewayAdaptRequest,
    GatewayGenerateRequest,
    GatewayTextResponse,
    GatewayTranslateRequest,
    GatewayUsageStats,
)
from postbridge.ai.streaming import (
    extract_stream_delta_text,
    extract_usage_from_chunk,
    iter_openai_sse_json_payloads,
    root_total_tokens,
)
from postbridge.domain.errors import ExternalApiError

logger = logging.getLogger(__name__)

OPENAI_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"


class AiGatewayClient(Protocol):
    def adapt_for_platform(self, req: GatewayAdaptRequest) -> GatewayTextResponse: ...
    def translate(self, req: GatewayTranslateRequest) -> GatewayTextResponse: ...
    def generate_post(self, req: GatewayGenerateRequest) -> GatewayTextResponse: ...
    def iter_generate_post(self, req: GatewayGenerateRequest) -> Iterator[dict[str, Any]]: ...


def _openai_assistant_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, dict):
                if p.get("type") == "text" and isinstance(p.get("text"), str):
                    parts.append(p["text"])
                elif isinstance(p.get("content"), str):
                    parts.append(p["content"])
        return "\n".join(parts).strip()
    return ""


def parse_openai_chat_completion_to_gateway_response(data: dict[str, Any]) -> GatewayTextResponse:
    """Маппинг тела ответа POST /v1/chat/completions → GatewayTextResponse."""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ExternalApiError(
            code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
            message="OpenAI response has no choices",
            source="ai_gateway",
            retryable=False,
            details={},
        )
    first = choices[0]
    if not isinstance(first, dict):
        raise ExternalApiError(
            code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
            message="OpenAI choice is not an object",
            source="ai_gateway",
            retryable=False,
            details={},
        )
    msg = first.get("message")
    if not isinstance(msg, dict):
        msg = first.get("delta") if isinstance(first.get("delta"), dict) else {}
    text = _openai_assistant_content_to_text(msg.get("content"))
    if not text:
        raise ExternalApiError(
            code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
            message="OpenAI assistant message has empty content",
            source="ai_gateway",
            retryable=False,
            details={},
        )
    usage: GatewayUsageStats | None = None
    raw_u = data.get("usage")
    if isinstance(raw_u, dict):
        usage = GatewayUsageStats(
            total_tokens=raw_u.get("total_tokens"),
            prompt_tokens=raw_u.get("prompt_tokens"),
            completion_tokens=raw_u.get("completion_tokens"),
        )
    total_root = data.get("total_tokens")
    tr: int | None = None
    if isinstance(total_root, int):
        tr = max(0, total_root)
    return GatewayTextResponse(
        title=None,
        body_text=text,
        usage=usage,
        total_tokens=tr,
    )


class HttpAiGatewayClient:
    """Вызовы только к OpenAI-совместимому POST /v1/chat/completions."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        timeout_seconds: float,
        default_model: str | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = httpx.Timeout(timeout_seconds)
        self._default_model = (default_model or "").strip() or None

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def _resolve_model(self, explicit: str | None) -> str:
        m = (explicit or "").strip() or self._default_model
        if not m:
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_VALIDATION",
                message="model is required (pass in request or set AI_GATEWAY_DEFAULT_MODEL)",
                source="ai_gateway",
                retryable=False,
                details={},
            )
        return m

    def _chat_completions(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        json_response_format: bool = False,
    ) -> GatewayTextResponse:
        url = f"{self._base}{OPENAI_CHAT_COMPLETIONS_PATH}"
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if json_response_format:
            payload = {**payload, "response_format": {"type": "json_object"}}
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.post(url, json=payload, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_TIMEOUT",
                message="AI gateway request timed out",
                source="ai_gateway",
                retryable=True,
                details={"path": OPENAI_CHAT_COMPLETIONS_PATH},
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_TRANSPORT",
                message="AI gateway transport error",
                source="ai_gateway",
                retryable=True,
                details={"path": OPENAI_CHAT_COMPLETIONS_PATH, "reason": str(exc)},
            ) from exc

        if r.status_code >= 400:
            detail: Any
            try:
                detail = r.json()
            except ValueError:
                detail = r.text[:2000]
            logger.warning(
                "AI gateway HTTP %s %s: %s", r.status_code, OPENAI_CHAT_COMPLETIONS_PATH, detail
            )
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_HTTP_ERROR",
                message="AI gateway returned error status",
                source="ai_gateway",
                retryable=r.status_code >= 500 or r.status_code == 429,
                details={
                    "status_code": r.status_code,
                    "path": OPENAI_CHAT_COMPLETIONS_PATH,
                    "body": detail,
                },
            )

        try:
            body = r.json()
        except ValueError as exc:
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
                message="AI gateway returned non-JSON body",
                source="ai_gateway",
                retryable=False,
                details={"path": OPENAI_CHAT_COMPLETIONS_PATH},
            ) from exc
        if not isinstance(body, dict):
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_INVALID_RESPONSE",
                message="AI gateway JSON is not an object",
                source="ai_gateway",
                retryable=False,
                details={"path": OPENAI_CHAT_COMPLETIONS_PATH},
            )
        return parse_openai_chat_completion_to_gateway_response(body)

    def adapt_for_platform(self, req: GatewayAdaptRequest) -> GatewayTextResponse:
        sys_lines = [
            "You are an editor. Adapt the following content for the target publishing platform.",
            f"Platform identifier: {req.platform}.",
            "Reply with the adapted post body only (markdown allowed). No preamble or quotes.",
        ]
        if req.target_language:
            sys_lines.append(f"Prefer output in language: {req.target_language}.")
        if req.capabilities_hint:
            try:
                hint = json.dumps(req.capabilities_hint, ensure_ascii=False)[:8000]
                sys_lines.append(f"Channel constraints (JSON): {hint}")
            except (TypeError, ValueError):
                pass
        user_parts: list[str] = []
        if req.title:
            user_parts.append(f"Title: {req.title}")
            user_parts.append("")
        user_parts.append(req.source_text)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": "\n".join(sys_lines)},
            {"role": "user", "content": "\n".join(user_parts)},
        ]
        return self._chat_completions(model=self._resolve_model(req.model), messages=messages)

    def translate(self, req: GatewayTranslateRequest) -> GatewayTextResponse:
        system = (
            "You translate text for publication. Output only the translation; "
            "preserve markdown structure. Target language code: "
            f"{req.target_language}."
        )
        user_parts: list[str] = []
        if req.title:
            user_parts.append(f"Title: {req.title}")
            user_parts.append("")
        user_parts.append(req.source_text)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(user_parts)},
        ]
        return self._chat_completions(model=self._resolve_model(req.model), messages=messages)

    def _build_generate_messages(self, req: GatewayGenerateRequest) -> list[dict[str, str]]:
        from postbridge.ai.json_generate_reply import GENERATE_JSON_SYSTEM_MESSAGE

        messages: list[dict[str, str]] = []
        if req.messages:
            messages.append({"role": "system", "content": GENERATE_JSON_SYSTEM_MESSAGE})
        if req.target_language:
            tl = str(req.target_language).strip()
            messages.append(
                {
                    "role": "system",
                    "content": f"Prefer responding in {tl}.",
                }
            )
            if tl.lower().startswith("ru"):
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "JSON fields title, body_markdown, hashtags, mentions, link_url: use Russian for "
                            "user-visible text unless the user explicitly asked for another language. "
                            "The draft context may be in English — still write the post fields in Russian unless "
                            "the user says otherwise."
                        ),
                    }
                )
        if req.messages:
            messages.extend([{"role": m.role, "content": m.content} for m in req.messages])
        elif req.prompt is not None and str(req.prompt).strip():
            messages.append({"role": "user", "content": req.prompt.strip()})
        else:
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_VALIDATION",
                message="generate_post requires prompt or messages",
                source="ai_gateway",
                retryable=False,
                details={},
            )
        return messages

    def generate_post(self, req: GatewayGenerateRequest) -> GatewayTextResponse:
        use_json = bool(req.messages)
        gw_raw = self._chat_completions(
            model=self._resolve_model(req.model),
            messages=self._build_generate_messages(req),
            json_response_format=use_json,
        )
        return postprocess_generate_chat_json(gw_raw, req)

    def iter_generate_post(self, req: GatewayGenerateRequest) -> Iterator[dict[str, Any]]:
        """Стриминг chat/completions: yield {type: delta, text} затем {type: complete, gateway}."""
        url = f"{self._base}{OPENAI_CHAT_COMPLETIONS_PATH}"
        model = self._resolve_model(req.model)
        messages = self._build_generate_messages(req)
        use_json = bool(req.messages)
        try:
            payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
            if use_json:
                payload["response_format"] = {"type": "json_object"}
            with httpx.Client(timeout=self._timeout) as http:
                with http.stream("POST", url, json=payload, headers=self._headers()) as r:
                    if r.status_code >= 400:
                        detail: Any
                        try:
                            detail = r.read().decode("utf-8", errors="replace")[:2000]
                        except Exception:
                            detail = ""
                        try:
                            detail_json = json.loads(detail) if detail else {}
                        except json.JSONDecodeError:
                            detail_json = detail
                        if use_json:
                            logger.info(
                                "AI gateway stream with json_object failed; retrying non-streaming with json_object (%s)",
                                detail_json,
                            )
                            gw_raw = self._chat_completions(
                                model=model,
                                messages=messages,
                                json_response_format=True,
                            )
                            gw = postprocess_generate_chat_json(gw_raw, req)
                            yield {"type": "delta", "text": gw.body_text or ""}
                            yield {"type": "complete", "gateway": gw}
                            return
                        logger.warning(
                            "AI gateway HTTP %s %s (stream): %s",
                            r.status_code,
                            OPENAI_CHAT_COMPLETIONS_PATH,
                            detail_json,
                        )
                        raise ExternalApiError(
                            code="EXTERNAL_AI_GATEWAY_HTTP_ERROR",
                            message="AI gateway returned error status",
                            source="ai_gateway",
                            retryable=r.status_code >= 500 or r.status_code == 429,
                            details={
                                "status_code": r.status_code,
                                "path": OPENAI_CHAT_COMPLETIONS_PATH,
                                "body": detail_json,
                            },
                        )
                    last_usage: GatewayUsageStats | None = None
                    last_root_total: int | None = None
                    parts: list[str] = []
                    for obj in iter_openai_sse_json_payloads(r.iter_lines()):
                        u = extract_usage_from_chunk(obj)
                        if u is not None:
                            last_usage = u
                        rt = root_total_tokens(obj)
                        if rt is not None:
                            last_root_total = rt
                        frag = extract_stream_delta_text(obj)
                        if frag:
                            parts.append(frag)
                            yield {"type": "delta", "text": frag}
                    full = "".join(parts).strip()
                    if not full:
                        logger.info(
                            "AI gateway stream returned no assistant text; using non-streaming chat/completions (model=%s)",
                            model,
                        )
                        gw = self.generate_post(req)
                        yield {"type": "delta", "text": gw.body_text or ""}
                        yield {"type": "complete", "gateway": gw}
                    else:
                        gw_raw = GatewayTextResponse(
                            title=None,
                            body_text=full,
                            usage=last_usage,
                            total_tokens=last_root_total,
                        )
                        gw = postprocess_generate_chat_json(gw_raw, req)
                        yield {"type": "complete", "gateway": gw}
                    return
        except ExternalApiError:
            raise
        except httpx.TimeoutException as exc:
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_TIMEOUT",
                message="AI gateway request timed out",
                source="ai_gateway",
                retryable=True,
                details={"path": OPENAI_CHAT_COMPLETIONS_PATH},
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalApiError(
                code="EXTERNAL_AI_GATEWAY_TRANSPORT",
                message="AI gateway transport error",
                source="ai_gateway",
                retryable=True,
                details={"path": OPENAI_CHAT_COMPLETIONS_PATH, "reason": str(exc)},
            ) from exc


class EchoAiGatewayClient:
    """Детерминированный stub для тестов без внешнего шлюза."""

    def adapt_for_platform(self, req: GatewayAdaptRequest) -> GatewayTextResponse:
        text = f"[adapt:{req.platform}] {req.source_text[:8000]}"
        return GatewayTextResponse(
            title=req.title,
            body_text=text,
            usage=GatewayUsageStats(total_tokens=1),
        )

    def translate(self, req: GatewayTranslateRequest) -> GatewayTextResponse:
        text = f"[translate:{req.target_language}] {req.source_text[:8000]}"
        return GatewayTextResponse(
            title=req.title,
            body_text=text,
            usage=GatewayUsageStats(total_tokens=1),
        )

    def generate_post(self, req: GatewayGenerateRequest) -> GatewayTextResponse:
        if req.messages:
            last = req.messages[-1].content
            inner = f"[generate-chat] {last[:7900]}"
            raw = json.dumps(
                {"title": "Generated", "body_markdown": inner},
                ensure_ascii=False,
            )
            gw0 = GatewayTextResponse(
                title=None,
                body_text=raw,
                usage=GatewayUsageStats(total_tokens=1),
            )
            return postprocess_generate_chat_json(gw0, req)
        body = f"[generate] {(req.prompt or '')[:8000]}"
        return GatewayTextResponse(
            title="Generated",
            body_text=body,
            usage=GatewayUsageStats(total_tokens=1),
        )

    def iter_generate_post(self, req: GatewayGenerateRequest) -> Iterator[dict[str, Any]]:
        gw = self.generate_post(req)
        body = gw.body_text or ""
        step = max(1, len(body) // 4) if len(body) > 12 else max(1, len(body))
        for i in range(0, len(body), step):
            yield {"type": "delta", "text": body[i : i + step]}
        yield {"type": "complete", "gateway": gw}


def gateway_response_to_warnings_json(resp: GatewayTextResponse) -> str | None:
    if not resp.warnings:
        return None
    return json.dumps(resp.warnings, ensure_ascii=False)
