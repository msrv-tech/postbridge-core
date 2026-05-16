from types import SimpleNamespace

import httpx
import pytest

from postbridge.api.schemas import LinkedInCredentials
from postbridge.domain.errors import ConfigurationError
from postbridge.domain.models import PostPayload
from postbridge.integrations.linkedin.credentials import _parse_optional_expires_at
from postbridge.integrations.linkedin.publisher import (
    LinkedInPublisher,
    _instruction_byte_index,
    _normalize_author_urn,
)


class _FakeResponse:
    status_code = 201
    headers = {"x-restli-id": "urn:li:share:123"}

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return {}


class _FakeClient:
    last_request = None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def post(self, url, *, headers=None, json=None):
        type(self).last_request = SimpleNamespace(url=url, headers=headers, json=json)
        return _FakeResponse()


class _MediaResponse:
    def __init__(self, *, status_code=200, headers=None, content=b"", json_data=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self._json_data = json_data or {}

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._json_data


class _FakeMediaClient:
    posts: list[SimpleNamespace] = []
    puts: list[SimpleNamespace] = []
    final_post = None

    def __init__(self, *args, **kwargs):
        type(self).posts = []
        type(self).puts = []
        type(self).final_post = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get(self, url, **kwargs):
        return _MediaResponse(headers={"content-type": "image/png"}, content=b"png-bytes")

    def post(self, url, *, headers=None, json=None):
        type(self).posts.append(SimpleNamespace(url=url, headers=headers, json=json))
        if url.endswith("/rest/images?action=initializeUpload"):
            return _MediaResponse(
                json_data={
                    "value": {
                        "uploadUrl": "https://upload.linkedin.test/image",
                        "image": "urn:li:image:abc",
                    }
                }
            )
        type(self).final_post = SimpleNamespace(url=url, headers=headers, json=json)
        return _FakeResponse()

    def put(self, url, *, content=None, headers=None):
        type(self).puts.append(SimpleNamespace(url=url, content=content, headers=headers))
        return _MediaResponse()


def test_linkedin_publisher_posts_text(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    publisher = LinkedInPublisher(
        settings=SimpleNamespace(linkedin_access_token=None, linkedin_api_version="202601")
    )

    external_id = publisher.publish_post(
        "organization:42",
        PostPayload(source_post_id="p1", text="Hello LinkedIn"),
        credentials=LinkedInCredentials(access_token="token"),
    )

    assert external_id == "urn:li:share:123"
    req = _FakeClient.last_request
    assert req.url == "https://api.linkedin.com/rest/posts"
    assert req.headers["Authorization"] == "Bearer token"
    assert req.headers["LinkedIn-Version"] == "202601"
    assert req.json["author"] == "urn:li:organization:42"
    assert req.json["commentary"] == "Hello LinkedIn"
    assert req.json["lifecycleState"] == "PUBLISHED"


def test_linkedin_publisher_env_credentials_include_author_urn(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    publisher = LinkedInPublisher(
        settings=SimpleNamespace(
            linkedin_access_token="token",
            linkedin_author_urn="urn:li:organization:777",
            linkedin_api_version="202601",
        )
    )

    publisher.publish_post(
        "",
        PostPayload(source_post_id="p1", text="Hello from env"),
    )

    assert _FakeClient.last_request.json["author"] == "urn:li:organization:777"


def test_linkedin_publisher_posts_image(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(httpx, "Client", _FakeMediaClient)
    publisher = LinkedInPublisher(
        settings=SimpleNamespace(linkedin_access_token=None, linkedin_api_version="202601")
    )

    external_id = publisher.publish_post(
        "organization:42",
        PostPayload(source_post_id="p1", text="x", media_url="https://example.com/a.png"),
        credentials=LinkedInCredentials(access_token="token"),
    )

    assert external_id == "urn:li:share:123"
    assert _FakeMediaClient.posts[0].url.endswith("/rest/images?action=initializeUpload")
    assert _FakeMediaClient.posts[0].json == {
        "initializeUploadRequest": {"owner": "urn:li:organization:42"}
    }
    assert _FakeMediaClient.puts[0].url == "https://upload.linkedin.test/image"
    assert _FakeMediaClient.puts[0].content == b"png-bytes"
    assert _FakeMediaClient.final_post.json["content"] == {
        "media": {"id": "urn:li:image:abc", "title": "a.png"}
    }


@pytest.mark.parametrize(
    "channel",
    [
        "organization:",
        "organization:   ",
        "person:",
        "person:\t",
        "linkedin/organization/",
        "linkedin/organization/  ",
        "linkedin/person/",
        "urn:li:organization:",
        "urn:li:organization:  ",
        "urn:li:person:",
    ],
)
def test_normalize_author_urn_rejects_empty_id(channel: str):
    creds = LinkedInCredentials(access_token="x")
    with pytest.raises(ConfigurationError):
        _normalize_author_urn(channel, creds)


def test_normalize_author_urn_strips_urn_suffix_whitespace():
    creds = LinkedInCredentials(access_token="x")
    assert _normalize_author_urn("urn:li:organization:  99 ", creds) == "urn:li:organization:99"


def test_parse_optional_expires_at_rejects_absurdly_large_values():
    assert _parse_optional_expires_at("1767225600") == 1767225600
    assert _parse_optional_expires_at("9" * 1000) is None
    assert _parse_optional_expires_at("253402300800") is None


def test_instruction_byte_index_preserves_zero_value():
    instruction = {"firstByte": 0, "lastByte": 0}

    assert _instruction_byte_index(instruction, "firstByte", 123) == 0
    assert _instruction_byte_index(instruction, "lastByte", 456) == 0
