"""Publisher for Mastodon statuses."""

from __future__ import annotations

import httpx

from postbridge.api.schemas import MastodonCredentials
from postbridge.config import Settings, get_settings
from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.domain.models import PostPayload
from postbridge.integrations.media_download import download_media, media_urls
from postbridge.integrations.mastodon.rule_post_text import MASTODON_TEXT_LIMIT

_VALID_VISIBILITY = {"public", "unlisted", "private", "direct"}
MASTODON_MAX_MEDIA_BYTES = 100 * 1024 * 1024
MASTODON_MAX_MEDIA_ITEMS = 4


def _trim_text(text: str) -> str:
    value = (text or "").strip() or " "
    if len(value) <= MASTODON_TEXT_LIMIT:
        return value
    return value[: MASTODON_TEXT_LIMIT - 1].rstrip() + "..."


class MastodonPublisher:
    """Client for publishing text statuses to a Mastodon instance."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def publish_post(
        self,
        target_channel: str,
        payload: PostPayload,
        credentials: MastodonCredentials | None = None,
    ) -> str | None:
        creds = credentials or self._credentials_from_env()
        instance_url = (creds.instance_url or self.settings.mastodon_instance_url or "").rstrip("/")
        if not creds.access_token or not instance_url:
            raise ConfigurationError("Missing Mastodon access token or instance URL.")
        visibility = creds.visibility or self.settings.mastodon_visibility or "public"
        if visibility not in _VALID_VISIBILITY:
            raise ConfigurationError(
                "MASTODON_VISIBILITY must be one of: public, unlisted, private, direct."
            )
        try:
            with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
                media_ids = self._upload_media(
                    client=client,
                    instance_url=instance_url,
                    access_token=creds.access_token,
                    payload=payload,
                    target_channel=target_channel,
                )
                data: list[tuple[str, str]] = [
                    ("status", _trim_text(payload.text)),
                    ("visibility", visibility),
                ]
                data.extend(("media_ids[]", media_id) for media_id in media_ids)
                response = client.post(
                    f"{instance_url}/api/v1/statuses",
                    headers={"Authorization": f"Bearer {creds.access_token}"},
                    data=data,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _mastodon_error(exc, target_channel) from exc
        except httpx.RequestError as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_MASTODON_REQUEST_ERROR",
                message="Mastodon API transport error",
                source="mastodon",
                retryable=True,
                details={"target_channel": target_channel},
            ) from exc
        data_obj = response.json()
        status_id = data_obj.get("id") if isinstance(data_obj, dict) else None
        return str(status_id) if status_id is not None else None

    def _credentials_from_env(self) -> MastodonCredentials:
        return MastodonCredentials(
            access_token=self.settings.mastodon_access_token or "",
            instance_url=self.settings.mastodon_instance_url,
            visibility=self.settings.mastodon_visibility,
        )

    def _upload_media(
        self,
        *,
        client: httpx.Client,
        instance_url: str,
        access_token: str,
        payload: PostPayload,
        target_channel: str,
    ) -> list[str]:
        ids: list[str] = []
        for url in media_urls(payload, limit=MASTODON_MAX_MEDIA_ITEMS):
            media = download_media(
                client,
                url,
                source="mastodon",
                target_channel=target_channel,
                max_bytes=MASTODON_MAX_MEDIA_BYTES,
            )
            response = client.post(
                f"{instance_url}/api/v2/media",
                headers={"Authorization": f"Bearer {access_token}"},
                files={"file": (media.filename, media.data, media.content_type)},
            )
            response.raise_for_status()
            data_obj = response.json()
            media_id = data_obj.get("id") if isinstance(data_obj, dict) else None
            if media_id is not None:
                ids.append(str(media_id))
        return ids


def _mastodon_error(exc: httpx.HTTPStatusError, target_channel: str) -> ExternalApiError:
    message = str(exc)
    details: dict[str, object] = {
        "status_code": exc.response.status_code,
        "target_channel": target_channel,
    }
    try:
        body = exc.response.json()
        if isinstance(body, dict):
            details["mastodon_error"] = body
            message = str(body.get("error") or message)
    except Exception:
        pass
    return ExternalApiError(
        code="EXTERNAL_API_MASTODON_HTTP_ERROR",
        message=f"Mastodon API error: {message}",
        source="mastodon",
        retryable=exc.response.status_code in (408, 409, 425, 429, 500, 502, 503, 504),
        details=details,
    )
