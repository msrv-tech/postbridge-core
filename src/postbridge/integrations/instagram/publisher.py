"""Publisher for Instagram Business content through Meta Graph API."""

from __future__ import annotations

import time

import httpx

from postbridge.api.schemas import InstagramCredentials
from postbridge.config import Settings, get_settings
from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.domain.models import PostPayload
from postbridge.integrations.instagram.rule_post_text import INSTAGRAM_CAPTION_LIMIT
from postbridge.integrations.media_download import media_urls

INSTAGRAM_GRAPH_BASE = "https://graph.facebook.com"
INSTAGRAM_MAX_CAROUSEL_ITEMS = 10
INSTAGRAM_CONTAINER_POLL_ATTEMPTS = 12
INSTAGRAM_CONTAINER_POLL_DELAY_SECONDS = 5.0


def _trim_caption(text: str) -> str:
    value = (text or "").strip()
    if len(value) <= INSTAGRAM_CAPTION_LIMIT:
        return value
    return value[: INSTAGRAM_CAPTION_LIMIT - 1].rstrip() + "..."


def _instagram_user_id(target_channel: str, credentials: InstagramCredentials) -> str:
    raw = (credentials.instagram_user_id or target_channel or "").strip()
    if raw.startswith("instagram/"):
        raw = raw.rsplit("/", 1)[-1].strip()
    if not raw:
        raise ConfigurationError(
            "Instagram target_channel or credentials.instagram_user_id is required."
        )
    return raw


class InstagramPublisher:
    """Client for publishing Instagram Business feed media."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def publish_video(self, target_channel, payload, credentials, delivery):
        from postbridge.services.video_delivery_state import DeliveryPending, VideoDeliveryReceipt, check_response, unknown
        creds = credentials or self._credentials_from_env()
        user_id = _instagram_user_id(target_channel, creds)
        if user_id != target_channel.removeprefix("instagram/"):
            raise ConfigurationError("Connect publishing credentials for this Instagram account")
        if not creds.access_token or payload.publication_format != "reel" or len(payload.text) > INSTAGRAM_CAPTION_LIMIT:
            raise ConfigurationError("Review this Instagram Reel and connect its publishing credentials")
        version = creds.graph_api_version or self.settings.meta_graph_api_version
        base = f"{INSTAGRAM_GRAPH_BASE}/{version}"
        try:
            with httpx.Client(timeout=httpx.Timeout(90, connect=15), follow_redirects=False,
                    headers={"Authorization": f"Bearer {creds.access_token}"}) as client:
                state = delivery.state
                if not state or state.get("phase") == "rejected":
                    delivery.save(phase="creating")
                    create = client.post(f"{base}/{user_id}/media", data={"media_type": "REELS",
                        "video_url": payload.media_url, "caption": payload.text, "share_to_feed": "true"})
                    if 400 <= create.status_code < 500 and create.status_code not in {408, 409, 425}:
                        delivery.save(phase="rejected")
                    check_response(create, "instagram")
                    container_id = create.json().get("id")
                    if not isinstance(container_id, str) or not container_id.isdigit():
                        raise unknown("instagram")
                    delivery.save(phase="processing", container_id=container_id)
                state = delivery.state
                if state.get("phase") == "creating":
                    raise unknown("instagram", "The container response was lost; no automatic recreation is allowed")
                if state.get("external_id"):
                    return self._video_receipt(client, base, creds.access_token, delivery)
                result = client.get(f"{base}/{state['container_id']}", params={"fields": "status_code"})
                check_response(result, "instagram")
                status = str(result.json().get("status_code") or "").upper()
                if status in {"ERROR", "EXPIRED"}:
                    raise ExternalApiError(code="EXTERNAL_API_INSTAGRAM_CONTAINER_ERROR", message="Instagram could not process the Reel",
                        source="instagram", retryable=False, details={})
                if state.get("phase") == "publishing" or status == "PUBLISHED":
                    raise unknown("instagram", "Instagram may have published this Reel; verify the destination before any resend")
                if status != "FINISHED":
                    raise DeliveryPending()
                delivery.save(phase="publishing")
                publish = client.post(f"{base}/{user_id}/media_publish", data={"creation_id": state["container_id"]})
                if 400 <= publish.status_code < 500 and publish.status_code not in {408, 409, 425}:
                    delivery.save(phase="processing")  # Explicit rejection: same container can be checked again.
                check_response(publish, "instagram")
                media_id = publish.json().get("id")
                if not isinstance(media_id, str) or not media_id.isdigit():
                    raise unknown("instagram")
                delivery.save(phase="published", external_id=media_id)
                return self._video_receipt(client, base, creds.access_token, delivery)
        except httpx.RequestError:
            raise unknown("instagram") from None

    def _video_receipt(self, client, base, token, delivery):
        from postbridge.services.video_delivery_state import VideoDeliveryReceipt
        from urllib.parse import urlparse
        state = delivery.state
        if not state.get("external_url"):
            try:
                response = client.get(f"{base}/{state['external_id']}", params={"fields": "permalink"}, headers={"Authorization": f"Bearer {token}"})
                url = response.json().get("permalink") if response.is_success else None
                parsed = urlparse(url or "")
                if parsed.scheme == "https" and parsed.hostname in {"www.instagram.com", "instagram.com"} and not parsed.username and not parsed.password:
                    delivery.save(external_url=url)
            except (httpx.RequestError, ValueError):
                pass  # The published media ID is already authoritative; never resend for a missing link.
        return VideoDeliveryReceipt(state["external_id"], state.get("external_url"))

    def publish_post(
        self,
        target_channel: str,
        payload: PostPayload,
        credentials: InstagramCredentials | None = None,
    ) -> str | None:
        creds = credentials or self._credentials_from_env()
        if not creds.access_token:
            raise ConfigurationError("Missing Instagram access token for publishing.")
        user_id = _instagram_user_id(target_channel, creds)
        version = creds.graph_api_version or self.settings.meta_graph_api_version
        urls = media_urls(payload, limit=INSTAGRAM_MAX_CAROUSEL_ITEMS)
        if not urls:
            raise ConfigurationError("Instagram publishing requires an image or video media_url.")
        try:
            with httpx.Client(timeout=httpx.Timeout(90.0, connect=15.0)) as client:
                creation_id = self._create_container(
                    client=client,
                    version=version,
                    user_id=user_id,
                    urls=urls,
                    caption=_trim_caption(payload.text),
                    token=creds.access_token,
                    target_channel=target_channel,
                )
                self._wait_for_container(
                    client=client,
                    version=version,
                    creation_id=creation_id,
                    token=creds.access_token,
                    target_channel=target_channel,
                )
                publish = client.post(
                    f"{INSTAGRAM_GRAPH_BASE}/{version}/{user_id}/media_publish",
                    data={"creation_id": creation_id, "access_token": creds.access_token},
                )
                publish.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _instagram_error(exc, target_channel) from exc
        except httpx.RequestError as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_INSTAGRAM_REQUEST_ERROR",
                message="Instagram Graph API transport error",
                source="instagram",
                retryable=True,
                details={"target_channel": target_channel},
            ) from exc
        data_obj = publish.json()
        post_id = data_obj.get("id") if isinstance(data_obj, dict) else None
        return str(post_id) if post_id is not None else None

    def _create_container(
        self,
        *,
        client: httpx.Client,
        version: str,
        user_id: str,
        urls: list[str],
        caption: str,
        token: str,
        target_channel: str,
    ) -> str:
        if len(urls) == 1:
            creation_id = self._post_media_container(
                client=client,
                version=version,
                user_id=user_id,
                data={
                    "caption": caption,
                    "access_token": token,
                    **({"media_type": "REELS"} if _is_video_url(urls[0]) else {}),
                    ("video_url" if _is_video_url(urls[0]) else "image_url"): urls[0],
                },
                target_channel=target_channel,
            )
            return creation_id

        children: list[str] = []
        for url in urls:
            if _is_video_url(url):
                raise ConfigurationError(
                    "Instagram carousel publishing supports image URLs in Core today."
                )
            child_id = self._post_media_container(
                client=client,
                version=version,
                user_id=user_id,
                data={
                    "image_url": url,
                    "is_carousel_item": "true",
                    "access_token": token,
                },
                target_channel=target_channel,
            )
            self._wait_for_container(
                client=client,
                version=version,
                creation_id=child_id,
                token=token,
                target_channel=target_channel,
            )
            children.append(child_id)
        return self._post_media_container(
            client=client,
            version=version,
            user_id=user_id,
            data={
                "media_type": "CAROUSEL",
                "children": ",".join(children),
                "caption": caption,
                "access_token": token,
            },
            target_channel=target_channel,
        )

    def _post_media_container(
        self,
        *,
        client: httpx.Client,
        version: str,
        user_id: str,
        data: dict[str, str],
        target_channel: str,
    ) -> str:
        create = client.post(
            f"{INSTAGRAM_GRAPH_BASE}/{version}/{user_id}/media",
            data=data,
        )
        create.raise_for_status()
        create_data = create.json()
        creation_id = create_data.get("id") if isinstance(create_data, dict) else None
        if not creation_id:
            raise ExternalApiError(
                code="EXTERNAL_API_INSTAGRAM_CREATE_ERROR",
                message="Instagram media container response did not include id.",
                source="instagram",
                retryable=False,
                details={"target_channel": target_channel},
            )
        return str(creation_id)

    def _wait_for_container(
        self,
        *,
        client: httpx.Client,
        version: str,
        creation_id: str,
        token: str,
        target_channel: str,
    ) -> None:
        for attempt in range(INSTAGRAM_CONTAINER_POLL_ATTEMPTS):
            response = client.get(
                f"{INSTAGRAM_GRAPH_BASE}/{version}/{creation_id}",
                params={"fields": "status_code", "access_token": token},
            )
            response.raise_for_status()
            data = response.json()
            status = str(data.get("status_code") or "").upper() if isinstance(data, dict) else ""
            if status in {"FINISHED", "PUBLISHED"}:
                return
            if status == "ERROR":
                raise ExternalApiError(
                    code="EXTERNAL_API_INSTAGRAM_CONTAINER_ERROR",
                    message="Instagram media container processing failed.",
                    source="instagram",
                    retryable=False,
                    details={"target_channel": target_channel, "creation_id": creation_id},
                )
            if attempt + 1 < INSTAGRAM_CONTAINER_POLL_ATTEMPTS:
                time.sleep(INSTAGRAM_CONTAINER_POLL_DELAY_SECONDS)
        raise ExternalApiError(
            code="EXTERNAL_API_INSTAGRAM_CONTAINER_TIMEOUT",
            message="Instagram media container was not ready before the publish timeout.",
            source="instagram",
            retryable=True,
            details={"target_channel": target_channel, "creation_id": creation_id},
        )

    def _credentials_from_env(self) -> InstagramCredentials:
        return InstagramCredentials(
            access_token=self.settings.instagram_access_token or "",
            instagram_user_id=self.settings.instagram_user_id,
            graph_api_version=self.settings.meta_graph_api_version,
        )


def _is_video_url(url: str) -> bool:
    path = url.lower().split("?", 1)[0]
    return path.endswith((".mp4", ".mov", ".m4v"))


def _instagram_error(exc: httpx.HTTPStatusError, target_channel: str) -> ExternalApiError:
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
        code="EXTERNAL_API_INSTAGRAM_HTTP_ERROR",
        message=f"Instagram API error: {message}",
        source="instagram",
        retryable=exc.response.status_code in (408, 409, 425, 429, 500, 502, 503, 504),
        details=details,
    )
