"""Download media URLs before platform-specific uploads."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse

import httpx

from postbridge.domain.errors import ExternalApiError
from postbridge.domain.models import PostPayload


@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    url: str
    data: bytes
    content_type: str
    filename: str


def media_urls(payload: PostPayload, *, limit: int) -> list[str]:
    urls: list[str] = []
    if payload.media_url:
        urls.append(payload.media_url)
    if payload.media_urls:
        urls.extend(payload.media_urls)
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        value = (raw or "").strip()
        if value and value not in seen:
            out.append(value)
            seen.add(value)
        if len(out) >= limit:
            break
    return out


def filename_from_url(url: str) -> str:
    name = PurePosixPath(urlparse(url).path).name.strip()
    return name or "postbridge-media"


def download_media(
    client: httpx.Client,
    url: str,
    *,
    source: str,
    target_channel: str,
    max_bytes: int,
) -> DownloadedMedia:
    try:
        response = client.get(url, follow_redirects=True, timeout=90.0)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ExternalApiError(
            code=f"EXTERNAL_API_{source.upper()}_MEDIA_DOWNLOAD_ERROR",
            message=f"Could not download media for {source}: HTTP {exc.response.status_code}",
            source=source,
            retryable=exc.response.status_code in (408, 429, 500, 502, 503, 504),
            details={"target_channel": target_channel, "media_url": url[:500]},
        ) from exc
    except httpx.RequestError as exc:
        raise ExternalApiError(
            code=f"EXTERNAL_API_{source.upper()}_MEDIA_DOWNLOAD_ERROR",
            message=f"Network error while downloading media for {source}.",
            source=source,
            retryable=True,
            details={"target_channel": target_channel, "media_url": url[:500]},
        ) from exc
    if len(response.content) > max_bytes:
        raise ExternalApiError(
            code=f"EXTERNAL_API_{source.upper()}_MEDIA_TOO_LARGE",
            message=f"Media file is too large for {source}.",
            source=source,
            retryable=False,
            details={"target_channel": target_channel, "media_url": url[:500]},
        )
    return DownloadedMedia(
        url=url,
        data=response.content,
        content_type=response.headers.get("content-type", "application/octet-stream"),
        filename=filename_from_url(url),
    )
