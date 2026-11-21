"""Qualified managed photos are decoded before the first Telegram request."""

from functools import lru_cache
from io import BytesIO
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import httpx
import pytest

from postbridge.api.main import serve_media
from postbridge.db import Base, ENGINE, SESSION_LOCAL, init_db
from postbridge.domain.errors import ExternalApiError
from postbridge.integrations.telegram import photo_validation
from postbridge.integrations.telegram.publisher import TelegramPublisher
from postbridge.models.domain import ChannelOrm, MediaAssetOrm, PublicationTargetOrm, TenantOrm
from postbridge.services.live_sync_publish_service import ingest_live_sync_publication, live_sync_executor_task_kwargs
from postbridge.services.media_assets import store_media_asset
from postbridge.workers.tasks import process_publication_target_task


@lru_cache
def _fixture_image(codec):
    return subprocess.check_output(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1080x1920",
         "-frames:v", "1", "-threads", "1", "-vcodec", codec, "-f", "image2pipe", "pipe:1"],
        timeout=15,
    )


@pytest.fixture
def telegram_workspace(monkeypatch):
    monkeypatch.setenv("MEDIA_STORAGE_TYPE", "s3")
    monkeypatch.setenv("S3_BUCKET", "fixture-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY", "fixture-access")
    monkeypatch.setenv("S3_SECRET_KEY", "fixture-secret")
    monkeypatch.setenv("MEDIA_BASE_URL", "https://postbridge.example.test/core/media")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fixture-token")
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    tenant_id, channel_id = str(uuid4()), str(uuid4())
    with SESSION_LOCAL() as session:
        session.add(TenantOrm(id=tenant_id, name="Telegram photo fixture"))
        session.flush()
        session.add(ChannelOrm(id=channel_id, tenant_id=tenant_id, platform="telegram",
                               kind="destination", title="Telegram", external_id="demo", status="connected"))
        session.commit()
    return tenant_id, channel_id


@pytest.mark.parametrize(
    ("codec", "damage", "lost_ack", "method"),
    [
        ("mjpeg", None, False, "sendPhoto"),
        ("mjpeg", 10, False, "sendDocument"),
        ("mjpeg", 25, False, "sendDocument"),
        ("mjpeg", 50, False, "sendDocument"),
        ("mjpeg", 75, False, "sendDocument"),
        ("mjpeg", "no-eoi", False, "sendDocument"),
        ("mjpeg", "false-eoi", False, "sendDocument"),
        ("png", None, False, "sendPhoto"),
        ("png", 50, False, "sendDocument"),
        ("png", "no-iend", False, "sendDocument"),
        ("png", "bad-crc", False, "sendDocument"),
        ("mjpeg", None, True, "sendPhoto"),
    ],
)
def test_managed_photo_integrity_via_upload_proxy_and_worker(telegram_workspace, codec, damage, lost_ack, method, monkeypatch):
    complete = _fixture_image(codec)
    mime = "image/jpeg" if codec == "mjpeg" else "image/png"
    data = complete
    if isinstance(damage, int):
        data = complete[:len(complete) * damage // 100]
    elif damage == "no-eoi":
        data = complete[:-2]
    elif damage == "false-eoi":
        data = complete[:len(complete) * 3 // 4] + b"\xff\xd9"
    elif damage == "no-iend":
        data = complete[:-12]
    elif damage == "bad-crc":
        data = complete[:-1] + bytes([complete[-1] ^ 1])

    objects = {}
    reads = []
    provider_calls = []

    class FixtureBody(BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    class FixtureStorage:
        def put_object(self, *, Bucket, Key, Body, ContentType):
            assert Bucket == "fixture-bucket"
            objects[Key] = (Body, ContentType)

        def generate_presigned_url(self, *args, **kwargs):
            return "https://private.example.test/attachment"

        def get_object(self, *, Bucket, Key):
            assert Bucket == "fixture-bucket"
            saved, content_type = objects[Key]
            return {"Body": FixtureBody(saved), "ContentLength": len(saved), "ContentType": content_type}

    def fail_on_http(*args, **kwargs):
        pytest.fail("Image classification must not make arbitrary HTTP requests")

    def fake_bot(self, http_method, url, **kwargs):
        provider_calls.append((http_method, url.rsplit("/", 1)[-1], kwargs["json"]))
        assert reads == [-1, len(data) + 1]
        if lost_ack:
            raise httpx.ReadTimeout("Fixture acknowledgement lost")
        return httpx.Response(200, request=httpx.Request(http_method, url), json={"result": {"message_id": 12}})

    monkeypatch.setattr("postbridge.infrastructure.media_storage._s3_client", lambda _: FixtureStorage())
    monkeypatch.setattr(httpx, "Client", fail_on_http)
    monkeypatch.setattr(TelegramPublisher, "_request_bot_api", fake_bot)

    tenant_id, channel_id = telegram_workspace
    with SESSION_LOCAL() as session:
        asset = store_media_asset(session, tenant_id=tenant_id, data=data, content_type=mime)
        row = session.get(MediaAssetOrm, asset["media_asset_id"])
        assert row.byte_size == len(data)
        assert asset["url"].endswith("/core/media/" + row.id)
        assert objects == {row.object_key: (data, mime)}

        proxy = serve_media(row.id)
        assert proxy.status_code == 200 and proxy.media_type == mime and proxy.body == data
        post = {"source_post_id": str(uuid4()), "text": "Fixture", "media_url": asset["url"]}
        route = dict(tenant_id=tenant_id, target_core_channel_id=channel_id, source_channel="pb/fixture",
                     target_channel="demo", target_platform="telegram", post=post)
        ingested = ingest_live_sync_publication(session, **route, correlation_id="fixture")

    kwargs = live_sync_executor_task_kwargs(source_post_id=post["source_post_id"], **route)
    if lost_ack:
        with pytest.raises(ExternalApiError):
            process_publication_target_task.run(ingested.target_id, **kwargs)
    else:
        assert process_publication_target_task.run(ingested.target_id, **kwargs) == 1
    assert process_publication_target_task.run(ingested.target_id, **kwargs) == 0
    with SESSION_LOCAL() as session:
        assert ingest_live_sync_publication(session, **route, correlation_id="replay").skipped
        target = session.get(PublicationTargetOrm, ingested.target_id)
        assert target.status == ("unknown" if lost_ack else "published")
    field = "photo" if method == "sendPhoto" else "document"
    assert provider_calls == [("POST", method, {"chat_id": "@demo", field: asset["url"], "caption": "Fixture"})]


def test_decoder_keeps_a_deadline_and_reaps_its_process(monkeypatch):
    real_popen = subprocess.Popen
    children = []

    def time_out(command, **kwargs):
        assert command[command.index("-protocol_whitelist") + 1] == "file,pipe"
        assert command[command.index("-frames:v") + 1] == "1"
        assert command[command.index("-max_pixels") + 1] == "16777216"
        child = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(photo_validation.subprocess, "Popen", time_out)
    monkeypatch.setattr(photo_validation, "_DECODER_TIMEOUT_SECONDS", 0.05)
    assert not photo_validation.has_decodable_photo_frame(Path("fixture.jpg"), png=False)
    assert children[0].returncode is not None
