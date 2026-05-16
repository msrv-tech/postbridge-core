"""Local media storage helper for core_db backend."""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import quote, urljoin

from postbridge.config import get_settings


def _get_media_base_url() -> str:
    settings = get_settings()
    base = (settings.media_base_url or "").strip()
    if base:
        return base.rstrip("/")
    if (settings.media_storage_path or "").strip():
        core = (settings.core_base_url or "").strip().rstrip("/")
        if core:
            return f"{core}/media"
    return ""


class CoreDbLocalStorageProvider:
    """Stores media locally and exposes it via Core /media."""

    def __init__(
        self,
        *,
        storage_path: str | None = None,
        base_url: str | None = None,
    ) -> None:
        settings = get_settings()
        default_path = (settings.media_storage_path or "").strip() or "/var/postbridge/media"
        self.storage_path = Path(storage_path or default_path)
        self.base_url = (base_url or _get_media_base_url()).rstrip("/")

    def is_configured(self) -> bool:
        return bool(self.base_url)

    async def upload_from_bytes(self, data: bytes, key: str) -> str:
        full_path = self.storage_path / key
        full_path.parent.mkdir(parents=True, exist_ok=True)

        def _write() -> None:
            with open(full_path, "wb") as f:
                f.write(data)

        await asyncio.to_thread(_write)
        return urljoin(self.base_url + "/", quote(key))


def get_local_storage() -> CoreDbLocalStorageProvider | None:
    base = _get_media_base_url()
    if not base:
        return None
    return CoreDbLocalStorageProvider()
