"""Publisher for LinkedIn organic posts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse

import httpx

from postbridge.api.schemas import LinkedInCredentials
from postbridge.config import Settings, get_settings
from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.domain.models import PostPayload

LINKEDIN_API_BASE = "https://api.linkedin.com"
LINKEDIN_TEXT_LIMIT = 3000
LINKEDIN_MAX_MEDIA_BYTES = 250 * 1024 * 1024
LINKEDIN_MAX_IMAGES = 20
LINKEDIN_VIDEO_CHUNK_SIZE = 4 * 1024 * 1024

_INVALID_LINKEDIN_CHANNEL = (
    "Invalid LinkedIn target_channel. Use urn:li:organization:<id>, "
    "urn:li:person:<id>, organization:<id>, person:<id>, or a numeric organization id."
)


@dataclass(frozen=True, slots=True)
class _DownloadedMedia:
    url: str
    data: bytes
    content_type: str
    filename: str


@dataclass(frozen=True, slots=True)
class _LinkedInAsset:
    urn: str
    kind: str
    title: str | None = None


def _normalize_author_urn(target_channel: str, credentials: LinkedInCredentials) -> str:
    raw = (credentials.author_urn or target_channel or "").strip()
    if not raw:
        raise ConfigurationError(
            "LinkedIn target_channel or credentials.author_urn is required."
        )
    if raw.startswith("urn:li:organization:"):
        oid = raw.removeprefix("urn:li:organization:").strip()
        if not oid:
            raise ConfigurationError(_INVALID_LINKEDIN_CHANNEL)
        return f"urn:li:organization:{oid}"
    if raw.startswith("urn:li:person:"):
        pid = raw.removeprefix("urn:li:person:").strip()
        if not pid:
            raise ConfigurationError(_INVALID_LINKEDIN_CHANNEL)
        return f"urn:li:person:{pid}"
    if raw.startswith("organization:"):
        oid = raw.split(":", 1)[1].strip()
        if not oid:
            raise ConfigurationError(_INVALID_LINKEDIN_CHANNEL)
        return f"urn:li:organization:{oid}"
    if raw.startswith("person:"):
        pid = raw.split(":", 1)[1].strip()
        if not pid:
            raise ConfigurationError(_INVALID_LINKEDIN_CHANNEL)
        return f"urn:li:person:{pid}"
    if raw.startswith("linkedin/organization/"):
        oid = raw.rsplit("/", 1)[-1].strip()
        if not oid:
            raise ConfigurationError(_INVALID_LINKEDIN_CHANNEL)
        return f"urn:li:organization:{oid}"
    if raw.startswith("linkedin/person/"):
        pid = raw.rsplit("/", 1)[-1].strip()
        if not pid:
            raise ConfigurationError(_INVALID_LINKEDIN_CHANNEL)
        return f"urn:li:person:{pid}"
    if raw.isdigit():
        return f"urn:li:organization:{raw}"
    raise ConfigurationError(_INVALID_LINKEDIN_CHANNEL)


def _trim_commentary(text: str) -> str:
    value = (text or "").strip() or " "
    if len(value) <= LINKEDIN_TEXT_LIMIT:
        return value
    return value[: LINKEDIN_TEXT_LIMIT - 1].rstrip() + "…"


def _media_urls(payload: PostPayload) -> list[str]:
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
    return out


def _filename_from_url(url: str) -> str:
    path = PurePosixPath(urlparse(url).path)
    name = path.name.strip()
    return name or "postbridge-media"


def _kind_from_media(media: _DownloadedMedia) -> str:
    ctype = media.content_type.lower().split(";", 1)[0].strip()
    suffix = PurePosixPath(media.filename.lower()).suffix
    if ctype.startswith("image/") or suffix in {".jpg", ".jpeg", ".png", ".gif"}:
        return "image"
    if ctype.startswith("video/") or suffix in {".mp4", ".mov", ".m4v", ".webm"}:
        return "video"
    if ctype == "application/pdf" or suffix == ".pdf":
        return "document"
    raise ExternalApiError(
        code="EXTERNAL_API_LINKEDIN_MEDIA_TYPE_UNSUPPORTED",
        message="LinkedIn publishing supports images, videos, and PDF documents.",
        source="linkedin",
        retryable=False,
        details={"media_url": media.url, "content_type": media.content_type},
    )


def _instruction_byte_index(
    instruction: dict[str, object],
    key: str,
    fallback: int,
) -> int:
    value = instruction.get(key)
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _post_body(author: str, text: str) -> dict[str, object]:
    return {
        "author": author,
        "commentary": _trim_commentary(text),
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }


class LinkedInPublisher:
    """Client for publishing organic posts to LinkedIn."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def publish_post(
        self,
        target_channel: str,
        payload: PostPayload,
        credentials: LinkedInCredentials | None = None,
    ) -> str | None:
        creds = credentials or self._credentials_from_env()
        if not creds.access_token:
            raise ConfigurationError("Missing LinkedIn access token for publishing.")

        author = _normalize_author_urn(target_channel, creds)
        body = _post_body(author, payload.text)
        headers = {
            "Authorization": f"Bearer {creds.access_token}",
            "LinkedIn-Version": creds.api_version or self.settings.linkedin_api_version,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
                assets = self._upload_media_assets(
                    client=client,
                    headers=headers,
                    author=author,
                    payload=payload,
                    target_channel=target_channel,
                )
                if assets:
                    body["content"] = self._content_for_assets(assets)
                response = client.post(
                    f"{LINKEDIN_API_BASE}/rest/posts",
                    headers=headers,
                    json=body,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            message = str(exc)
            details: dict[str, object] = {
                "status_code": exc.response.status_code,
                "target_channel": target_channel,
            }
            try:
                error_body = exc.response.json()
                if isinstance(error_body, dict):
                    message = str(error_body.get("message") or message)
                    details["linkedin_error"] = error_body
            except Exception:
                pass
            raise ExternalApiError(
                code="EXTERNAL_API_LINKEDIN_HTTP_ERROR",
                message=f"LinkedIn API error: {message}",
                source="linkedin",
                retryable=exc.response.status_code in (408, 409, 425, 429, 500, 502, 503, 504),
                details=details,
            ) from exc
        except httpx.RequestError as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_LINKEDIN_REQUEST_ERROR",
                message="LinkedIn API transport error",
                source="linkedin",
                retryable=True,
                details={"target_channel": target_channel},
            ) from exc

        restli_id = response.headers.get("x-restli-id") or response.headers.get("X-RestLi-Id")
        if restli_id:
            return restli_id
        try:
            data = response.json()
            if isinstance(data, dict):
                raw_id = data.get("id")
                if raw_id is not None:
                    return str(raw_id)
        except Exception:
            pass
        return None

    def _upload_media_assets(
        self,
        *,
        client: httpx.Client,
        headers: dict[str, str],
        author: str,
        payload: PostPayload,
        target_channel: str,
    ) -> list[_LinkedInAsset]:
        urls = _media_urls(payload)
        if not urls:
            return []
        if len(urls) > LINKEDIN_MAX_IMAGES:
            raise ExternalApiError(
                code="EXTERNAL_API_LINKEDIN_TOO_MANY_MEDIA",
                message=f"LinkedIn supports up to {LINKEDIN_MAX_IMAGES} images in one post.",
                source="linkedin",
                retryable=False,
                details={"target_channel": target_channel, "count": len(urls)},
            )
        media = [self._download_media(client, url) for url in urls]
        kinds = [_kind_from_media(item) for item in media]
        if len(set(kinds)) > 1:
            raise ExternalApiError(
                code="EXTERNAL_API_LINKEDIN_MIXED_MEDIA_UNSUPPORTED",
                message="LinkedIn posts cannot mix images, videos, and documents.",
                source="linkedin",
                retryable=False,
                details={"target_channel": target_channel, "media_kinds": kinds},
            )
        if kinds[0] in {"video", "document"} and len(media) > 1:
            raise ExternalApiError(
                code="EXTERNAL_API_LINKEDIN_MULTIPLE_MEDIA_UNSUPPORTED",
                message="LinkedIn supports only one video or document per post.",
                source="linkedin",
                retryable=False,
                details={"target_channel": target_channel, "media_kind": kinds[0]},
            )
        assets: list[_LinkedInAsset] = []
        for item, kind in zip(media, kinds, strict=True):
            if kind == "image":
                assets.append(self._upload_image(client, headers, author, item))
            elif kind == "video":
                assets.append(self._upload_video(client, headers, author, item))
            elif kind == "document":
                assets.append(self._upload_document(client, headers, author, item))
        return assets

    def _download_media(self, client: httpx.Client, url: str) -> _DownloadedMedia:
        response = client.get(url, follow_redirects=True)
        response.raise_for_status()
        data = response.content
        if not data:
            raise ExternalApiError(
                code="EXTERNAL_API_LINKEDIN_MEDIA_EMPTY",
                message="LinkedIn media download returned an empty file.",
                source="linkedin",
                retryable=False,
                details={"media_url": url},
            )
        if len(data) > LINKEDIN_MAX_MEDIA_BYTES:
            raise ExternalApiError(
                code="EXTERNAL_API_LINKEDIN_MEDIA_TOO_LARGE",
                message="LinkedIn media file is too large for this publisher.",
                source="linkedin",
                retryable=False,
                details={"media_url": url, "bytes": len(data)},
            )
        return _DownloadedMedia(
            url=url,
            data=data,
            content_type=response.headers.get("content-type", ""),
            filename=_filename_from_url(url),
        )

    def _upload_image(
        self,
        client: httpx.Client,
        headers: dict[str, str],
        author: str,
        media: _DownloadedMedia,
    ) -> _LinkedInAsset:
        init_response = client.post(
            f"{LINKEDIN_API_BASE}/rest/images?action=initializeUpload",
            headers=headers,
            json={"initializeUploadRequest": {"owner": author}},
        )
        init_response.raise_for_status()
        value = init_response.json().get("value") or {}
        upload_url = value.get("uploadUrl")
        image_urn = value.get("image")
        if not upload_url or not image_urn:
            raise ExternalApiError(
                code="EXTERNAL_API_LINKEDIN_UPLOAD_INIT_INVALID",
                message="LinkedIn image upload initialization did not return upload URL.",
                source="linkedin",
                retryable=True,
                details={"media_url": media.url},
            )
        upload_response = client.put(
            upload_url,
            content=media.data,
            headers={"Content-Type": media.content_type or "application/octet-stream"},
        )
        upload_response.raise_for_status()
        return _LinkedInAsset(urn=str(image_urn), kind="image", title=media.filename)

    def _upload_document(
        self,
        client: httpx.Client,
        headers: dict[str, str],
        author: str,
        media: _DownloadedMedia,
    ) -> _LinkedInAsset:
        init_response = client.post(
            f"{LINKEDIN_API_BASE}/rest/documents?action=initializeUpload",
            headers=headers,
            json={"initializeUploadRequest": {"owner": author}},
        )
        init_response.raise_for_status()
        value = init_response.json().get("value") or {}
        upload_url = value.get("uploadUrl")
        document_urn = value.get("document")
        if not upload_url or not document_urn:
            raise ExternalApiError(
                code="EXTERNAL_API_LINKEDIN_UPLOAD_INIT_INVALID",
                message="LinkedIn document upload initialization did not return upload URL.",
                source="linkedin",
                retryable=True,
                details={"media_url": media.url},
            )
        upload_response = client.put(
            upload_url,
            content=media.data,
            headers={"Content-Type": media.content_type or "application/pdf"},
        )
        upload_response.raise_for_status()
        return _LinkedInAsset(urn=str(document_urn), kind="document", title=media.filename)

    def _upload_video(
        self,
        client: httpx.Client,
        headers: dict[str, str],
        author: str,
        media: _DownloadedMedia,
    ) -> _LinkedInAsset:
        init_response = client.post(
            f"{LINKEDIN_API_BASE}/rest/videos?action=initializeUpload",
            headers=headers,
            json={
                "initializeUploadRequest": {
                    "owner": author,
                    "fileSizeBytes": len(media.data),
                    "uploadCaptions": False,
                    "uploadThumbnail": False,
                }
            },
        )
        init_response.raise_for_status()
        value = init_response.json().get("value") or {}
        video_urn = value.get("video")
        instructions = value.get("uploadInstructions") or []
        if not video_urn or not isinstance(instructions, list) or not instructions:
            raise ExternalApiError(
                code="EXTERNAL_API_LINKEDIN_UPLOAD_INIT_INVALID",
                message="LinkedIn video upload initialization did not return upload instructions.",
                source="linkedin",
                retryable=True,
                details={"media_url": media.url},
            )
        uploaded_parts: list[str] = []
        for instruction in instructions:
            if not isinstance(instruction, dict):
                continue
            upload_url = instruction.get("uploadUrl")
            first = _instruction_byte_index(instruction, "firstByte", 0)
            last = _instruction_byte_index(
                instruction,
                "lastByte",
                min(first + LINKEDIN_VIDEO_CHUNK_SIZE - 1, len(media.data) - 1),
            )
            if not upload_url:
                continue
            upload_response = client.put(
                upload_url,
                content=media.data[first : last + 1],
                headers={"Content-Type": media.content_type or "application/octet-stream"},
            )
            upload_response.raise_for_status()
            etag = upload_response.headers.get("etag") or upload_response.headers.get("ETag")
            if etag:
                uploaded_parts.append(etag)
        finalize_body = {
            "finalizeUploadRequest": {
                "video": video_urn,
                "uploadToken": value.get("uploadToken") or "",
                "uploadedPartIds": uploaded_parts,
            }
        }
        finalize_response = client.post(
            f"{LINKEDIN_API_BASE}/rest/videos?action=finalizeUpload",
            headers=headers,
            json=finalize_body,
        )
        finalize_response.raise_for_status()
        return _LinkedInAsset(urn=str(video_urn), kind="video", title=media.filename)

    def _content_for_assets(self, assets: list[_LinkedInAsset]) -> dict[str, object]:
        kind = assets[0].kind
        if kind == "image" and len(assets) > 1:
            return {"multiImage": {"images": [{"id": asset.urn} for asset in assets]}}
        return {"media": {"id": assets[0].urn, "title": assets[0].title or "media"}}

    def _credentials_from_env(self) -> LinkedInCredentials:
        return LinkedInCredentials(
            access_token=self.settings.linkedin_access_token or "",
            author_urn=getattr(self.settings, "linkedin_author_urn", None),
            api_version=self.settings.linkedin_api_version,
        )
