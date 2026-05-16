"""Protocol for media storage providers."""

from __future__ import annotations

from typing import Protocol


class MediaStorageProvider(Protocol):
    """Media storage contract for live-sync assets."""

    async def upload_from_url(self, source_url: str, key: str) -> str:
        ...

    async def upload_from_bytes(self, data: bytes, key: str) -> str:
        ...

    async def delete_object(self, key: str) -> None:
        ...
