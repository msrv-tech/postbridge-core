"""URL helpers for OpenAI-compatible gateway endpoints."""

from __future__ import annotations


def join_openai_compatible_path(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    normalized_path = path if path.startswith("/") else f"/{path}"
    if base.endswith("/v1") and normalized_path.startswith("/v1/"):
        normalized_path = normalized_path.removeprefix("/v1")
    return f"{base}{normalized_path}"
