"""Publisher for Bluesky posts through AT Protocol XRPC."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from postbridge.api.schemas import BlueskyCredentials
from postbridge.config import Settings, get_settings
from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.domain.models import PostPayload
from postbridge.integrations.bluesky.rule_post_text import BLUESKY_TEXT_LIMIT
from postbridge.integrations.media_download import download_media, media_urls

BLUESKY_DEFAULT_SERVICE = "https://bsky.social"
BLUESKY_MAX_IMAGE_BYTES = 1_000_000
BLUESKY_MAX_IMAGES = 4


def _trim_text(text: str) -> str:
    value = (text or "").strip() or " "
    if len(value) <= BLUESKY_TEXT_LIMIT:
        return value
    return value[: BLUESKY_TEXT_LIMIT - 1].rstrip() + "..."


class BlueskyPublisher:
    """Client for publishing text posts to Bluesky."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def publish_post(
        self,
        target_channel: str,
        payload: PostPayload,
        credentials: BlueskyCredentials | None = None,
    ) -> str | None:
        creds = credentials or self._credentials_from_env()
        if not creds.identifier or not creds.app_password:
            raise ConfigurationError("Missing Bluesky identifier or app password.")
        service_url = (creds.service_url or self.settings.bluesky_service_url).rstrip("/")
        try:
            with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
                session = client.post(
                    f"{service_url}/xrpc/com.atproto.server.createSession",
                    json={"identifier": creds.identifier, "password": creds.app_password},
                )
                session.raise_for_status()
                session_data = session.json()
                access_jwt = session_data.get("accessJwt")
                did = session_data.get("did")
                if not access_jwt or not did:
                    raise ExternalApiError(
                        code="EXTERNAL_API_BLUESKY_AUTH_ERROR",
                        message="Bluesky session response did not include accessJwt and did.",
                        source="bluesky",
                        retryable=False,
                        details={"target_channel": target_channel},
                    )
                record: dict[str, object] = {
                    "$type": "app.bsky.feed.post",
                    "text": _trim_text(payload.text),
                    "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                }
                images = self._upload_images(
                    client=client,
                    service_url=service_url,
                    access_jwt=access_jwt,
                    payload=payload,
                    target_channel=target_channel,
                )
                if images:
                    record["embed"] = {
                        "$type": "app.bsky.embed.images",
                        "images": images,
                    }
                response = client.post(
                    f"{service_url}/xrpc/com.atproto.repo.createRecord",
                    headers={"Authorization": f"Bearer {access_jwt}"},
                    json={
                        "repo": did,
                        "collection": "app.bsky.feed.post",
                        "record": record,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _bluesky_error(exc, target_channel) from exc
        except httpx.RequestError as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_BLUESKY_REQUEST_ERROR",
                message="Bluesky API transport error",
                source="bluesky",
                retryable=True,
                details={"target_channel": target_channel},
            ) from exc
        data_obj = response.json()
        uri = data_obj.get("uri") if isinstance(data_obj, dict) else None
        return str(uri) if uri is not None else None

    def _credentials_from_env(self) -> BlueskyCredentials:
        return BlueskyCredentials(
            identifier=self.settings.bluesky_identifier or "",
            app_password=self.settings.bluesky_app_password or "",
            service_url=self.settings.bluesky_service_url,
        )

    def _upload_images(
        self,
        *,
        client: httpx.Client,
        service_url: str,
        access_jwt: str,
        payload: PostPayload,
        target_channel: str,
    ) -> list[dict[str, object]]:
        images: list[dict[str, object]] = []
        for url in media_urls(payload, limit=BLUESKY_MAX_IMAGES):
            media = download_media(
                client,
                url,
                source="bluesky",
                target_channel=target_channel,
                max_bytes=BLUESKY_MAX_IMAGE_BYTES,
            )
            if not media.content_type.lower().split(";", 1)[0].startswith("image/"):
                raise ExternalApiError(
                    code="EXTERNAL_API_BLUESKY_MEDIA_TYPE_UNSUPPORTED",
                    message="Bluesky publishing supports image media uploads.",
                    source="bluesky",
                    retryable=False,
                    details={"target_channel": target_channel, "media_url": url[:500]},
                )
            response = client.post(
                f"{service_url}/xrpc/com.atproto.repo.uploadBlob",
                headers={
                    "Authorization": f"Bearer {access_jwt}",
                    "Content-Type": media.content_type,
                },
                content=media.data,
            )
            response.raise_for_status()
            data_obj = response.json()
            blob = data_obj.get("blob") if isinstance(data_obj, dict) else None
            if blob is not None:
                images.append({"alt": "", "image": blob})
        return images


def _bluesky_error(exc: httpx.HTTPStatusError, target_channel: str) -> ExternalApiError:
    message = str(exc)
    details: dict[str, object] = {
        "status_code": exc.response.status_code,
        "target_channel": target_channel,
    }
    try:
        body = exc.response.json()
        if isinstance(body, dict):
            details["bluesky_error"] = body
            message = str(body.get("message") or body.get("error") or message)
    except Exception:
        pass
    return ExternalApiError(
        code="EXTERNAL_API_BLUESKY_HTTP_ERROR",
        message=f"Bluesky API error: {message}",
        source="bluesky",
        retryable=exc.response.status_code in (408, 409, 425, 429, 500, 502, 503, 504),
        details=details,
    )
