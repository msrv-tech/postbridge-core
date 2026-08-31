"""Загрузка бинарных медиа в локальное хранилище или S3 (канон Core)."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote, urljoin

from postbridge.config import get_settings

logger = logging.getLogger(__name__)


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


def _upload_s3(object_key: str, data: bytes, content_type: str, settings) -> str:
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
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key or None,
        aws_secret_access_key=settings.s3_secret_key or None,
        config=cfg,
    )
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
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key or None,
        aws_secret_access_key=settings.s3_secret_key or None,
        config=cfg,
    )
    client.delete_object(Bucket=settings.s3_bucket, Key=object_key)
