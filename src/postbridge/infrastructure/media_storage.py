"""Загрузка бинарных медиа в локальное хранилище или S3 (канон Core)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from urllib.parse import quote, urljoin
from uuid import UUID

from postbridge.config import get_settings

logger = logging.getLogger(__name__)


def copy_media_object_to_file(object_key: str, destination: Path) -> None:
    """Stream a managed object to a worker's private temporary directory."""
    settings = get_settings()
    if settings.media_storage_type == "local":
        root = Path(settings.media_storage_path or "/var/postbridge/media").resolve()
        source = (root / object_key).resolve()
        source.relative_to(root)
        shutil.copyfile(source, destination)
    elif settings.media_storage_type == "s3":
        _s3_client(settings).download_file(settings.s3_bucket, object_key, str(destination))
    else:
        raise RuntimeError("Media storage is not configured")


def upload_media_file(object_key: str, source: Path, content_type: str) -> None:
    """Upload a rendered file without loading it into application memory."""
    settings = get_settings()
    if settings.media_storage_type == "local":
        root = Path(settings.media_storage_path or "/var/postbridge/media").resolve()
        destination = (root / object_key).resolve()
        destination.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    elif settings.media_storage_type == "s3":
        _s3_client(settings).upload_file(str(source), settings.s3_bucket, object_key, ExtraArgs={"ContentType": content_type})
    else:
        raise RuntimeError("Media storage is not configured")


def _managed_media_base_url(settings) -> str:
    return (settings.media_base_url or f"{settings.core_base_url.rstrip('/')}/media").rstrip("/")


def managed_media_url(asset_id: str, object_key: str) -> str:
    """Stable public URL using the existing Core proxy for private S3 objects."""
    settings = get_settings()
    base = _managed_media_base_url(settings)
    return f"{base}/{asset_id}" if settings.media_storage_type == "s3" else f"{base}/{quote(object_key)}"


def managed_s3_media_asset_id(url: str) -> str | None:
    """Recognize only the canonical public proxy URL for an S3 asset."""
    settings = get_settings()
    if settings.media_storage_type != "s3":
        return None
    base, _, asset_id = url.rpartition("/")
    if base != _managed_media_base_url(settings):
        return None
    try:
        return asset_id if str(UUID(asset_id)) == asset_id else None
    except ValueError:
        return None


def upload_media_object(object_key: str, data: bytes, content_type: str) -> str:
    """
    Сохраняет объект и возвращает публичный URL.
    Режим: MEDIA_STORAGE_TYPE=local|s3|none (none → ошибка).
    """
    settings = get_settings()
    mode = settings.media_storage_type
    if mode == "local":
        return _upload_local(object_key, data, settings)
    if mode == "s3":
        return _upload_s3(object_key, data, content_type, settings)
    raise RuntimeError(
        "MEDIA_STORAGE_NOT_CONFIGURED: set MEDIA_STORAGE_TYPE to local or s3 "
        "and configure MEDIA_STORAGE_PATH/MEDIA_BASE_URL or S3_*"
    )


def delete_media_object(object_key: str) -> None:
    """Delete an object from the configured canonical media storage."""
    settings = get_settings()
    mode = settings.media_storage_type
    if mode == "local":
        _delete_local(object_key, settings)
        return
    if mode == "s3":
        _delete_s3(object_key, settings)
        return
    raise RuntimeError(
        "MEDIA_STORAGE_NOT_CONFIGURED: set MEDIA_STORAGE_TYPE to local or s3 "
        "and configure MEDIA_STORAGE_PATH/MEDIA_BASE_URL or S3_*"
    )


def read_media_object(object_key: str, *, max_bytes: int | None = None) -> bytes:
    """Read an object through the configured canonical media storage."""
    settings = get_settings()
    mode = settings.media_storage_type
    if mode == "local":
        return _read_local(object_key, settings, max_bytes=max_bytes)
    if mode == "s3":
        return _read_s3(object_key, settings, max_bytes=max_bytes)
    raise RuntimeError(
        "MEDIA_STORAGE_NOT_CONFIGURED: set MEDIA_STORAGE_TYPE to local or s3 "
        "and configure MEDIA_STORAGE_PATH/MEDIA_BASE_URL or S3_*"
    )


def _upload_local(object_key: str, data: bytes, settings) -> str:
    if not settings.media_storage_path or not settings.media_base_url:
        raise RuntimeError("local media requires MEDIA_STORAGE_PATH and MEDIA_BASE_URL")
    base_path = Path(settings.media_storage_path)
    full_path = base_path / object_key
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(data)
    base_url = settings.media_base_url.rstrip("/")
    return urljoin(base_url + "/", quote(object_key))


def _delete_local(object_key: str, settings) -> None:
    if not settings.media_storage_path:
        raise RuntimeError("local media requires MEDIA_STORAGE_PATH")
    base_path = Path(settings.media_storage_path).resolve()
    full_path = (base_path / object_key).resolve()
    try:
        full_path.relative_to(base_path)
    except ValueError as exc:
        raise RuntimeError("invalid local media object key") from exc
    full_path.unlink(missing_ok=True)


def _read_local(object_key: str, settings, *, max_bytes: int | None = None) -> bytes:
    if not settings.media_storage_path:
        raise RuntimeError("local media requires MEDIA_STORAGE_PATH")
    base_path = Path(settings.media_storage_path).resolve()
    full_path = (base_path / object_key).resolve()
    try:
        full_path.relative_to(base_path)
    except ValueError as exc:
        raise RuntimeError("invalid local media object key") from exc
    if max_bytes is None:
        return full_path.read_bytes()
    with full_path.open("rb") as stored:
        data = stored.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise RuntimeError("local media exceeds requested limit")
    return data


def _s3_client(settings):
    if not settings.s3_bucket:
        raise RuntimeError("s3 media requires S3_BUCKET")

    import boto3
    from botocore.config import Config

    cfg = Config(
        signature_version="s3v4",
        retries={"mode": "standard", "max_attempts": 2},
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    )
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key or None,
        aws_secret_access_key=settings.s3_secret_key or None,
        config=cfg,
    )


def _upload_s3(object_key: str, data: bytes, content_type: str, settings) -> str:
    client = _s3_client(settings)
    extra: dict[str, str] = {}
    if content_type:
        extra["ContentType"] = content_type
    client.put_object(Bucket=settings.s3_bucket, Key=object_key, Body=data, **extra)
    if settings.s3_public_base_url:
        return urljoin(settings.s3_public_base_url.rstrip("/") + "/", quote(object_key))
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": object_key},
        ExpiresIn=3600,
    )


def _delete_s3(object_key: str, settings) -> None:
    client = _s3_client(settings)
    client.delete_object(Bucket=settings.s3_bucket, Key=object_key)


def read_bounded_s3_media_object(object_key: str, *, max_bytes: int) -> bytes:
    settings = get_settings()
    if settings.media_storage_type != "s3":
        raise RuntimeError("S3 media storage is required")
    return _read_s3(object_key, settings, max_bytes=max_bytes)


def _read_s3(object_key: str, settings, *, max_bytes: int | None = None) -> bytes:
    from botocore.exceptions import BotoCoreError, ClientError

    client = _s3_client(settings)
    try:
        response = client.get_object(Bucket=settings.s3_bucket, Key=object_key)
    except ClientError as exc:
        error = exc.response
        code = str(error.get("Error", {}).get("Code", "")) if isinstance(error, dict) else ""
        if code in {"404", "NoSuchKey", "NotFound"}:
            raise FileNotFoundError(object_key) from exc
        raise RuntimeError("failed to read media object from s3") from exc
    except BotoCoreError as exc:
        raise RuntimeError("failed to read media object from s3") from exc
    body = response.get("Body") if isinstance(response, dict) else None
    if body is None or not hasattr(body, "read"):
        raise RuntimeError("s3 media response body is missing")
    try:
        if max_bytes is None:
            data = body.read()
        else:
            content_length = response.get("ContentLength")
            if isinstance(content_length, int) and content_length > max_bytes:
                raise RuntimeError("S3 media exceeds requested limit")
            try:
                data = body.read(max_bytes + 1)
            except BotoCoreError as exc:
                raise RuntimeError("failed to read bounded media from s3") from exc
            if isinstance(data, bytes) and len(data) > max_bytes:
                raise RuntimeError("S3 media exceeds requested limit")
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()
    if not isinstance(data, bytes):
        raise RuntimeError("s3 media response body is invalid")
    return data
