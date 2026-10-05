from __future__ import annotations

import sys
from io import BytesIO
from types import ModuleType, SimpleNamespace

import pytest

from postbridge.domain.errors import ExternalApiError, PostbridgeError, ValidationError
from postbridge.infrastructure import media_storage
from postbridge.observability.failure_class import classify_publication_failure
from postbridge.services import media_assets


def test_upload_media_object_local(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        media_storage,
        "get_settings",
        lambda: SimpleNamespace(
            media_storage_type="local",
            media_storage_path=str(tmp_path),
            media_base_url="https://cdn.test/media/",
        ),
    )

    url = media_storage.upload_media_object("folder/file name.txt", b"data", "text/plain")

    assert url == "https://cdn.test/media/folder/file%20name.txt"
    assert (tmp_path / "folder/file name.txt").read_bytes() == b"data"

    media_storage.delete_media_object("folder/file name.txt")

    assert not (tmp_path / "folder/file name.txt").exists()


def test_upload_media_object_requires_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media_storage, "get_settings", lambda: SimpleNamespace(media_storage_type="none"))

    with pytest.raises(RuntimeError, match="MEDIA_STORAGE_NOT_CONFIGURED"):
        media_storage.upload_media_object("key", b"data", "text/plain")

    with pytest.raises(RuntimeError, match="local media requires"):
        media_storage._upload_local(
            "key",
            b"data",
            SimpleNamespace(media_storage_path="", media_base_url=""),
        )

    with pytest.raises(RuntimeError, match="s3 media requires"):
        media_storage._upload_s3("key", b"data", "text/plain", SimpleNamespace(s3_bucket=""))


def test_upload_media_object_s3_public_and_presigned(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class Client:
        def put_object(self, **kwargs: object) -> None:
            calls.append(kwargs)

        def generate_presigned_url(self, action: str, **kwargs: object) -> str:
            return f"https://signed.test/{action}"

        def delete_object(self, **kwargs: object) -> None:
            calls.append(kwargs)

        def get_object(self, **kwargs: object) -> dict[str, object]:
            calls.append(kwargs)
            return {"Body": BytesIO(b"stored-data")}

    boto3 = ModuleType("boto3")
    boto3.client = lambda *args, **kwargs: Client()  # type: ignore[attr-defined]
    botocore = ModuleType("botocore")
    botocore_config = ModuleType("botocore.config")
    botocore_config.Config = lambda **kwargs: ("config", kwargs)  # type: ignore[attr-defined]
    botocore_exceptions = ModuleType("botocore.exceptions")
    botocore_exceptions.BotoCoreError = type("BotoCoreError", (Exception,), {})  # type: ignore[attr-defined]
    botocore_exceptions.ClientError = type("ClientError", (Exception,), {})  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    monkeypatch.setitem(sys.modules, "botocore", botocore)
    monkeypatch.setitem(sys.modules, "botocore.config", botocore_config)
    monkeypatch.setitem(sys.modules, "botocore.exceptions", botocore_exceptions)

    settings = SimpleNamespace(
        s3_bucket="bucket",
        s3_endpoint_url="https://s3.test",
        s3_region="ru-1",
        s3_access_key="access",
        s3_secret_key="secret",
        s3_public_base_url="https://cdn.test/",
    )
    assert media_storage._upload_s3("folder/file name.txt", b"data", "text/plain", settings) == (
        "https://cdn.test/folder/file%20name.txt"
    )
    assert calls[-1] == {
        "Bucket": "bucket",
        "Key": "folder/file name.txt",
        "Body": b"data",
        "ContentType": "text/plain",
    }

    settings.s3_public_base_url = ""
    assert media_storage._upload_s3("key", b"data", "", settings) == "https://signed.test/get_object"
    assert "ContentType" not in calls[-1]

    assert media_storage._read_s3("key", settings) == b"stored-data"
    assert calls[-1] == {"Bucket": "bucket", "Key": "key"}

    media_storage._delete_s3("key", settings)

    assert calls[-1] == {"Bucket": "bucket", "Key": "key"}


def test_managed_s3_asset_uses_core_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        media_assets,
        "get_settings",
        lambda: SimpleNamespace(
            media_storage_type="s3",
            media_base_url="https://postbridge.test/core/media/",
        ),
    )

    assert media_assets._public_media_url(
        asset_id="asset-1",
        storage_url="https://s3.test/signed",
    ) == "https://postbridge.test/core/media/asset-1"


def test_unproxied_media_keeps_storage_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        media_assets,
        "get_settings",
        lambda: SimpleNamespace(media_storage_type="local", media_base_url="https://cdn.test/media"),
    )

    assert media_assets._public_media_url(
        asset_id="asset-1",
        storage_url="https://cdn.test/media/path/image.png",
    ) == "https://cdn.test/media/path/image.png"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (ValidationError("VALIDATION_BAD", "bad"), "validation"),
        (PostbridgeError("API_RATE_429", "slow"), "rate_limit"),
        (PostbridgeError("TOKEN_EXPIRED", "bad token"), "auth"),
        (PostbridgeError("DNS_FAILURE", "dns"), "network"),
        (ExternalApiError("REMOTE_BAD", "remote", source="remote", retryable=False), "external_api"),
        (PostbridgeError("SOMETHING_ELSE", "unknown"), "other"),
    ],
)
def test_classify_publication_failure(exc: PostbridgeError, expected: str) -> None:
    assert classify_publication_failure(exc) == expected
