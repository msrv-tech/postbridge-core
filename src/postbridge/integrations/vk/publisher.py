"""Publisher для публикации постов в группы VK (wall.post)."""

from __future__ import annotations

import mimetypes
from urllib.parse import urlparse

import httpx

from postbridge.api.schemas import VKCredentials
from postbridge.config import Settings, get_settings
from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.domain.models import PostPayload

VK_API_BASE = "https://api.vk.com/method"
VK_API_VERSION = "5.199"
MAX_WALL_ATTACHMENTS = 10


def _parse_group_id(target_channel: str) -> int:
    """Преобразует target_channel в owner_id (отрицательный для групп)."""
    s = target_channel.strip()
    if s.startswith("vk/"):
        s = s[3:]
    if s.startswith("-"):
        return int(s)
    try:
        return -int(s)
    except ValueError:
        raise ConfigurationError(
            f"Invalid VK group id: {target_channel}. Use numeric id or vk/123456."
        ) from None


def _collect_image_urls(payload: PostPayload) -> list[str]:
    """До MAX_WALL_ATTACHMENTS уникальных URL (media_url + media_urls)."""
    out: list[str] = []
    seen: set[str] = set()
    if payload.media_url:
        u = (payload.media_url or "").strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    if payload.media_urls:
        for raw in payload.media_urls:
            if not raw:
                continue
            u = str(raw).strip()
            if u and u not in seen:
                seen.add(u)
                out.append(u)
            if len(out) >= MAX_WALL_ATTACHMENTS:
                break
    return out[:MAX_WALL_ATTACHMENTS]


def _photo_filename_for_upload(image_url: str, content_type: str | None) -> tuple[str, str]:
    ct = (content_type or "").split(";")[0].strip().lower() or "image/jpeg"
    ext = mimetypes.guess_extension(ct) or ".jpg"
    if ext in (".jpe",):
        ext = ".jpg"
    path = urlparse(image_url).path
    name = path.rsplit("/", 1)[-1] if path else ""
    if not name or len(name) > 100 or "/" in name:
        name = f"upload{ext}"
    elif "." not in name:
        name = f"{name}{ext}"
    return name, ct


class VKPublisher:
    """Клиент для публикации постов в группы VK (wall.post)."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def _vk_api_post(
        self,
        client: httpx.Client,
        method: str,
        creds: VKCredentials,
        api_params: dict,
        target_channel: str,
    ):
        """Вызов api.vk.com/method/{method}. Возвращает поле response или бросает ExternalApiError."""
        payload = {
            **api_params,
            "access_token": creds.access_token,
            "v": VK_API_VERSION,
        }
        url = f"{VK_API_BASE}/{method}"
        try:
            response = client.post(url, data=payload, timeout=60.0)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            try:
                err_body = exc.response.json()
            except Exception:
                err_body = {}
            err = err_body.get("error", {})
            code = err.get("error_code", 0)
            msg = err.get("error_msg", str(exc))
            raise ExternalApiError(
                code="EXTERNAL_API_VK_HTTP_ERROR",
                message=f"VK API error: {msg}",
                source="vk",
                retryable=code in (1, 6, 9, 10, 14),
                details={
                    "status_code": exc.response.status_code,
                    "target_channel": target_channel,
                    "vk_error_code": code,
                    "vk_method": method,
                },
            ) from exc
        except httpx.RequestError as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_VK_REQUEST_ERROR",
                message=f"VK API transport error ({method})",
                source="vk",
                retryable=True,
                details={"target_channel": target_channel, "vk_method": method},
            ) from exc

        if "error" in data:
            err = data["error"]
            code = err.get("error_code", 0)
            msg = err.get("error_msg", "Unknown VK error")
            raise ExternalApiError(
                code="EXTERNAL_API_VK_HTTP_ERROR",
                message=f"VK API error: {msg}",
                source="vk",
                retryable=code in (1, 6, 9, 10, 14),
                details={
                    "target_channel": target_channel,
                    "vk_error_code": code,
                    "vk_error_msg": msg,
                    "vk_method": method,
                },
            )
        return data["response"]

    def _build_wall_photo_attachments(
        self,
        client: httpx.Client,
        creds: VKCredentials,
        owner_id: int,
        image_urls: list[str],
        target_channel: str,
    ) -> str:
        """Загрузка фото на сервер VK и строка attachments для wall.post.
        photos.getWallUploadServer и saveWallPhoto требуют user token (групповой даёт error 27).
        photos.saveMessagesPhoto не подходит для wall — фото из сообщений не отображаются на стене."""
        group_id = abs(owner_id)
        upload_creds = creds
        if creds.user_access_token:
            upload_creds = VKCredentials(access_token=creds.user_access_token)
        else:
            raise ExternalApiError(
                code="EXTERNAL_API_VK_UPLOAD_ERROR",
                message=(
                    "VK photos.getWallUploadServer недоступен с токеном группы. "
                    "Для постов с картинками нужен user_access_token в credentials.vk "
                    "(получить через OAuth приложения)."
                ),
                source="vk",
                retryable=False,
                details={"target_channel": target_channel},
            )

        meta = self._vk_api_post(
            client,
            "photos.getWallUploadServer",
            upload_creds,
            {"group_id": group_id},
            target_channel,
        )
        upload_url = (meta or {}).get("upload_url")
        if not upload_url:
            raise ExternalApiError(
                code="EXTERNAL_API_VK_UPLOAD_ERROR",
                message="VK photos.getWallUploadServer: нет upload_url в ответе",
                source="vk",
                retryable=False,
                details={"target_channel": target_channel},
            )
        parts: list[str] = []
        for image_url in image_urls:
            try:
                img_resp = client.get(image_url, follow_redirects=True, timeout=90.0)
                img_resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ExternalApiError(
                    code="EXTERNAL_API_VK_IMAGE_DOWNLOAD_ERROR",
                    message=(
                        f"Не удалось скачать картинку для VK: HTTP {exc.response.status_code}"
                    ),
                    source="vk",
                    retryable=exc.response.status_code in (408, 429, 500, 502, 503, 504),
                    details={
                        "target_channel": target_channel,
                        "image_url": image_url[:500],
                    },
                ) from exc
            except httpx.RequestError as exc:
                raise ExternalApiError(
                    code="EXTERNAL_API_VK_IMAGE_DOWNLOAD_ERROR",
                    message=f"Ошибка сети при скачивании картинки для VK: {exc}",
                    source="vk",
                    retryable=True,
                    details={"target_channel": target_channel, "image_url": image_url[:500]},
                ) from exc

            fname, ctype = _photo_filename_for_upload(
                image_url, img_resp.headers.get("content-type")
            )
            try:
                up = client.post(
                    upload_url,
                    files={"photo": (fname, img_resp.content, ctype)},
                    timeout=90.0,
                )
                up.raise_for_status()
                up_json = up.json()
            except httpx.HTTPStatusError as exc:
                raise ExternalApiError(
                    code="EXTERNAL_API_VK_UPLOAD_ERROR",
                    message=f"Загрузка фото на сервер VK: HTTP {exc.response.status_code}",
                    source="vk",
                    retryable=exc.response.status_code in (408, 429, 500, 502, 503, 504),
                    details={"target_channel": target_channel},
                ) from exc
            except httpx.RequestError as exc:
                raise ExternalApiError(
                    code="EXTERNAL_API_VK_UPLOAD_ERROR",
                    message=f"Ошибка сети при загрузке фото на VK: {exc}",
                    source="vk",
                    retryable=True,
                    details={"target_channel": target_channel},
                ) from exc

            if not isinstance(up_json, dict):
                raise ExternalApiError(
                    code="EXTERNAL_API_VK_UPLOAD_ERROR",
                    message="Некорректный ответ сервера загрузки VK",
                    source="vk",
                    retryable=False,
                    details={"target_channel": target_channel},
                )
            photo_raw = up_json.get("photo")
            server = up_json.get("server")
            hash_v = up_json.get("hash")
            if (
                server is None
                or hash_v is None
                or photo_raw is None
                or (isinstance(photo_raw, str) and not photo_raw.strip())
            ):
                err_msg = up_json.get("error") or up_json
                raise ExternalApiError(
                    code="EXTERNAL_API_VK_UPLOAD_ERROR",
                    message=f"VK отклонил загрузку фото: {err_msg}",
                    source="vk",
                    retryable=False,
                    details={"target_channel": target_channel, "image_url": image_url[:500]},
                )

            saved = self._vk_api_post(
                client,
                "photos.saveWallPhoto",
                upload_creds,
                {
                    "group_id": group_id,
                    "photo": photo_raw,
                    "server": server,
                    "hash": hash_v,
                },
                target_channel,
            )
            if not saved or not isinstance(saved, list) or not saved[0]:
                raise ExternalApiError(
                    code="EXTERNAL_API_VK_UPLOAD_ERROR",
                    message="photos.saveWallPhoto вернул пустой ответ",
                    source="vk",
                    retryable=False,
                    details={"target_channel": target_channel},
                )
            ph = saved[0]
            oid = ph.get("owner_id")
            pid = ph.get("id")
            if oid is None or pid is None:
                raise ExternalApiError(
                    code="EXTERNAL_API_VK_UPLOAD_ERROR",
                    message=f"photos.saveWallPhoto: нет id/owner_id: {ph}",
                    source="vk",
                    retryable=False,
                    details={"target_channel": target_channel},
                )
            parts.append(f"photo{oid}_{pid}")
        return ",".join(parts)

    def publish_post(
        self,
        target_channel: str,
        payload: PostPayload,
        credentials: VKCredentials | None = None,
    ) -> str | None:
        """Публикует пост на стену группы. Возвращает post_id."""
        creds = credentials or self._credentials_from_env()
        if not creds.access_token:
            raise ConfigurationError(
                "Missing VK_ACCESS_TOKEN for VK publishing."
            )

        owner_id = _parse_group_id(target_channel)
        image_urls = _collect_image_urls(payload)
        params: dict = {
            "owner_id": owner_id,
            "message": payload.text or "",
            "from_group": 1,
        }

        timeout = httpx.Timeout(120.0, connect=30.0)
        with httpx.Client(timeout=timeout) as client:
            if image_urls:
                params["attachments"] = self._build_wall_photo_attachments(
                    client, creds, owner_id, image_urls, target_channel
                )
            resp = self._vk_api_post(
                client, "wall.post", creds, params, target_channel
            )
        post_id = (resp or {}).get("post_id") if isinstance(resp, dict) else None
        if post_id is not None:
            return str(post_id)
        return None

    def edit_message(
        self,
        message_id: str,
        text: str = "",
        media_url: str | None = None,
        media_urls: list[str] | None = None,
        credentials: VKCredentials | None = None,
        target_channel: str | None = None,
    ) -> None:
        """Редактирует пост на стене группы (wall.edit). target_channel нужен для owner_id."""
        if not target_channel:
            raise ConfigurationError(
                "target_channel required for VK wall.edit (owner_id)"
            )
        creds = credentials or self._credentials_from_env()
        if not creds.access_token:
            raise ConfigurationError(
                "Missing VK_ACCESS_TOKEN for VK edit."
            )
        owner_id = _parse_group_id(target_channel)
        params: dict = {
            "post_id": message_id,
            "owner_id": owner_id,
            "message": text or "",
            "access_token": creds.access_token,
            "v": VK_API_VERSION,
        }
        url = f"{VK_API_BASE}/wall.edit"
        try:
            with httpx.Client(timeout=30) as client:
                response = client.post(url, data=params)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            try:
                err_body = exc.response.json()
            except Exception:
                err_body = {}
            err = err_body.get("error", {})
            code = err.get("error_code", 0)
            msg = err.get("error_msg", str(exc))
            raise ExternalApiError(
                code="EXTERNAL_API_VK_HTTP_ERROR",
                message=f"VK API error: {msg}",
                source="vk",
                retryable=code in (1, 6, 9, 10, 14),
                details={
                    "status_code": exc.response.status_code,
                    "target_channel": target_channel,
                    "vk_error_code": code,
                },
            ) from exc
        except httpx.RequestError as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_VK_REQUEST_ERROR",
                message="VK API transport error",
                source="vk",
                retryable=True,
                details={"target_channel": target_channel},
            ) from exc

        if "error" in data:
            err = data["error"]
            code = err.get("error_code", 0)
            msg = err.get("error_msg", "Unknown VK error")
            raise ExternalApiError(
                code="EXTERNAL_API_VK_HTTP_ERROR",
                message=f"VK API error: {msg}",
                source="vk",
                retryable=code in (1, 6, 9, 10, 14),
                details={
                    "target_channel": target_channel,
                    "vk_error_code": code,
                    "vk_error_msg": msg,
                },
            )

    def delete_post(
        self,
        target_channel: str,
        post_id: str,
        credentials: VKCredentials | None = None,
    ) -> None:
        """Удаляет пост со стены группы (wall.delete).
        С community token может быть недоступен — метод выбросит ExternalApiError."""
        creds = credentials or self._credentials_from_env()
        if not creds.access_token:
            raise ConfigurationError(
                "Missing VK_ACCESS_TOKEN for VK delete."
            )
        owner_id = _parse_group_id(target_channel)
        params: dict = {
            "owner_id": owner_id,
            "post_id": post_id,
            "access_token": creds.access_token,
            "v": VK_API_VERSION,
        }
        url = f"{VK_API_BASE}/wall.delete"
        try:
            with httpx.Client(timeout=30) as client:
                response = client.post(url, data=params)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            try:
                err_body = exc.response.json()
            except Exception:
                err_body = {}
            err = err_body.get("error", {})
            code = err.get("error_code", 0)
            msg = err.get("error_msg", str(exc))
            raise ExternalApiError(
                code="EXTERNAL_API_VK_HTTP_ERROR",
                message=f"VK API error: {msg}",
                source="vk",
                retryable=code in (1, 6, 9, 10, 14),
                details={
                    "status_code": exc.response.status_code,
                    "target_channel": target_channel,
                    "vk_error_code": code,
                },
            ) from exc
        except httpx.RequestError as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_VK_REQUEST_ERROR",
                message="VK API transport error",
                source="vk",
                retryable=True,
                details={"target_channel": target_channel},
            ) from exc

        if "error" in data:
            err = data["error"]
            code = err.get("error_code", 0)
            msg = err.get("error_msg", "Unknown VK error")
            raise ExternalApiError(
                code="EXTERNAL_API_VK_HTTP_ERROR",
                message=f"VK API error: {msg}",
                source="vk",
                retryable=code in (1, 6, 9, 10, 14),
                details={
                    "target_channel": target_channel,
                    "vk_error_code": code,
                    "vk_error_msg": msg,
                },
            )

    def _credentials_from_env(self) -> VKCredentials:
        return VKCredentials(
            access_token=self.settings.vk_access_token or "",
        )
