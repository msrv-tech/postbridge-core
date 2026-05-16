"""S3 media storage provider."""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin

import httpx

from postbridge.config import get_settings


class S3StorageProvider:
    """Uploads media to S3 and returns public or presigned URLs."""

    def _get_client(self):
        import boto3
        from botocore.config import Config

        settings = get_settings()
        config = Config(
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
            config=config,
        )

    async def upload_from_url(self, source_url: str, key: str) -> str:
        settings = get_settings()
        bucket = settings.s3_bucket
        if not bucket:
            raise ValueError("s3_bucket not configured")

        async with httpx.AsyncClient() as client:
            resp = await client.get(source_url)
            resp.raise_for_status()
            body = resp.content

        return await self.upload_from_bytes(body, key)

    async def upload_from_bytes(self, data: bytes, key: str) -> str:
        settings = get_settings()
        bucket = settings.s3_bucket
        if not bucket:
            raise ValueError("s3_bucket not configured")

        def _upload() -> str:
            s3 = self._get_client()
            s3.put_object(Bucket=bucket, Key=key, Body=data)
            if settings.s3_public_base_url:
                return urljoin(settings.s3_public_base_url.rstrip("/") + "/", key)
            return s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=3600,
            )

        return await asyncio.to_thread(_upload)

    async def delete_object(self, key: str) -> None:
        settings = get_settings()
        bucket = settings.s3_bucket
        if not bucket:
            return

        def _delete() -> None:
            s3 = self._get_client()
            s3.delete_object(Bucket=bucket, Key=key)

        await asyncio.to_thread(_delete)
