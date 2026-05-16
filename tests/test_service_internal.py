"""Internal service API (CORE_SERVICE_TOKEN + X-Tenant-Id)."""

import base64
from io import BytesIO
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from postbridge.db import Base, ENGINE, BatchImportRunOrm, init_db  # noqa: E402
from postbridge.models.domain import ChannelOrm, TenantOrm  # noqa: E402
from postbridge.services.ai_image_generation import build_post_image_prompt  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    session = __import__("postbridge.db", fromlist=["SESSION_LOCAL"]).SESSION_LOCAL()
    session.query(BatchImportRunOrm).delete()
    session.commit()
    session.close()
    yield


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        "postbridge.api.service_internal.process_publication_target_task.delay",
        MagicMock(),
    )
    monkeypatch.setattr(
        "postbridge.api.service_internal.process_batch_import_run_task.delay",
        MagicMock(),
    )
    monkeypatch.setattr(
        "postbridge.api.service_internal.process_media_generation_job_task.delay",
        MagicMock(),
    )
    from postbridge.api.main import app

    return TestClient(app)


def _headers(tenant_id: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer svc-test-secret",
        "X-Tenant-Id": tenant_id,
        "X-Correlation-Id": str(uuid4()),
    }


@pytest.mark.parametrize(
    ("configured_locale", "expected_locked"),
    [
        ("ru", True),
        ("en", False),
        ("", False),
    ],
)
def test_runtime_config_locks_locale_only_for_ru(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    configured_locale: str,
    expected_locked: bool,
):
    monkeypatch.setenv("POSTBRIDGE_DEFAULT_LOCALE", configured_locale)

    response = client.get("/internal/service/runtime-config", headers=_headers(str(uuid4())))

    assert response.status_code == 200
    assert response.json()["locale_locked"] is expected_locked


def test_service_rejects_bad_token(client: TestClient):
    tid = str(uuid4())
    r = client.post(
        "/internal/service/tenants/ensure",
        json={"name": "W"},
        headers={**_headers(tid), "Authorization": "Bearer wrong"},
    )
    assert r.status_code == 403


def test_ensure_tenant_and_channel_and_publication(client: TestClient):
    tid = str(uuid4())
    h = _headers(tid)
    r1 = client.post("/internal/service/tenants/ensure", json={"name": "Acme"}, headers=h)
    assert r1.status_code == 200
    assert r1.json()["tenant_id"] == tid

    ch_id = str(uuid4())
    r2 = client.post(
        "/internal/service/channels/ensure",
        json={
            "channel_id": ch_id,
            "platform": "max",
            "title": "Max",
            "external_id": "chat-1",
            "credential": {
                "auth_type": "api_key",
                "encrypted_secret": '{"base_url":"https://x","token":"t"}',
                "status": "active",
            },
        },
        headers=h,
    )
    assert r2.status_code == 200
    assert r2.json()["channel_id"] == ch_id

    r3 = client.post(
        "/internal/service/publications",
        json={
            "core_channel_ids": [ch_id],
            "title": "Hi",
            "body_markdown": "Body",
            "dispatch": False,
        },
        headers=h,
    )
    assert r3.status_code == 200
    body = r3.json()
    assert len(body["publication_target_ids"]) == 1

    tid_target = body["publication_target_ids"][0]
    r4 = client.get(f"/internal/service/publication-targets/{tid_target}", headers=h)
    assert r4.status_code == 200
    assert r4.json()["status"] == "pending"


def test_tenant_settings_store_image_style_prompt(client: TestClient):
    tid = str(uuid4())
    h = _headers(tid)
    client.post("/internal/service/tenants/ensure", json={"name": "Acme"}, headers=h)

    update = client.put(
        "/internal/service/tenant/settings",
        json={"image_style_prompt": "minimal clay bridge style"},
        headers=h,
    )

    assert update.status_code == 200, update.text
    assert update.json()["image_style_prompt"] == "minimal clay bridge style"

    read = client.get("/internal/service/tenant/settings", headers=h)
    assert read.status_code == 200, read.text
    assert read.json()["image_style_prompt"] == "minimal clay bridge style"


def test_service_media_upload_local(client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MEDIA_STORAGE_TYPE", "local")
    monkeypatch.setenv("MEDIA_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("MEDIA_BASE_URL", "http://testserver/media")

    tid = str(uuid4())
    h = _headers(tid)
    client.post("/internal/service/tenants/ensure", json={"name": "W"}, headers=h)

    r = client.post(
        "/internal/service/media/upload",
        headers=h,
        files={"file": ("x.png", BytesIO(b"abc"), "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["media_asset_id"]
    assert body["url"].startswith("http://testserver/media/")
    assert (tmp_path / f"tenants/{tid}/media/{body['media_asset_id']}.png").is_file()


def test_post_image_prompt_combines_style_request_and_post_context():
    prompt = build_post_image_prompt(
        user_prompt="show a bridge between post and channels",
        title="Platform updates",
        summary="New visual generation flow",
        content_md="Users can generate an image from the post editor.",
        style_prompt="Minimal matte 3D objects on a warm background.",
    )

    assert "Minimal matte 3D objects" in prompt
    assert "show a bridge between post and channels" in prompt
    assert "Platform updates" in prompt
    assert "New visual generation flow" in prompt
    assert "Users can generate an image" in prompt
    assert "No text, letters, captions, UI labels, or logos" in prompt


def test_post_image_prompt_truncates_context_within_label_limits():
    prompt = build_post_image_prompt(
        user_prompt="x" * 900,
        title="y" * 400,
        summary="z" * 700,
        content_md="w" * 1700,
        style_prompt="Style.",
    )

    line_limits = {
        "User image request: ": 800,
        "Post title: ": 300,
        "Post summary: ": 600,
        "Post body excerpt: ": 1600,
    }
    for line in prompt.splitlines():
        for prefix, limit in line_limits.items():
            if line.startswith(prefix):
                value = line.removeprefix(prefix)
                assert len(value) <= limit
                assert value.endswith("...")


def test_generate_image_bytes_requests_url_and_parses_usage(monkeypatch: pytest.MonkeyPatch):
    from postbridge.services.ai_image_generation import generate_image_bytes

    monkeypatch.setenv("AI_GATEWAY_BASE_URL", "https://provider.example.test/api")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gt-test")
    monkeypatch.setenv("AI_IMAGE_GENERATION_MODEL", "gpt-image-2")
    monkeypatch.setenv("AI_GATEWAY_TIMEOUT_SECONDS", "90")

    captured: dict[str, object] = {}

    class FakeResponse:
        def __init__(
            self,
            *,
            status_code: int = 200,
            body: dict | None = None,
            content: bytes = b"",
            headers: dict[str, str] | None = None,
        ) -> None:
            self.status_code = status_code
            self._body = body or {}
            self.content = content
            self.headers = headers or {}
            self.text = "{}"

        def json(self) -> dict:
            return self._body

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            _ = args
            captured.setdefault("timeouts", []).append(kwargs.get("timeout"))
            captured.setdefault("follow_redirects", []).append(kwargs.get("follow_redirects"))

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse(
                body={
                    "data": [{"url": "https://cdn.example.test/generated.png"}],
                    "usage": {"total_tokens": 123},
                }
            )

        def get(self, url: str) -> FakeResponse:
            captured["image_url"] = url
            return FakeResponse(
                content=b"image-bytes",
                headers={"content-type": "image/png"},
            )

    monkeypatch.setattr("postbridge.services.ai_image_generation.httpx.Client", FakeClient)

    result = generate_image_bytes("make a bridge", correlation_id="corr-image-test")

    assert captured["url"] == "https://provider.example.test/api/v1/images/generations"
    assert captured["image_url"] == "https://cdn.example.test/generated.png"
    assert captured["timeouts"] == [90.0, 90.0]
    assert captured["follow_redirects"] == [None, True]
    assert captured["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer gt-test",
        "X-Correlation-Id": "corr-image-test",
    }
    assert captured["json"] == {
        "model": "gpt-image-2",
        "prompt": "make a bridge",
        "n": 1,
        "size": "1536x1024",
        "response_format": "url",
    }
    assert result.data == b"image-bytes"
    assert result.content_type == "image/png"
    assert result.usage_tokens_charged == 123


def test_service_media_generate_local(client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch):
    from postbridge.db import SESSION_LOCAL
    from postbridge.models.domain import ContentItemOrm
    from postbridge.services.ai_image_generation import ImageGenerationResult
    from postbridge.services.postbridge_workspace_content import content_item_to_api_dict

    monkeypatch.setenv("AI_GATEWAY_ENABLED", "1")
    monkeypatch.setenv("MEDIA_STORAGE_TYPE", "local")
    monkeypatch.setenv("MEDIA_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("MEDIA_BASE_URL", "http://testserver/media")

    captured: dict[str, str | None] = {}

    def fake_generate_image_bytes(
        prompt: str,
        *,
        model: str | None = None,
        correlation_id: str | None = None,
    ) -> ImageGenerationResult:
        captured["prompt"] = prompt
        captured["model"] = model
        captured["correlation_id"] = correlation_id
        return ImageGenerationResult(
            data=base64.b64decode("iVBORw0KGgo="),
            content_type="image/png",
            usage_tokens_charged=77,
        )

    monkeypatch.setattr(
        "postbridge.api.service_internal.generate_image_bytes",
        fake_generate_image_bytes,
    )

    tid = str(uuid4())
    h = _headers(tid)
    correlation_id = h["X-Correlation-Id"]
    client.post("/internal/service/tenants/ensure", json={"name": "W"}, headers=h)
    client.put(
        "/internal/service/tenant/settings",
        headers=h,
        json={"image_style_prompt": "tenant soft clay style"},
    )
    content_id = str(uuid4())
    session = SESSION_LOCAL()
    try:
        session.add(
            ContentItemOrm(
                id=content_id,
                tenant_id=tid,
                source_type="postbridge",
                title="Launch digest",
                body_markdown="The platform now helps prepare posts faster.",
                status="draft",
            )
        )
        session.commit()
    finally:
        session.close()

    r = client.post(
        "/internal/service/media/generate",
        headers=h,
        json={
            "target": "cover",
            "prompt": "bridge metaphor",
            "title": "Launch digest",
            "summary": "A short release note",
            "content_md": "The platform now helps prepare posts faster.",
            "content_item_id": content_id,
            "model": "image-test-model",
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["media_asset_id"]
    assert body["url"].startswith("http://testserver/media/")
    assert body["prompt"] == captured["prompt"]
    assert body["usage_tokens_charged"] == 77
    assert captured["model"] == "image-test-model"
    assert captured["correlation_id"] == correlation_id
    assert "tenant soft clay style" in captured["prompt"]
    assert "bridge metaphor" in captured["prompt"]
    assert "Launch digest" in captured["prompt"]
    assert (tmp_path / f"tenants/{tid}/media/{body['media_asset_id']}.png").is_file()
    session = SESSION_LOCAL()
    try:
        item = session.get(ContentItemOrm, content_id)
        assert item is not None
        assert content_item_to_api_dict(item)["cover_image_url"] == body["url"]
    finally:
        session.close()


def test_service_media_generation_job_queues_and_worker_completes(
    client: TestClient,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    from postbridge.db import SESSION_LOCAL
    from postbridge.models.domain import ContentItemOrm, MediaGenerationJobOrm
    from postbridge.services.ai_image_generation import ImageGenerationResult
    from postbridge.workers.media_generation_tasks import process_media_generation_job_task

    monkeypatch.setenv("AI_GATEWAY_ENABLED", "1")
    monkeypatch.setenv("MEDIA_STORAGE_TYPE", "local")
    monkeypatch.setenv("MEDIA_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("MEDIA_BASE_URL", "http://testserver/media")

    queued: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "postbridge.api.service_internal.process_media_generation_job_task.delay",
        lambda job_id, correlation_id=None: queued.append((job_id, correlation_id)),
    )

    def fake_generate_image_bytes(
        prompt: str,
        *,
        model: str | None = None,
        correlation_id: str | None = None,
    ) -> ImageGenerationResult:
        assert "Async launch" in prompt
        assert model == "image-test-model"
        assert correlation_id is not None
        return ImageGenerationResult(
            data=base64.b64decode("iVBORw0KGgo="),
            content_type="image/png",
            usage_tokens_charged=88,
        )

    monkeypatch.setattr(
        "postbridge.workers.media_generation_tasks.generate_image_bytes",
        fake_generate_image_bytes,
    )

    tid = str(uuid4())
    h = _headers(tid)
    client.post("/internal/service/tenants/ensure", json={"name": "W"}, headers=h)
    content_id = str(uuid4())
    session = SESSION_LOCAL()
    try:
        session.add(
            ContentItemOrm(
                id=content_id,
                tenant_id=tid,
                source_type="postbridge",
                title="Async launch",
                body_markdown="Generate this in the background.",
                status="draft",
            )
        )
        session.commit()
    finally:
        session.close()

    r = client.post(
        "/internal/service/media/generation-jobs",
        headers=h,
        json={
            "target": "media",
            "title": "Async launch",
            "content_md": "Generate this in the background.",
            "content_item_id": content_id,
            "requester_user_id": "user-admin",
            "model": "image-test-model",
        },
    )

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["content_item_id"] == content_id
    assert body["requester_user_id"] == "user-admin"
    assert queued == [(body["id"], h["X-Correlation-Id"])]

    result = process_media_generation_job_task.run(body["id"], h["X-Correlation-Id"])
    assert result["status"] == "completed"

    get_r = client.get(
        f"/internal/service/media/generation-jobs/{body['id']}",
        headers=h,
    )
    assert get_r.status_code == 200, get_r.text
    completed = get_r.json()
    assert completed["status"] == "completed"
    assert completed["url"].startswith("http://testserver/media/")
    assert completed["usage_tokens_charged"] == 88

    session = SESSION_LOCAL()
    try:
        item = session.get(ContentItemOrm, content_id)
        assert item is not None
        assert item.media_url == completed["url"]
        assert item.media_urls == [completed["url"]]
        assert (item.body_structured_json or "").find(completed["url"]) >= 0
        job = session.get(MediaGenerationJobOrm, body["id"])
        assert job is not None
        assert job.status == "completed"
    finally:
        session.close()


def test_service_media_generation_job_records_queue_failure(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AI_GATEWAY_ENABLED", "1")

    def fail_delay(_job_id: str, _correlation_id: str | None = None) -> None:
        raise RuntimeError("queue offline")

    monkeypatch.setattr(
        "postbridge.api.service_internal.process_media_generation_job_task.delay",
        fail_delay,
    )

    tid = str(uuid4())
    h = _headers(tid)
    client.post("/internal/service/tenants/ensure", json={"name": "W"}, headers=h)

    r = client.post(
        "/internal/service/media/generation-jobs",
        headers=h,
        json={"target": "cover", "title": "Async launch"},
    )

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "MEDIA_GENERATION_QUEUE_FAILED"


def test_service_media_generate_requires_prompt_or_post_context(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_GATEWAY_ENABLED", "1")
    tid = str(uuid4())
    h = _headers(tid)
    client.post("/internal/service/tenants/ensure", json={"name": "W"}, headers=h)

    r = client.post("/internal/service/media/generate", headers=h, json={})

    assert r.status_code == 422, r.text
    assert r.json()["code"] == "VALIDATION_IMAGE_PROMPT_REQUIRED"


def test_create_empty_postbridge_draft_allowed(client: TestClient):
    tid = str(uuid4())
    h = _headers(tid)
    client.post("/internal/service/tenants/ensure", json={"name": "W"}, headers=h)

    r = client.post(
        "/internal/service/content-items/postbridge",
        json={
            "content_md": "",
            "status": "draft",
            "saas_workspace_id": "ws-1",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content_md"] == ""
    assert body["status"] == "draft"


def test_get_target_wrong_tenant(client: TestClient):
    tid_a, tid_b = str(uuid4()), str(uuid4())
    client.post("/internal/service/tenants/ensure", json={}, headers=_headers(tid_a))
    ch_id = str(uuid4())
    client.post(
        "/internal/service/channels/ensure",
        json={"channel_id": ch_id, "platform": "max", "title": "M", "external_id": "x"},
        headers=_headers(tid_a),
    )
    r = client.post(
        "/internal/service/publications",
        json={"core_channel_ids": [ch_id], "title": "T"},
        headers=_headers(tid_a),
    )
    target_id = r.json()["publication_target_ids"][0]
    r2 = client.get(f"/internal/service/publication-targets/{target_id}", headers=_headers(tid_b))
    assert r2.status_code == 422


def test_service_bridges_and_rss_feeds(client: TestClient):
    tid = str(uuid4())
    h = _headers(tid)
    client.post("/internal/service/tenants/ensure", json={"name": "BridgeCo"}, headers=h)
    src = str(uuid4())
    tgt = str(uuid4())
    client.post(
        "/internal/service/channels/ensure",
        json={
            "channel_id": src,
            "platform": "telegram",
            "title": "Src",
            "external_id": "-1001",
        },
        headers=h,
    )
    client.post(
        "/internal/service/channels/ensure",
        json={
            "channel_id": tgt,
            "platform": "max",
            "title": "Tgt",
            "external_id": "max-1",
        },
        headers=h,
    )
    r_create = client.post(
        "/internal/service/bridges",
        json={
            "saas_user_id": "saas-u1",
            "source_channel_id": src,
            "target_channel_id": tgt,
            "mode": "live_sync",
            "status": "active",
        },
        headers=h,
    )
    assert r_create.status_code == 200, r_create.text
    bridge_id = r_create.json()["id"]
    r_list = client.get(
        "/internal/service/bridges",
        params={"saas_user_id": "saas-u1"},
        headers=h,
    )
    assert r_list.status_code == 200
    assert len(r_list.json()["items"]) == 1
    r_count = client.get(
        "/internal/service/bridges/live-sync-count",
        params={"saas_user_id": "saas-u1"},
        headers=h,
    )
    assert r_count.status_code == 200
    assert r_count.json()["count"] == 1
    r_tgt = client.get(
        "/internal/service/bridges/live-sync-targets",
        params={"source_channel_id": src},
        headers=h,
    )
    assert r_tgt.status_code == 200
    items = r_tgt.json()["items"]
    assert len(items) == 1
    assert items[0]["target_channel_id"] == tgt
    assert items[0]["bridge_id"] == bridge_id
    assert items[0]["bridge_settings"] is None

    r_patch = client.patch(
        f"/internal/service/bridges/{bridge_id}",
        params={"saas_user_id": "saas-u1"},
        json={
            "settings_json": {
                "adaptation": {
                    "mode": "ai_auto",
                    "instructions": "Keep it compact.",
                }
            }
        },
        headers=h,
    )
    assert r_patch.status_code == 200, r_patch.text
    assert r_patch.json()["settings_json"]["adaptation"]["mode"] == "ai_auto"

    r_rss = client.post(
        "/internal/service/rss-feeds",
        json={"source_channel_id": src, "saas_user_id": "saas-u1"},
        headers=h,
    )
    assert r_rss.status_code == 200, r_rss.text
    feed_id = r_rss.json()["id"]
    r_rss_list = client.get(
        "/internal/service/rss-feeds",
        params={"saas_user_id": "saas-u1"},
        headers=h,
    )
    assert r_rss_list.status_code == 200
    assert any(x["id"] == feed_id for x in r_rss_list.json()["items"])
    r_rss_del = client.delete(
        f"/internal/service/rss-feeds/{feed_id}",
        params={"saas_user_id": "saas-u1"},
        headers=h,
    )
    assert r_rss_del.status_code == 204
    r_bridge_del = client.delete(
        f"/internal/service/bridges/{bridge_id}",
        params={"saas_user_id": "saas-u1"},
        headers=h,
    )
    assert r_bridge_del.status_code == 204
