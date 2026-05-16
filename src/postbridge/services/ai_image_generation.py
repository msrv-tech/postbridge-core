"""Post image generation through the configured OpenAI-compatible image endpoint."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any

import httpx

from postbridge.config import get_settings
from postbridge.domain.errors import ExternalApiError, ValidationError


IMAGE_GENERATION_PATH = "/v1/images/generations"


@dataclass(frozen=True, slots=True)
class ImageGenerationResult:
    data: bytes
    content_type: str
    usage_tokens_charged: int


DEFAULT_POST_IMAGE_STYLE_PROMPT = """
Create a 16:9 horizontal cover image for a Postbridge platform article or post.

Style: soft minimal 3D claymorphism, tactile matte objects, rounded shapes, warm off-white studio background, gentle shadows, clean premium SaaS/product illustration, calm and friendly but not childish.

Composition: one clear central metaphor for the content topic. Prefer a small source content tile, bridge, path, portal, calendar, radar, approval mark, or connected destination tiles. Keep the scene sparse with one main object group and plenty of empty space.

Color: warm off-white background, teal as the main Postbridge accent, small coral and warm yellow secondary accents, muted graphite details. Avoid purple-dominant palettes and heavy gradients.

Content rules: no readable text, no Cyrillic, no fake UI paragraphs, no real platform logos, no brand slogans, no people, no robots, no watermark, no clutter.
""".strip()


def _truncate(value: str | None, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    suffix = "..."
    if limit <= len(suffix):
        return suffix[: max(0, limit)]
    return text[: limit - len(suffix)].rstrip() + suffix


def build_post_image_prompt(
    *,
    user_prompt: str | None,
    title: str | None,
    summary: str | None,
    content_md: str | None,
    style_prompt: str | None = None,
) -> str:
    style = (style_prompt or get_settings().ai_image_style_prompt or DEFAULT_POST_IMAGE_STYLE_PROMPT).strip()
    parts = [
        style,
        "",
        "Use the following post context to choose the key metaphor. Do not render any readable text from it.",
    ]
    if user_prompt and user_prompt.strip():
        parts.append(f"User image request: {_truncate(user_prompt, 800)}")
    if title and title.strip():
        parts.append(f"Post title: {_truncate(title, 300)}")
    if summary and summary.strip():
        parts.append(f"Post summary: {_truncate(summary, 600)}")
    if content_md and content_md.strip():
        parts.append(f"Post body excerpt: {_truncate(content_md, 1600)}")
    parts.append("")
    parts.append("Return a polished image only. No text, letters, captions, UI labels, or logos.")
    return "\n".join(parts)


def _extract_b64_image(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                value = item.get("b64_json") or item.get("base64") or item.get("image")
                if isinstance(value, str) and value.strip():
                    return value.strip()
    for key in ("b64_json", "base64", "image"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_image_url(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                value = item.get("url")
                if isinstance(value, str) and value.strip():
                    return value.strip()
    value = body.get("url")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _extract_usage_tokens_charged(body: Any) -> int:
    if not isinstance(body, dict):
        return 1
    usage = body.get("usage")
    candidates: list[Any] = []
    if isinstance(usage, dict):
        candidates.extend(
            [
                usage.get("total_tokens"),
                usage.get("gitsell_tokens_charged"),
                usage.get("usage_tokens_charged"),
            ]
        )
    candidates.extend(
        [
            body.get("usage_tokens_charged"),
            body.get("gitsell_tokens_charged"),
            body.get("gitsell_tokens_spent"),
            body.get("total_tokens"),
        ]
    )
    for value in candidates:
        if value is None:
            continue
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            continue
    return 1


def generate_image_bytes(
    prompt: str,
    *,
    model: str | None = None,
    correlation_id: str | None = None,
) -> ImageGenerationResult:
    settings = get_settings()
    base = (settings.ai_gateway_base_url or "").strip().rstrip("/")
    if not base:
        raise ValidationError(
            code="VALIDATION_AI_GATEWAY_DISABLED",
            message="AI gateway is not configured",
            details={},
        )
    image_model = (model or settings.ai_image_generation_model or "").strip()
    if not image_model:
        raise ValidationError(
            code="VALIDATION_AI_IMAGE_MODEL_REQUIRED",
            message="AI image model is not configured",
            details={"required": "AI_IMAGE_GENERATION_MODEL"},
        )

    payload: dict[str, Any] = {
        "model": image_model,
        "prompt": prompt,
        "n": 1,
        "size": settings.ai_image_generation_size,
        "response_format": "url",
    }
    headers = {"Content-Type": "application/json"}
    if settings.ai_gateway_api_key:
        headers["Authorization"] = f"Bearer {settings.ai_gateway_api_key}"
    if correlation_id:
        headers["X-Correlation-Id"] = correlation_id

    gateway_timeout = max(float(settings.ai_gateway_timeout_seconds), 60.0)

    try:
        with httpx.Client(timeout=gateway_timeout) as client:
            response = client.post(f"{base}{IMAGE_GENERATION_PATH}", headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise ExternalApiError(
            code="EXTERNAL_AI_IMAGE_TIMEOUT",
            message="AI image generation timed out",
            source="ai_gateway",
            retryable=True,
            details={"path": IMAGE_GENERATION_PATH},
        ) from exc
    except httpx.HTTPError as exc:
        raise ExternalApiError(
            code="EXTERNAL_AI_IMAGE_TRANSPORT",
            message="AI image generation request failed",
            source="ai_gateway",
            retryable=True,
            details={"path": IMAGE_GENERATION_PATH, "reason": str(exc)},
        ) from exc

    if response.status_code >= 400:
        raise ExternalApiError(
            code="EXTERNAL_AI_IMAGE_HTTP_ERROR",
            message="AI image generation failed",
            source="ai_gateway",
            retryable=response.status_code >= 500 or response.status_code == 429,
            details={"status_code": response.status_code, "path": IMAGE_GENERATION_PATH, "body": response.text[:2000]},
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise ExternalApiError(
            code="EXTERNAL_AI_IMAGE_INVALID_RESPONSE",
            message="AI image generation returned invalid JSON",
            source="ai_gateway",
            retryable=False,
            details={"path": IMAGE_GENERATION_PATH},
        ) from exc

    usage_tokens_charged = _extract_usage_tokens_charged(body)
    b64 = _extract_b64_image(body)
    if b64:
        try:
            return ImageGenerationResult(
                data=base64.b64decode(b64),
                content_type="image/png",
                usage_tokens_charged=usage_tokens_charged,
            )
        except (binascii.Error, ValueError) as exc:
            raise ExternalApiError(
                code="EXTERNAL_AI_IMAGE_INVALID_RESPONSE",
                message="AI image generation returned invalid base64",
                source="ai_gateway",
                retryable=False,
                details={"path": IMAGE_GENERATION_PATH},
            ) from exc

    image_url = _extract_image_url(body)
    if image_url:
        try:
            with httpx.Client(timeout=gateway_timeout, follow_redirects=True) as client:
                image_response = client.get(image_url)
            image_response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ExternalApiError(
                code="EXTERNAL_AI_IMAGE_FETCH_FAILED",
                message="Could not fetch generated image",
                source="ai_gateway",
                retryable=True,
                details={"path": IMAGE_GENERATION_PATH},
            ) from exc
        content_type = image_response.headers.get("content-type", "image/png").split(";")[0]
        return ImageGenerationResult(
            data=image_response.content,
            content_type=content_type if content_type.startswith("image/") else "image/png",
            usage_tokens_charged=usage_tokens_charged,
        )

    raise ExternalApiError(
        code="EXTERNAL_AI_IMAGE_INVALID_RESPONSE",
        message="AI image generation response did not contain an image",
        source="ai_gateway",
        retryable=False,
        details={"path": IMAGE_GENERATION_PATH},
    )
