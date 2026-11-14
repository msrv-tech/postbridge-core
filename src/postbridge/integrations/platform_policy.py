"""Regional provider availability; this never changes stored connections."""

from typing import Any

from postbridge.config import get_settings
from postbridge.domain.errors import ValidationError


def normalize_platform_id(platform: str) -> str:
    normalized = (platform or "").strip().lower()
    return "x" if normalized == "twitter" else normalized


def disabled_platforms() -> set[str]:
    raw = get_settings().postbridge_disabled_platforms or ""
    return {
        normalize_platform_id(part)
        for part in raw.replace(";", ",").split(",")
        if part.strip()
    }


def assert_platform_enabled(platform: str) -> None:
    normalized = normalize_platform_id(platform)
    if normalized in disabled_platforms():
        raise ValidationError(
            code="PLATFORM_DISABLED",
            message="This platform is not available in this deployment.",
            details={"platform": normalized},
        )


def is_platform_enabled(platform: str) -> bool:
    return normalize_platform_id(platform) not in disabled_platforms()


def assert_channel_enabled(channel: Any, *, tenant_id: str, requested_platform: str | None = None) -> None:
    """Check a caller-resolved canonical channel without changing records."""
    if channel is None or channel.tenant_id != tenant_id:
        raise ValidationError(code="VALIDATION_CHANNEL_NOT_FOUND", message="Channel not found in this tenant")
    assert_platform_enabled(channel.platform)
    if requested_platform is not None and normalize_platform_id(requested_platform) != normalize_platform_id(channel.platform):
        raise ValidationError(code="VALIDATION_CHANNEL_PLATFORM_MISMATCH", message="Target platform does not match its channel")
