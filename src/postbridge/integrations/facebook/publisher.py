"""Publisher for Facebook Pages posts through Meta Graph API."""

from __future__ import annotations

import httpx

from postbridge.api.schemas import FacebookCredentials
from postbridge.config import Settings, get_settings
from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.domain.models import PostPayload
from postbridge.integrations.media_download import media_urls

FACEBOOK_GRAPH_BASE = "https://graph.facebook.com"
FACEBOOK_TEXT_LIMIT = 63206
FACEBOOK_MAX_ATTACHED_MEDIA = 10


def _trim_text(text: str) -> str:
    value = (text or "").strip() or " "
    if len(value) <= FACEBOOK_TEXT_LIMIT:
        return value
    return value[: FACEBOOK_TEXT_LIMIT - 1].rstrip() + "..."


def _page_id(target_channel: str, credentials: FacebookCredentials) -> str:
    raw = (credentials.page_id or target_channel or "").strip()
    if raw.startswith("facebook/page/"):
        raw = raw.rsplit("/", 1)[-1].strip()
    if not raw:
        raise ConfigurationError("Facebook target_channel or credentials.page_id is required.")
    return raw


class FacebookPublisher:
    """Client for publishing posts to Facebook Pages."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def publish_post(
        self,
        target_channel: str,
        payload: PostPayload,
        credentials: FacebookCredentials | None = None,
    ) -> str | None:
        creds = credentials or self._credentials_from_env()
        if not creds.page_access_token:
            raise ConfigurationError("Missing Facebook page access token for publishing.")
        page_id = _page_id(target_channel, creds)
        version = creds.graph_api_version or self.settings.meta_graph_api_version
        try:
            with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
                response = self._publish(
                    client=client,
                    version=version,
                    page_id=page_id,
                    payload=payload,
                    token=creds.page_access_token,
                )
        except httpx.HTTPStatusError as exc:
            raise _facebook_error(exc, "facebook", target_channel) from exc
        except httpx.RequestError as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_FACEBOOK_REQUEST_ERROR",
                message="Facebook Graph API transport error",
                source="facebook",
                retryable=True,
                details={"target_channel": target_channel},
            ) from exc
        data_obj = response.json()
        post_id = data_obj.get("id") if isinstance(data_obj, dict) else None
        return str(post_id) if post_id is not None else None

    def _publish(
        self,
        *,
        client: httpx.Client,
        version: str,
        page_id: str,
        payload: PostPayload,
        token: str,
    ) -> httpx.Response:
        urls = media_urls(payload, limit=FACEBOOK_MAX_ATTACHED_MEDIA)
        text = _trim_text(payload.text)
        if not urls:
            response = client.post(
                f"{FACEBOOK_GRAPH_BASE}/{version}/{page_id}/feed",
                data={"message": text, "access_token": token},
            )
            response.raise_for_status()
            return response
        if len(urls) == 1:
            media_url = urls[0]
            if _is_video_url(media_url):
                response = client.post(
                    f"{FACEBOOK_GRAPH_BASE}/{version}/{page_id}/videos",
                    data={
                        "file_url": media_url,
                        "description": text,
                        "access_token": token,
                    },
                )
            else:
                response = client.post(
                    f"{FACEBOOK_GRAPH_BASE}/{version}/{page_id}/photos",
                    data={
                        "url": media_url,
                        "caption": text,
                        "published": "true",
                        "access_token": token,
                    },
                )
            response.raise_for_status()
            return response

        attached_media: list[str] = []
        for media_url in urls:
            if _is_video_url(media_url):
                raise ConfigurationError(
                    "Facebook multi-media publishing supports photos only; use one video per post."
                )
            photo = client.post(
                f"{FACEBOOK_GRAPH_BASE}/{version}/{page_id}/photos",
                data={
                    "url": media_url,
                    "published": "false",
                    "access_token": token,
                },
            )
            photo.raise_for_status()
            photo_data = photo.json()
            photo_id = photo_data.get("id") if isinstance(photo_data, dict) else None
            if photo_id is not None:
                attached_media.append(f'{{"media_fbid":"{photo_id}"}}')
        response = client.post(
            f"{FACEBOOK_GRAPH_BASE}/{version}/{page_id}/feed",
            data={
                "message": text,
                "access_token": token,
                **{f"attached_media[{idx}]": value for idx, value in enumerate(attached_media)},
            },
        )
        response.raise_for_status()
        return response

    def _credentials_from_env(self) -> FacebookCredentials:
        return FacebookCredentials(
            page_access_token=self.settings.facebook_page_access_token or "",
            page_id=self.settings.facebook_page_id,
            graph_api_version=self.settings.meta_graph_api_version,
        )


def _is_video_url(url: str) -> bool:
    path = url.lower().split("?", 1)[0]
    return path.endswith((".mp4", ".mov", ".m4v", ".webm"))


def _facebook_error(
    exc: httpx.HTTPStatusError,
    source: str,
    target_channel: str,
) -> ExternalApiError:
    message = str(exc)
    details: dict[str, object] = {
        "status_code": exc.response.status_code,
        "target_channel": target_channel,
    }
    try:
        body = exc.response.json()
        if isinstance(body, dict):
            details["meta_error"] = body
            err = body.get("error")
            if isinstance(err, dict):
                message = str(err.get("message") or message)
    except Exception:
        pass
    return ExternalApiError(
        code=f"EXTERNAL_API_{source.upper()}_HTTP_ERROR",
        message=f"{source.title()} API error: {message}",
        source=source,
        retryable=exc.response.status_code in (408, 409, 425, 429, 500, 502, 503, 504),
        details=details,
    )
