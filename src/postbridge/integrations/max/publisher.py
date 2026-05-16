"""Publisher для публикации постов в MAX API (platform-api.max.ru)."""

import io
import logging
import re
from urllib.parse import urlparse

import requests

from postbridge.api.schemas import MaxCredentials
from postbridge.config import Settings, get_settings
from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.domain.models import PostPayload

logger = logging.getLogger(__name__)

_UPLOAD_TYPE_BY_EXT: dict[str, str] = {
    "jpg": "image",
    "jpeg": "image",
    "png": "image",
    "gif": "image",
    "webp": "image",
    "tiff": "image",
    "bmp": "image",
    "heic": "image",
    "mp4": "video",
    "mov": "video",
    "mkv": "video",
    "webm": "video",
    "mp3": "audio",
    "wav": "audio",
    "m4a": "audio",
    "ogg": "audio",
}


def _is_numeric_chat_id(value: str) -> bool:
    return bool(re.match(r"^-?\d+$", value.strip()))


class MaxPublisher:
    """Клиент для публикации постов в MAX API (platform-api.max.ru)."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def publish_post(
        self,
        target_channel: str,
        payload: PostPayload,
        credentials: MaxCredentials | None = None,
    ) -> str | None:
        creds = credentials or self._credentials_from_env()
        if not creds.base_url or not creds.token:
            raise ConfigurationError(
                "Missing MAX_API_BASE_URL or MAX_API_TOKEN for MAX publishing."
            )

        chat_id = self._resolve_chat_id(creds, target_channel)
        url = f"{creds.base_url.rstrip('/')}/messages"
        headers = {
            "Authorization": creds.token,
            "Content-Type": "application/json",
        }
        body = self._build_message_body(creds, payload)
        params = {"chat_id": chat_id}

        try:
            response = requests.post(
                url,
                params=params,
                json=body,
                headers=headers,
                timeout=self.settings.max_api_timeout_seconds,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            resp = exc.response
            raise ExternalApiError(
                code="EXTERNAL_API_MAX_HTTP_ERROR",
                message="MAX API request failed",
                source="max",
                retryable=resp.status_code >= 500 or resp.status_code == 429,
                details={
                    "status_code": resp.status_code,
                    "target_channel": target_channel,
                },
            ) from exc
        except requests.RequestException as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_MAX_REQUEST_ERROR",
                message="MAX API transport error",
                source="max",
                retryable=True,
                details={"target_channel": target_channel},
            ) from exc

        data = response.json()
        if not isinstance(data, dict):
            return None
        msg = data.get("message")
        if isinstance(msg, dict):
            body = msg.get("body")
            if isinstance(body, dict):
                mid = body.get("mid")
                if mid:
                    return str(mid)
            mid = msg.get("id") or msg.get("message_id")
            if mid is not None:
                return str(mid)
        mid = data.get("id") or data.get("message_id")
        if mid is not None:
            return str(mid)
        return None

    def _resolve_chat_id(self, creds: MaxCredentials, target_channel: str) -> int:
        if _is_numeric_chat_id(target_channel):
            return int(target_channel.strip())

        chat_id = self._find_chat_by_username(creds, target_channel.strip())
        if chat_id is not None:
            return chat_id

        raise ConfigurationError(
            f"MAX chat not found for target '{target_channel}'. "
            "Ensure the bot is added to the chat, or use numeric chat_id."
        )

    def _find_chat_by_username(self, creds: MaxCredentials, username: str) -> int | None:
        url = f"{creds.base_url.rstrip('/')}/chats"
        headers = {"Authorization": creds.token}
        marker = None
        username_lower = username.lower()

        while True:
            params = {"count": 100}
            if marker is not None:
                params["marker"] = marker

            try:
                resp = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.settings.max_api_timeout_seconds,
                )
                resp.raise_for_status()
            except requests.RequestException:
                return None

            data = resp.json()
            chats = data.get("chats") or []

            for chat in chats:
                cid = chat.get("chat_id")
                link = (chat.get("link") or "").lower()
                title = (chat.get("title") or "").lower()
                if (
                    username_lower in link
                    or username_lower in title
                    or (link.endswith(f"/{username_lower}") or f"/{username_lower}" in link)
                ):
                    return cid

            marker = data.get("marker")
            if marker is None:
                break

        return None

    def _upload_media_from_url(
        self, creds: MaxCredentials, media_url: str
    ) -> tuple[str, str] | None:
        try:
            resp = requests.get(media_url, timeout=30)
            resp.raise_for_status()
            data = resp.content
        except requests.RequestException as e:
            logger.warning("Failed to fetch media from %s: %s", media_url[:80], e)
            return None

        path = (urlparse(media_url).path or "").strip("/")
        path_parts = path.split("/")
        segment = path_parts[-1] if path_parts else ""
        seg_parts = segment.split("_", 2)
        url_filename = seg_parts[2] if len(seg_parts) >= 3 else segment
        ext = url_filename.split(".")[-1].lower() if "." in url_filename else "bin"
        upload_type = _UPLOAD_TYPE_BY_EXT.get(ext, "file")

        uploads_url = f"{creds.base_url.rstrip('/')}/uploads"
        try:
            r = requests.post(
                uploads_url,
                params={"type": upload_type},
                headers={"Authorization": creds.token},
                timeout=15,
            )
            r.raise_for_status()
            upload_info = r.json()
            upload_url = upload_info.get("url")
            if not upload_url:
                logger.warning("MAX /uploads response missing url: %s", upload_info)
                return None
        except requests.RequestException as e:
            logger.warning("MAX /uploads failed: %s", e)
            return None

        filename = url_filename if url_filename else (f"file.{ext}" if ext and ext != "bin" else "file")
        mime = "application/pdf" if ext == "pdf" else "application/octet-stream"
        file_obj = io.BytesIO(data)
        try:
            upload_resp = requests.post(
                upload_url,
                files={"data": (filename, file_obj, mime)},
                timeout=60,
            )
            upload_resp.raise_for_status()
            result = upload_resp.json()
            token = result.get("token")
            if not token:
                logger.warning("MAX upload response missing token: %s", result)
                return None
            return (token, upload_type)
        except requests.RequestException as e:
            err_detail = ""
            if hasattr(e, "response") and e.response is not None:
                try:
                    err_detail = f" body={e.response.text[:200]}"
                except Exception:
                    pass
            logger.warning("MAX file upload failed: %s%s", e, err_detail)
            return None

    def _build_message_body(self, creds: MaxCredentials, payload: PostPayload) -> dict:
        text = payload.text or ""
        attachments: list[dict] = []

        urls: list[str] = []
        if payload.media_urls:
            urls = list(payload.media_urls)
        elif payload.media_url:
            urls = [payload.media_url]

        for media_url in urls:
            ext = (urlparse(media_url).path or "").split(".")[-1].lower()
            upload_type = _UPLOAD_TYPE_BY_EXT.get(ext, "file")

            if upload_type == "image":
                attachments.append({"type": "image", "payload": {"url": media_url}})
            else:
                uploaded = self._upload_media_from_url(creds, media_url)
                if uploaded:
                    token, _ = uploaded
                    attachments.append({"type": upload_type, "payload": {"token": token}})
                else:
                    logger.warning("Media upload failed for type=%s url=%s", upload_type, media_url[:80])

        body: dict = {"text": text}
        if attachments:
            body["attachments"] = attachments
        return body

    def edit_message(
        self,
        message_id: str,
        text: str = "",
        media_url: str | None = None,
        media_urls: list[str] | None = None,
        credentials: MaxCredentials | None = None,
        target_channel: str | None = None,
    ) -> None:
        creds = credentials or self._credentials_from_env()
        if not creds.base_url or not creds.token:
            raise ConfigurationError(
                "Missing MAX_API_BASE_URL or MAX_API_TOKEN for MAX publishing."
            )
        url = f"{creds.base_url.rstrip('/')}/messages"
        headers = {
            "Authorization": creds.token,
            "Content-Type": "application/json",
        }
        params = {"message_id": message_id}
        body = self._build_message_body(
            creds,
            PostPayload(source_post_id="", text=text, media_url=media_url, media_urls=media_urls),
        )
        try:
            response = requests.put(
                url,
                params=params,
                json=body,
                headers=headers,
                timeout=self.settings.max_api_timeout_seconds,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            resp = exc.response
            raise ExternalApiError(
                code="EXTERNAL_API_MAX_HTTP_ERROR",
                message="MAX API edit message failed",
                source="max",
                retryable=resp.status_code >= 500 or resp.status_code == 429,
                details={
                    "status_code": resp.status_code,
                    "message_id": message_id,
                },
            ) from exc
        except requests.RequestException as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_MAX_REQUEST_ERROR",
                message="MAX API transport error",
                source="max",
                retryable=True,
                details={"message_id": message_id},
            ) from exc

    def delete_message(
        self,
        message_id: str,
        credentials: MaxCredentials | None = None,
    ) -> None:
        creds = credentials or self._credentials_from_env()
        if not creds.base_url or not creds.token:
            raise ConfigurationError(
                "Missing MAX_API_BASE_URL or MAX_API_TOKEN for MAX publishing."
            )
        url = f"{creds.base_url.rstrip('/')}/messages"
        headers = {"Authorization": creds.token}
        params = {"message_id": message_id}
        try:
            response = requests.delete(
                url,
                params=params,
                headers=headers,
                timeout=self.settings.max_api_timeout_seconds,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            resp = exc.response
            raise ExternalApiError(
                code="EXTERNAL_API_MAX_HTTP_ERROR",
                message="MAX API delete message failed",
                source="max",
                retryable=resp.status_code >= 500 or resp.status_code == 429,
                details={
                    "status_code": resp.status_code,
                    "message_id": message_id,
                },
            ) from exc
        except requests.RequestException as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_MAX_REQUEST_ERROR",
                message="MAX API transport error",
                source="max",
                retryable=True,
                details={"message_id": message_id},
            ) from exc

    def _credentials_from_env(self) -> MaxCredentials:
        return MaxCredentials(
            base_url=self.settings.max_api_base_url or "",
            token=self.settings.max_api_token or "",
        )
