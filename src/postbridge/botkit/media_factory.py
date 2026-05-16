"""Media storage factory for the thin bot runtime."""

from __future__ import annotations

from functools import lru_cache

from postbridge.config import get_settings

from .media_protocol import MediaStorageProvider


@lru_cache
def get_media_storage_provider() -> MediaStorageProvider | None:
    storage_type = (get_settings().media_storage_type or "").lower()
    if storage_type == "s3":
        from postbridge.botkit.media_s3 import S3StorageProvider

        return S3StorageProvider()
    if storage_type == "local":
        from postbridge.botkit.media_local import LocalStorageProvider

        return LocalStorageProvider()
    return None
