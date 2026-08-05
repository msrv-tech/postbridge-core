"""Publisher for X posts through X API v2."""

from __future__ import annotations

import httpx

from postbridge.api.schemas import XCredentials
from postbridge.config import Settings, get_settings
from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.domain.models import PostPayload
from postbridge.integrations.media_download import download_media, media_urls
from postbridge.integrations.x.rule_post_text import X_TEXT_LIMIT

X_API_BASE = "https://api.x.com"
X_MAX_MEDIA_BYTES = 512 * 1024 * 1024
X_MAX_MEDIA_ITEMS = 4


def _trim_text(text: str) -> str:
    value = (text or "").strip() or " "
    if len(value) <= X_TEXT_LIMIT:
        return value
    return value[: X_TEXT_LIMIT - 1].rstrip() + "..."


class XPublisher:
    """Client for publishing text posts to X."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def publish_post(
        self,
        target_channel: str,
        payload: PostPayload,
        credentials: XCredentials | None = None,
    ) -> str | None:
        creds = credentials or self._credentials_from_env()
        if not creds.access_token:
            raise ConfigurationError("Missing X access token for publishing.")
        headers = {
            "Authorization": f"Bearer {creds.access_token}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
                media_ids = self._upload_media(
                    client=client,
                    headers=headers,
                    payload=payload,
                    target_channel=target_channel,
                )
                body: dict[str, object] = {"text": _trim_text(payload.text)}
                if media_ids:
                    body["media"] = {"media_ids": media_ids}
                response = client.post(
                    f"{X_API_BASE}/2/tweets",
                    headers=headers,
                    json=body,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _x_error(exc, target_channel) from exc
        except httpx.RequestError as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_X_REQUEST_ERROR",
                message="X API transport error",
                source="x",
                retryable=True,
                details={"target_channel": target_channel},
            ) from exc
        data_obj = response.json()
        data = data_obj.get("data") if isinstance(data_obj, dict) else None
        post_id = data.get("id") if isinstance(data, dict) else None
        return str(post_id) if post_id is not None else None

    def _credentials_from_env(self) -> XCredentials:
        return XCredentials(access_token=self.settings.x_access_token or "")

    def _upload_media(
        self,
        *,
        client: httpx.Client,
        headers: dict[str, str],
        payload: PostPayload,
        target_channel: str,
    ) -> list[str]:
        ids: list[str] = []
        for url in media_urls(payload, limit=X_MAX_MEDIA_ITEMS):
            media = download_media(
                client,
                url,
                source="x",
                target_channel=target_channel,
                max_bytes=X_MAX_MEDIA_BYTES,
            )
            response = client.post(
                f"{X_API_BASE}/2/media/upload",
                headers={"Authorization": headers["Authorization"]},
                files={"media": (media.filename, media.data, media.content_type)},
            )
            response.raise_for_status()
            data_obj = response.json()
            data = data_obj.get("data") if isinstance(data_obj, dict) else None
            media_id = (
                data.get("id")
                if isinstance(data, dict)
                else data_obj.get("media_id_string") if isinstance(data_obj, dict) else None
            )
            if media_id is not None:
                ids.append(str(media_id))
        return ids


def _x_error(exc: httpx.HTTPStatusError, target_channel: str) -> ExternalApiError:
    message = str(exc)
    details: dict[str, object] = {
        "status_code": exc.response.status_code,
        "target_channel": target_channel,
    }
    try:
        body = exc.response.json()
        if isinstance(body, dict):
            details["x_error"] = body
            message = str(body.get("detail") or body.get("title") or message)
    except Exception:
        pass
    return ExternalApiError(
        code="EXTERNAL_API_X_HTTP_ERROR",
        message=f"X API error: {message}",
        source="x",
        retryable=exc.response.status_code in (408, 409, 425, 429, 500, 502, 503, 504),
        details=details,
    )
