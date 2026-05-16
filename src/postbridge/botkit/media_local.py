"""Local filesystem media storage."""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import quote, urljoin

import httpx

from postbridge.config import get_settings


class LocalStorageProvider:
    """Stores media on local disk and returns public URLs."""

    async def upload_from_url(self, source_url: str, key: str) -> str:
        async with httpx.AsyncClient() as client:
            resp = await client.get(source_url)
            resp.raise_for_status()
            body = resp.content
        return await self.upload_from_bytes(body, key)

    async def upload_from_bytes(self, data: bytes, key: str) -> str:
        settings = get_settings()
        base_path = Path(settings.media_storage_path)
        full_path = base_path / key
        full_path.parent.mkdir(parents=True, exist_ok=True)

        def _write() -> None:
            with open(full_path, "wb") as f:
                f.write(data)

        await asyncio.to_thread(_write)

        base_url = settings.media_base_url.rstrip("/")
        if not base_url:
            raise ValueError("media_base_url not configured for local storage")
        return urljoin(base_url + "/", quote(key))

    async def delete_object(self, key: str) -> None:
        settings = get_settings()
        base_path = Path(settings.media_storage_path)
        full_path = base_path / key

        def _unlink() -> None:
            if full_path.is_file():
                full_path.unlink()

        await asyncio.to_thread(_unlink)
