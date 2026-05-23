from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
import requests

from postbridge.api.schemas import MaxCredentials, VKCredentials
from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.domain.models import PostPayload
from postbridge.integrations.max.fetcher import MaxFetcher
from postbridge.integrations.max.publisher import MaxPublisher
from postbridge.integrations.vk.publisher import (
    VKPublisher,
    _collect_image_urls,
    _parse_group_id,
    _photo_filename_for_upload,
)


class _RequestsResponse:
    def __init__(self, body: object, *, status_code: int = 200, content: bytes = b"data") -> None:
        self._body = body
        self.status_code = status_code
        self.content = content
        self.text = str(body)

    def json(self) -> object:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = requests.HTTPError("boom")
            error.response = self
            raise error


class _VkResponse:
    def __init__(self, body: dict, *, status_code: int = 200, content: bytes = b"", headers: dict | None = None) -> None:
        self._body = body
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.text = str(body)
        self.request = httpx.Request("POST", "https://api.vk.com/method/test")

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=self.request, response=httpx.Response(self.status_code, json=self._body, request=self.request))


def test_vk_helpers_normalize_group_images_and_upload_filenames() -> None:
    assert _parse_group_id("vk/123") == -123
    assert _parse_group_id("-456") == -456
    with pytest.raises(ConfigurationError):
        _parse_group_id("group")

    payload = PostPayload(
        source_post_id="p1",
        text="hello",
        media_url="https://cdn.test/a.png",
        media_urls=["https://cdn.test/a.png", " https://cdn.test/b ", "", *[f"https://cdn.test/{i}.jpg" for i in range(20)]],
    )
    urls = _collect_image_urls(payload)
    assert urls[0:2] == ["https://cdn.test/a.png", "https://cdn.test/b"]
    assert len(urls) == 10
    assert _photo_filename_for_upload("https://cdn.test/path/photo", "image/png") == ("photo.png", "image/png")
    assert _photo_filename_for_upload("https://cdn.test/" + "x" * 120, "image/jpeg") == ("upload.jpg", "image/jpeg")


def test_vk_publisher_posts_text_and_maps_api_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[SimpleNamespace] = []

    class Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, **kwargs: object) -> _VkResponse:
            calls.append(SimpleNamespace(url=url, kwargs=kwargs))
            return _VkResponse({"response": {"post_id": 77}})

    monkeypatch.setattr("postbridge.integrations.vk.publisher.httpx.Client", Client)
    publisher = VKPublisher(settings=SimpleNamespace(vk_access_token=""))

    assert publisher.publish_post(
        "123",
        PostPayload(source_post_id="p1", text="Hello VK"),
        credentials=VKCredentials(access_token="token"),
    ) == "77"
    assert calls[0].url.endswith("/wall.post")
    assert calls[0].kwargs["data"]["owner_id"] == -123
    assert calls[0].kwargs["data"]["message"] == "Hello VK"

    err = publisher._vk_api_post(
        Client(),
        "wall.post",
        VKCredentials(access_token="token"),
        {},
        "123",
    )
    assert err == {"post_id": 77}


def test_vk_publisher_uploads_wall_photo_and_requires_user_token(monkeypatch: pytest.MonkeyPatch) -> None:
    class Client:
        def __init__(self) -> None:
            self.saved: list[dict] = []

        def get(self, url: str, **kwargs: object) -> _VkResponse:
            return _VkResponse({}, content=b"image", headers={"content-type": "image/png"})

        def post(self, url: str, **kwargs: object) -> _VkResponse:
            if url.endswith("/photos.getWallUploadServer"):
                return _VkResponse({"response": {"upload_url": "https://upload.vk.test/photo"}})
            if url == "https://upload.vk.test/photo":
                return _VkResponse({"photo": "[{}]", "server": 1, "hash": "h"})
            if url.endswith("/photos.saveWallPhoto"):
                self.saved.append(kwargs["data"])
                return _VkResponse({"response": [{"owner_id": -123, "id": 456}]})
            raise AssertionError(url)

    publisher = VKPublisher(settings=SimpleNamespace(vk_access_token=""))
    with pytest.raises(ExternalApiError) as exc:
        publisher._build_wall_photo_attachments(Client(), VKCredentials(access_token="group"), -123, ["https://cdn.test/a.png"], "123")
    assert exc.value.code == "EXTERNAL_API_VK_UPLOAD_ERROR"

    client = Client()
    attachments = publisher._build_wall_photo_attachments(
        client,
        VKCredentials(access_token="group", user_access_token="user"),
        -123,
        ["https://cdn.test/a.png"],
        "123",
    )
    assert attachments == "photo-123_456"
    assert client.saved[0]["group_id"] == 123


def test_max_publisher_resolves_chat_builds_body_and_handles_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(max_api_timeout_seconds=5, max_api_base_url="", max_api_token="")
    publisher = MaxPublisher(settings=settings)
    creds = MaxCredentials(base_url="https://max.test", token="token")
    calls: list[SimpleNamespace] = []

    def fake_get(url: str, **kwargs: object) -> _RequestsResponse:
        calls.append(SimpleNamespace(method="GET", url=url, kwargs=kwargs))
        return _RequestsResponse({"chats": [{"chat_id": 42, "link": "https://max.test/team", "title": "Team"}]})

    def fake_post(url: str, **kwargs: object) -> _RequestsResponse:
        calls.append(SimpleNamespace(method="POST", url=url, kwargs=kwargs))
        return _RequestsResponse({"message": {"body": {"mid": "m1"}}})

    monkeypatch.setattr("postbridge.integrations.max.publisher.requests.get", fake_get)
    monkeypatch.setattr("postbridge.integrations.max.publisher.requests.post", fake_post)

    assert publisher.publish_post(
        "team",
        PostPayload(source_post_id="p", text="Hello", media_url="https://cdn.test/image.png"),
        credentials=creds,
    ) == "m1"
    post_call = [c for c in calls if c.method == "POST" and c.url.endswith("/messages")][0]
    assert post_call.kwargs["params"] == {"chat_id": 42}
    assert post_call.kwargs["json"] == {
        "text": "Hello",
        "attachments": [{"type": "image", "payload": {"url": "https://cdn.test/image.png"}}],
    }

    def failing_post(url: str, **kwargs: object) -> _RequestsResponse:
        return _RequestsResponse({"error": "nope"}, status_code=500)

    monkeypatch.setattr("postbridge.integrations.max.publisher.requests.post", failing_post)
    with pytest.raises(ExternalApiError) as exc:
        publisher.publish_post("42", PostPayload(source_post_id="p", text="x"), credentials=creds)
    assert exc.value.retryable is True


def test_max_publisher_uploads_non_image_media(monkeypatch: pytest.MonkeyPatch) -> None:
    publisher = MaxPublisher(settings=SimpleNamespace(max_api_timeout_seconds=5))
    creds = MaxCredentials(base_url="https://max.test", token="token")
    calls: list[SimpleNamespace] = []

    def fake_get(url: str, **kwargs: object) -> _RequestsResponse:
        return _RequestsResponse({}, content=b"pdf")

    def fake_post(url: str, **kwargs: object) -> _RequestsResponse:
        calls.append(SimpleNamespace(url=url, kwargs=kwargs))
        if url == "https://max.test/uploads":
            assert kwargs["params"] == {"type": "file"}
            return _RequestsResponse({"url": "https://upload.max.test/file"})
        return _RequestsResponse({"token": "upload-token"})

    monkeypatch.setattr("postbridge.integrations.max.publisher.requests.get", fake_get)
    monkeypatch.setattr("postbridge.integrations.max.publisher.requests.post", fake_post)

    body = publisher._build_message_body(
        creds,
        PostPayload(source_post_id="p", text="doc", media_url="https://cdn.test/files/report.pdf"),
    )

    assert body == {"text": "doc", "attachments": [{"type": "file", "payload": {"token": "upload-token"}}]}
    assert calls[1].url == "https://upload.max.test/file"


def test_max_fetcher_maps_messages_and_username_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = MaxFetcher(settings=SimpleNamespace(max_api_timeout_seconds=5, max_api_base_url="", max_api_token=""))
    creds = MaxCredentials(base_url="https://max.test", token="token")

    class Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str, **kwargs: object) -> httpx.Response:
            request = httpx.Request("GET", url)
            return httpx.Response(
                200,
                request=request,
                json={
                    "messages": [
                        {"id": "skip", "body": {}},
                        {"id": "m1", "body": {"text": "first"}},
                        {"message_id": "m2", "body": {"attachments": [{"type": "image", "payload": {"url": "https://cdn.test/a.png"}}]}},
                    ]
                },
            )

    monkeypatch.setattr("postbridge.integrations.max.fetcher.httpx.Client", Client)
    posts = asyncio.run(fetcher.fetch_posts("42", 10, credentials=creds))

    assert [p.source_post_id for p in posts] == ["m2", "m1"]
    assert posts[0].media_url == "https://cdn.test/a.png"
    assert posts[1].text == "first"

    with pytest.raises(ExternalApiError) as exc:
        asyncio.run(fetcher.fetch_posts("unknown", 10, credentials=creds))
    assert exc.value.code == "EXTERNAL_API_MAX_FETCH_ERROR"
