from types import SimpleNamespace

import httpx
import pytest

from postbridge.api.schemas import (
    BlueskyCredentials,
    FacebookCredentials,
    InstagramCredentials,
    MastodonCredentials,
    XCredentials,
)
from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.domain.models import PostPayload
from postbridge.integrations.bluesky.publisher import BlueskyPublisher
from postbridge.integrations.facebook.publisher import FacebookPublisher
from postbridge.integrations.instagram.publisher import InstagramPublisher
from postbridge.integrations.mastodon.publisher import MastodonPublisher
from postbridge.integrations.x.publisher import XPublisher


class _Response:
    def __init__(self, json_data, status_code=200, headers=None, content=b""):
        self._json_data = json_data
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://api.test")
            response = httpx.Response(self.status_code, request=request, json=self._json_data)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self):
        return self._json_data


class _RecordingClient:
    posts: list[SimpleNamespace] = []
    gets: list[SimpleNamespace] = []
    get_status_code: int = 200
    get_content: bytes = b"png-bytes"
    fail_x_media_upload: bool = False

    def __init__(self, *args, **kwargs):
        type(self).posts = []
        type(self).gets = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get(self, url, **kwargs):
        type(self).gets.append(SimpleNamespace(url=url, kwargs=kwargs))
        if "graph.facebook.com" in url:
            return _Response({"status_code": "FINISHED"})
        return _Response(
            {},
            status_code=type(self).get_status_code,
            headers={"content-type": "image/png"},
            content=type(self).get_content,
        )

    def post(self, url, *, headers=None, json=None, data=None, files=None, content=None):
        type(self).posts.append(
            SimpleNamespace(
                url=url,
                headers=headers,
                json=json,
                data=data,
                files=files,
                content=content,
            )
        )
        if url.endswith("/2/media/upload"):
            if type(self).fail_x_media_upload:
                return _Response({"title": "upload failed"}, status_code=429)
            upload_count = len([p for p in type(self).posts if p.url.endswith("/2/media/upload")])
            return _Response({"data": {"id": f"media-{upload_count}"}})
        if url.endswith("/api/v2/media"):
            return _Response({"id": "mastodon-media-1"})
        if url.endswith("/photos"):
            return _Response({"id": f"photo-{len(type(self).posts)}"})
        if url.endswith("/videos"):
            return _Response({"id": "video-1"})
        if url.endswith("/media"):
            return _Response({"id": "ig-container-1"})
        if url.endswith("/media_publish"):
            return _Response({"id": "ig-post-1"})
        if url.endswith("/2/tweets"):
            return _Response({"data": {"id": "tweet-1"}})
        if url.endswith("com.atproto.server.createSession"):
            return _Response({"accessJwt": "jwt", "did": "did:plc:abc"})
        if url.endswith("com.atproto.repo.uploadBlob"):
            return _Response(
                {
                    "blob": {
                        "$type": "blob",
                        "ref": {"$link": "blob-cid"},
                        "mimeType": "image/png",
                        "size": 9,
                    }
                }
            )
        if url.endswith("com.atproto.repo.createRecord"):
            return _Response({"uri": "at://did:plc:abc/app.bsky.feed.post/1"})
        if url.endswith("/api/v1/statuses"):
            return _Response({"id": "status-1"})
        return _Response({"id": "fb-post-1"})


@pytest.fixture(autouse=True)
def fake_httpx(monkeypatch: pytest.MonkeyPatch):
    _RecordingClient.get_status_code = 200
    _RecordingClient.get_content = b"png-bytes"
    _RecordingClient.fail_x_media_upload = False
    monkeypatch.setattr(httpx, "Client", _RecordingClient)


def test_facebook_publisher_posts_page_feed():
    publisher = FacebookPublisher(settings=SimpleNamespace(meta_graph_api_version="v25.0"))

    external_id = publisher.publish_post(
        "facebook/page/42",
        PostPayload(source_post_id="p1", text="Hello Facebook"),
        credentials=FacebookCredentials(page_access_token="token"),
    )

    assert external_id == "fb-post-1"
    req = _RecordingClient.posts[0]
    assert req.url == "https://graph.facebook.com/v25.0/42/feed"
    assert req.data["message"] == "Hello Facebook"
    assert req.data["access_token"] == "token"


def test_facebook_publisher_posts_single_photo():
    publisher = FacebookPublisher(settings=SimpleNamespace(meta_graph_api_version="v25.0"))

    external_id = publisher.publish_post(
        "42",
        PostPayload(source_post_id="p1", text="Photo", media_url="https://cdn.test/a.jpg"),
        credentials=FacebookCredentials(page_access_token="token"),
    )

    assert external_id == "photo-1"
    req = _RecordingClient.posts[0]
    assert req.url == "https://graph.facebook.com/v25.0/42/photos"
    assert req.data["url"] == "https://cdn.test/a.jpg"
    assert req.data["caption"] == "Photo"
    assert req.data["published"] == "true"


def test_facebook_publisher_posts_single_video():
    publisher = FacebookPublisher(settings=SimpleNamespace(meta_graph_api_version="v25.0"))

    external_id = publisher.publish_post(
        "42",
        PostPayload(source_post_id="p1", text="Video", media_url="https://cdn.test/a.mp4"),
        credentials=FacebookCredentials(page_access_token="token"),
    )

    assert external_id == "video-1"
    req = _RecordingClient.posts[0]
    assert req.url == "https://graph.facebook.com/v25.0/42/videos"
    assert req.data["file_url"] == "https://cdn.test/a.mp4"
    assert req.data["description"] == "Video"


def test_facebook_publisher_posts_multi_photo_feed():
    publisher = FacebookPublisher(settings=SimpleNamespace(meta_graph_api_version="v25.0"))

    external_id = publisher.publish_post(
        "42",
        PostPayload(
            source_post_id="p1",
            text="Album",
            media_urls=["https://cdn.test/a.jpg", "https://cdn.test/b.jpg"],
        ),
        credentials=FacebookCredentials(page_access_token="token"),
    )

    assert external_id == "fb-post-1"
    assert _RecordingClient.posts[0].url.endswith("/photos")
    assert _RecordingClient.posts[0].data["published"] == "false"
    assert _RecordingClient.posts[1].url.endswith("/photos")
    feed = _RecordingClient.posts[2]
    assert feed.url == "https://graph.facebook.com/v25.0/42/feed"
    assert feed.data["message"] == "Album"
    assert feed.data["attached_media[0]"] == '{"media_fbid":"photo-1"}'
    assert feed.data["attached_media[1]"] == '{"media_fbid":"photo-2"}'


def test_instagram_publisher_creates_and_publishes_media_container():
    publisher = InstagramPublisher(settings=SimpleNamespace(meta_graph_api_version="v25.0"))

    external_id = publisher.publish_post(
        "instagram/1784",
        PostPayload(source_post_id="p1", text="Hello IG", media_url="https://cdn.test/a.jpg"),
        credentials=InstagramCredentials(access_token="token"),
    )

    assert external_id == "ig-post-1"
    assert _RecordingClient.posts[0].url == "https://graph.facebook.com/v25.0/1784/media"
    assert _RecordingClient.posts[0].data["image_url"] == "https://cdn.test/a.jpg"
    assert _RecordingClient.posts[1].data["creation_id"] == "ig-container-1"
    assert _RecordingClient.gets[0].url == "https://graph.facebook.com/v25.0/ig-container-1"


def test_instagram_publisher_creates_carousel():
    publisher = InstagramPublisher(settings=SimpleNamespace(meta_graph_api_version="v25.0"))

    external_id = publisher.publish_post(
        "1784",
        PostPayload(
            source_post_id="p1",
            text="Carousel",
            media_urls=["https://cdn.test/a.jpg", "https://cdn.test/b.jpg"],
        ),
        credentials=InstagramCredentials(access_token="token"),
    )

    assert external_id == "ig-post-1"
    assert _RecordingClient.posts[0].data["is_carousel_item"] == "true"
    assert _RecordingClient.posts[1].data["is_carousel_item"] == "true"
    carousel = _RecordingClient.posts[2]
    assert carousel.data["media_type"] == "CAROUSEL"
    assert carousel.data["children"] == "ig-container-1,ig-container-1"
    assert _RecordingClient.posts[3].data["creation_id"] == "ig-container-1"


def test_instagram_publisher_requires_media():
    publisher = InstagramPublisher(settings=SimpleNamespace(meta_graph_api_version="v25.0"))

    with pytest.raises(ConfigurationError):
        publisher.publish_post(
            "1784",
            PostPayload(source_post_id="p1", text="text only"),
            credentials=InstagramCredentials(access_token="token"),
        )


def test_x_publisher_posts_tweet():
    publisher = XPublisher(settings=SimpleNamespace(x_access_token=None))

    external_id = publisher.publish_post(
        "",
        PostPayload(source_post_id="p1", text="Hello X"),
        credentials=XCredentials(access_token="token"),
    )

    assert external_id == "tweet-1"
    req = _RecordingClient.posts[0]
    assert req.url == "https://api.x.com/2/tweets"
    assert req.headers["Authorization"] == "Bearer token"
    assert req.json == {"text": "Hello X"}


def test_x_publisher_uploads_media_before_tweet():
    publisher = XPublisher(settings=SimpleNamespace(x_access_token=None))

    publisher.publish_post(
        "",
        PostPayload(source_post_id="p1", text="Hello X", media_url="https://cdn.test/a.png"),
        credentials=XCredentials(access_token="token"),
    )

    upload = _RecordingClient.posts[0]
    tweet = _RecordingClient.posts[1]
    assert upload.url == "https://api.x.com/2/media/upload"
    assert upload.files["media"][0] == "a.png"
    assert tweet.json["media"] == {"media_ids": ["media-1"]}


def test_x_publisher_uploads_up_to_four_media_urls():
    publisher = XPublisher(settings=SimpleNamespace(x_access_token=None))

    publisher.publish_post(
        "",
        PostPayload(
            source_post_id="p1",
            text="Hello X album",
            media_url="https://cdn.test/a.png",
            media_urls=[
                "https://cdn.test/a.png",
                "https://cdn.test/b.png",
                "https://cdn.test/c.png",
                "https://cdn.test/d.png",
                "https://cdn.test/e.png",
            ],
        ),
        credentials=XCredentials(access_token="token"),
    )

    uploads = [req for req in _RecordingClient.posts if req.url.endswith("/2/media/upload")]
    tweet = _RecordingClient.posts[-1]
    assert [req.files["media"][0] for req in uploads] == ["a.png", "b.png", "c.png", "d.png"]
    assert tweet.json["media"] == {"media_ids": ["media-1", "media-2", "media-3", "media-4"]}


def test_x_publisher_surfaces_media_download_failure():
    _RecordingClient.get_status_code = 404
    publisher = XPublisher(settings=SimpleNamespace(x_access_token=None))

    with pytest.raises(ExternalApiError) as excinfo:
        publisher.publish_post(
            "",
            PostPayload(source_post_id="p1", text="Hello X", media_url="https://cdn.test/missing.png"),
            credentials=XCredentials(access_token="token"),
        )

    assert excinfo.value.code == "EXTERNAL_API_X_MEDIA_DOWNLOAD_ERROR"
    assert excinfo.value.retryable is False


def test_x_publisher_rejects_oversized_media(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("postbridge.integrations.x.publisher.X_MAX_MEDIA_BYTES", 3)
    _RecordingClient.get_content = b"xxxx"
    publisher = XPublisher(settings=SimpleNamespace(x_access_token=None))

    with pytest.raises(ExternalApiError) as excinfo:
        publisher.publish_post(
            "",
            PostPayload(source_post_id="p1", text="Hello X", media_url="https://cdn.test/huge.png"),
            credentials=XCredentials(access_token="token"),
        )

    assert excinfo.value.code == "EXTERNAL_API_X_MEDIA_TOO_LARGE"
    assert excinfo.value.retryable is False


def test_x_publisher_surfaces_media_upload_http_error():
    _RecordingClient.fail_x_media_upload = True
    publisher = XPublisher(settings=SimpleNamespace(x_access_token=None))

    with pytest.raises(ExternalApiError) as excinfo:
        publisher.publish_post(
            "",
            PostPayload(source_post_id="p1", text="Hello X", media_url="https://cdn.test/a.png"),
            credentials=XCredentials(access_token="token"),
        )

    assert excinfo.value.code == "EXTERNAL_API_X_HTTP_ERROR"
    assert excinfo.value.retryable is True


def test_bluesky_publisher_creates_record():
    publisher = BlueskyPublisher(settings=SimpleNamespace(bluesky_service_url="https://bsky.social"))

    external_id = publisher.publish_post(
        "",
        PostPayload(source_post_id="p1", text="Hello Bluesky"),
        credentials=BlueskyCredentials(identifier="alice.test", app_password="pass"),
    )

    assert external_id == "at://did:plc:abc/app.bsky.feed.post/1"
    assert _RecordingClient.posts[0].url.endswith("com.atproto.server.createSession")
    record_req = _RecordingClient.posts[1]
    assert record_req.url.endswith("com.atproto.repo.createRecord")
    assert record_req.headers["Authorization"] == "Bearer jwt"
    assert record_req.json["record"]["text"] == "Hello Bluesky"


def test_bluesky_publisher_uploads_image_embed():
    publisher = BlueskyPublisher(settings=SimpleNamespace(bluesky_service_url="https://bsky.social"))

    publisher.publish_post(
        "",
        PostPayload(source_post_id="p1", text="Hello Bluesky", media_url="https://cdn.test/a.png"),
        credentials=BlueskyCredentials(identifier="alice.test", app_password="pass"),
    )

    upload = _RecordingClient.posts[1]
    record_req = _RecordingClient.posts[2]
    assert upload.url.endswith("com.atproto.repo.uploadBlob")
    assert upload.content == b"png-bytes"
    assert record_req.json["record"]["embed"]["images"][0]["image"]["ref"]["$link"] == "blob-cid"


def test_mastodon_publisher_posts_status():
    publisher = MastodonPublisher(
        settings=SimpleNamespace(
            mastodon_access_token=None,
            mastodon_instance_url=None,
            mastodon_visibility="public",
        )
    )

    external_id = publisher.publish_post(
        "",
        PostPayload(source_post_id="p1", text="Hello Mastodon"),
        credentials=MastodonCredentials(
            access_token="token",
            instance_url="https://mastodon.social",
        ),
    )

    assert external_id == "status-1"
    req = _RecordingClient.posts[0]
    assert req.url == "https://mastodon.social/api/v1/statuses"
    assert req.headers["Authorization"] == "Bearer token"
    assert req.data == [("status", "Hello Mastodon"), ("visibility", "public")]


def test_mastodon_publisher_uploads_media_before_status():
    publisher = MastodonPublisher(
        settings=SimpleNamespace(
            mastodon_access_token=None,
            mastodon_instance_url=None,
            mastodon_visibility="public",
        )
    )

    publisher.publish_post(
        "",
        PostPayload(source_post_id="p1", text="Hello Mastodon", media_url="https://cdn.test/a.png"),
        credentials=MastodonCredentials(
            access_token="token",
            instance_url="https://mastodon.social",
        ),
    )

    upload = _RecordingClient.posts[0]
    status = _RecordingClient.posts[1]
    assert upload.url == "https://mastodon.social/api/v2/media"
    assert upload.files["file"][0] == "a.png"
    assert status.data == [
        ("status", "Hello Mastodon"),
        ("visibility", "public"),
        ("media_ids[]", "mastodon-media-1"),
    ]
