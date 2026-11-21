"""Исполнение publication_target: claim, publisher, идемпотентность."""

from functools import lru_cache
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4
import subprocess

import httpx
import pytest
from datetime import UTC, datetime, timedelta

from postbridge.db import Base, ENGINE, SESSION_LOCAL, BatchImportRunOrm, init_db  # noqa: E402
from postbridge.models.domain import (  # noqa: E402
    ChannelOrm,
    ContentItemOrm,
    MediaAssetOrm,
    PublicationPlanOrm,
    PublicationTargetOrm,
    RenderVariantOrm,
    TenantOrm,
)
from postbridge.integrations.telegram.publisher import TelegramPublisher
from postbridge.services.publication_target_executor import (  # noqa: E402
    PUBLICATION_TARGET_PENDING,
    PUBLICATION_TARGET_PUBLISHED,
    PUBLICATION_TARGET_PUBLISHING,
    PublicationTargetExecutor,
    claim_publication_target_pending,
    recover_stuck_publication_targets,
)
from postbridge.services.publication_planning import create_content_with_plan_and_targets  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    session = SESSION_LOCAL()
    session.query(BatchImportRunOrm).delete()
    session.commit()
    session.close()
    yield


def _seed_chain_with_max_channel() -> tuple[str, str]:
    session = SESSION_LOCAL()
    tenant_id = str(uuid4())
    session.add(TenantOrm(id=tenant_id, name="t"))
    session.flush()
    ch_id = str(uuid4())
    session.add(
        ChannelOrm(
            id=ch_id,
            tenant_id=tenant_id,
            platform="max",
            kind="destination",
            title="Max ch",
            external_id="chat-42",
            status="connected",
        )
    )
    session.commit()
    session.close()

    session = SESSION_LOCAL()
    result = create_content_with_plan_and_targets(
        session,
        tenant_id=tenant_id,
        channel_ids=[ch_id],
        title="Hi",
        body_markdown="Body",
        target_status=PUBLICATION_TARGET_PENDING,
    )
    session.commit()
    tid = result.publication_target_ids[0]
    session.close()
    return tenant_id, tid


class _FakePublisher:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def publish_post(self, target_channel, post, credentials=None):
        self.calls.append((target_channel, post, credentials))
        return "msg-ext-1"


def test_executor_passes_media_from_content_columns():
    session = SESSION_LOCAL()
    tenant_id = str(uuid4())
    session.add(TenantOrm(id=tenant_id, name="t"))
    session.flush()
    ch_id = str(uuid4())
    session.add(
        ChannelOrm(
            id=ch_id,
            tenant_id=tenant_id,
            platform="max",
            kind="destination",
            title="Max ch",
            external_id="chat-99",
            status="connected",
        )
    )
    session.commit()
    result = create_content_with_plan_and_targets(
        session,
        tenant_id=tenant_id,
        channel_ids=[ch_id],
        title="Hi",
        body_markdown="Body",
        media_url="https://example.com/a.jpg",
        media_urls=["https://example.com/b.jpg"],
        target_status=PUBLICATION_TARGET_PENDING,
    )
    session.commit()
    target_id = result.publication_target_ids[0]
    session.close()

    fake = _FakePublisher()
    session = SESSION_LOCAL()
    executor = PublicationTargetExecutor(session=session, publisher=fake)
    assert executor.run(target_id) == 1
    session.close()
    assert len(fake.calls) == 1
    post = fake.calls[0][1]
    assert post.media_url == "https://example.com/a.jpg"
    assert post.media_urls == ["https://example.com/b.jpg"]


def test_executor_publishes_and_sets_published():
    _, target_id = _seed_chain_with_max_channel()
    fake = _FakePublisher()
    session = SESSION_LOCAL()
    executor = PublicationTargetExecutor(session=session, publisher=fake)
    assert executor.run(target_id) == 1
    session.close()

    verify = SESSION_LOCAL()
    t = verify.get(PublicationTargetOrm, target_id)
    assert t is not None
    assert t.status == PUBLICATION_TARGET_PUBLISHED
    assert t.external_post_id == "msg-ext-1"
    assert t.published_at is not None
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == "chat-42"
    verify.close()


def test_executor_idempotent_when_already_published():
    _, target_id = _seed_chain_with_max_channel()
    fake = _FakePublisher()
    session = SESSION_LOCAL()
    executor = PublicationTargetExecutor(session=session, publisher=fake)
    assert executor.run(target_id) == 1
    assert executor.run(target_id) == 0
    session.close()
    assert len(fake.calls) == 1


def test_second_worker_cannot_claim_same_target():
    _, target_id = _seed_chain_with_max_channel()
    fake = _FakePublisher()
    s1 = SESSION_LOCAL()
    assert claim_publication_target_pending(s1, target_id) is True
    s1.close()

    s2 = SESSION_LOCAL()
    ex = PublicationTargetExecutor(session=s2, publisher=fake)
    assert ex.run(target_id) == 0
    assert len(fake.calls) == 0
    s2.close()

    s3 = SESSION_LOCAL()
    t = s3.get(PublicationTargetOrm, target_id)
    assert t is not None
    assert t.status == PUBLICATION_TARGET_PUBLISHING
    s3.close()


def test_recover_stuck_publication_targets_resets_and_returns_ids():
    _, target_id = _seed_chain_with_max_channel()
    session = SESSION_LOCAL()
    try:
        target = session.get(PublicationTargetOrm, target_id)
        assert target is not None
        assert claim_publication_target_pending(session, target_id)
        target.updated_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()

        recovered = recover_stuck_publication_targets(session, timeout_seconds=60)
        assert recovered == [target_id]

        refreshed = session.get(PublicationTargetOrm, target_id)
        assert refreshed is not None
        assert refreshed.status == PUBLICATION_TARGET_PENDING
    finally:
        session.close()


def test_dispatch_endpoint_enqueues(monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        "postbridge.api.internal_auth.get_settings",
        lambda: SimpleNamespace(sync_publish_token="secret"),
    )

    _, target_id = _seed_chain_with_max_channel()
    delayed: list = []

    def fake_delay(*_a, **_k):
        delayed.append(True)

    monkeypatch.setattr(
        "postbridge.api.publication_internal.process_publication_target_task.delay",
        fake_delay,
    )

    from postbridge.api.main import app  # noqa: E402

    client = TestClient(app)
    r = client.post(
        f"/internal/publication-targets/{target_id}/dispatch",
        headers={"X-Sync-Publish-Token": "secret"},
    )
    assert r.status_code == 200
    assert r.json() == {"status": "enqueued", "target_id": target_id}
    assert delayed == [True]


@lru_cache
def _telegram_fixture_image(codec="mjpeg", width=640, height=480):
    return subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", f"color=size={width}x{height}",
         "-frames:v", "1", "-threads", "1", "-vcodec", codec, "-f", "image2pipe", "pipe:1"],
        check=True, capture_output=True, timeout=15,
    ).stdout


@pytest.mark.parametrize(
    ("case", "expected_method", "field"),
    [
        ("managed-jpeg", "sendPhoto", "photo"),
        ("managed-jpeg-first", "sendPhoto", "photo"),
        ("managed-png", "sendPhoto", "photo"),
        ("url-limit", "sendPhoto", "photo"),
        ("over-url-limit", "sendDocument", "document"),
        ("unknown-size", "sendDocument", "document"),
        ("zero-size", "sendDocument", "document"),
        ("invalid-image", "sendDocument", "document"),
        ("dimension-limit", "sendPhoto", "photo"),
        ("over-dimension-limit", "sendDocument", "document"),
        ("ratio-limit", "sendPhoto", "photo"),
        ("over-ratio-limit", "sendDocument", "document"),
        ("object-size-mismatch", "sendDocument", "document"),
        ("object-read-failure", "sendDocument", "document"),
        ("managed-document", "sendDocument", "document"),
        ("foreign-workspace", "sendDocument", "document"),
        ("foreign-origin", "sendDocument", "document"),
        ("missing-asset", "sendDocument", "document"),
        ("suffix-fallback", "sendPhoto", "photo"),
        ("primary-document", "sendDocument", "document"),
        ("no-media", "sendMessage", "text"),
    ],
)
def test_telegram_executor_selects_method_from_managed_media(case, expected_method, field, monkeypatch):
    base_url = "https://postbridge.example.test/core/media"
    monkeypatch.setenv("MEDIA_STORAGE_TYPE", "s3")
    monkeypatch.setenv("S3_BUCKET", "fixture-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY", "fixture-access")
    monkeypatch.setenv("S3_SECRET_KEY", "fixture-secret")
    monkeypatch.setenv("MEDIA_BASE_URL", base_url)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fixture-token")

    def fail_on_http(*args, **kwargs):
        pytest.fail("Media classification must not make HTTP requests")

    monkeypatch.setattr(httpx, "Client", fail_on_http)
    calls = []
    storage_gets = []
    storage_read_sizes = []

    class TrackedBody(BytesIO):
        def read(self, size=-1):
            storage_read_sizes.append(size)
            return super().read(size)

    def get_object(**request):
        storage_gets.append(request)
        if case == "object-read-failure":
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        # Exercise the streaming cap even when object response size is absent.
        return {"Body": TrackedBody(data)}

    monkeypatch.setattr("postbridge.infrastructure.media_storage._s3_client",
                        lambda _: SimpleNamespace(get_object=get_object))

    def bot_request(self, method, url, **kwargs):
        body = kwargs["json"]
        calls.append((method, url.rsplit("/", 1)[-1], body))
        return httpx.Response(200, request=httpx.Request(method, url), json={"result": {"message_id": 12}})

    monkeypatch.setattr(TelegramPublisher, "_request_bot_api", bot_request)
    with SESSION_LOCAL() as session:
        tenant_id = str(uuid4())
        asset_tenant_id = str(uuid4()) if case == "foreign-workspace" else tenant_id
        session.add(TenantOrm(id=tenant_id, name="Telegram workspace"))
        if asset_tenant_id != tenant_id:
            session.add(TenantOrm(id=asset_tenant_id, name="Different workspace"))
        session.flush()
        asset_id = str(uuid4())
        if case != "missing-asset":
            mime = {
                "managed-document": "application/pdf",
                "managed-png": "image/png",
            }.get(case, "image/jpeg")
            width, height = {
                "dimension-limit": (8400, 1600),
                "over-dimension-limit": (8402, 1600),
                "ratio-limit": (2000, 100),
                "over-ratio-limit": (2002, 100),
            }.get(case, (640, 480))
            codec = "png" if case == "managed-png" else "mjpeg"
            data = _telegram_fixture_image(codec, width, height)
            if case == "url-limit":
                data = data.ljust(5_000_000, b"\0")
            elif case == "invalid-image":
                data = b"not an image"
            size = {
                "over-url-limit": 5_000_001,
                "zero-size": 0,
                "object-size-mismatch": len(data) - 1,
            }.get(case, len(data))
            session.add(MediaAssetOrm(
                id=asset_id, tenant_id=asset_tenant_id, object_key=f"media/{asset_id}",
                content_type=mime, byte_size=size,
            ))
        if case == "unknown-size":
            get = session.get

            def get_with_missing_size(model, identity, **kwargs):
                if model is MediaAssetOrm and identity == asset_id:
                    return SimpleNamespace(tenant_id=tenant_id, content_type="image/jpeg", byte_size=None)
                return get(model, identity, **kwargs)

            monkeypatch.setattr(session, "get", get_with_missing_size)
        channel_id = str(uuid4())
        session.add(ChannelOrm(
            id=channel_id, tenant_id=tenant_id, platform="telegram", kind="destination",
            title="Telegram", external_id="demo", status="connected",
        ))
        session.commit()

        managed_url = f"{base_url}/{asset_id}"
        media_url = managed_url
        media_urls = None
        if case == "managed-jpeg-first":
            media_url = None
            media_urls = [managed_url, "https://external.example.test/other.jpg"]
        elif case == "foreign-origin":
            media_url = managed_url.replace("postbridge.example.test", "external.example.test")
        elif case == "suffix-fallback":
            media_url = "https://external.example.test/PHOTO.JPEG?v=1"
        elif case == "primary-document":
            media_url = "https://external.example.test/document.pdf"
            media_urls = [managed_url]
        elif case == "no-media":
            media_url = None

        result = create_content_with_plan_and_targets(
            session, tenant_id=tenant_id, channel_ids=[channel_id], title="Title", body_markdown="Body",
            target_status=PUBLICATION_TARGET_PENDING, media_url=media_url, media_urls=media_urls,
        )
        session.commit()
        target_id = result.publication_target_ids[0]
        executor = PublicationTargetExecutor(session)
        assert executor.run(target_id) == 1
        assert executor.run(target_id) == 0
        assert session.get(PublicationTargetOrm, target_id).external_post_id == "12"

    body = {"chat_id": "@demo", field: media_url or (media_urls[0] if media_urls else "Body")}
    if field != "text":
        body["caption"] = "Body"
    assert calls == [("POST", expected_method, body)]

    if case in {"managed-jpeg", "managed-jpeg-first", "managed-png", "url-limit", "invalid-image",
                "dimension-limit", "over-dimension-limit", "ratio-limit", "over-ratio-limit",
                "object-size-mismatch", "object-read-failure"}:
        assert storage_gets == [{"Bucket": "fixture-bucket", "Key": f"media/{asset_id}"}]
        assert storage_read_sizes == ([] if case == "object-read-failure" else [size + 1])
    else:
        assert storage_gets == []
        assert storage_read_sizes == []
