"""Production contracts: tenant isolation, revisions, durable jobs and a real MP4."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from postbridge.db import Base
from postbridge.domain.errors import ValidationError
from postbridge.models.domain import TenantOrm, MediaAssetOrm, VideoProductionJobOrm, VideoRevisionOrm
from postbridge.services.media_assets import delete_media_asset
from postbridge.services.video_studio import (VideoCreate, VideoRevise, create_project, get_project,
    revise_project, cancel_project, list_projects, store_video_material, project_by_request)
from postbridge.services.video_media import inspect_media
from postbridge.workers import video_tasks

# Small, valid PNG. FFmpeg, rather than the declared MIME type, validates uploads.
from contextlib import contextmanager
import base64
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=")


@contextmanager
def error(code):
    with pytest.raises(ValidationError) as caught:
        yield
    assert caught.value.code == code


@pytest.fixture()
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_STUDIO_ENABLED", "1")
    monkeypatch.setenv("MEDIA_STORAGE_TYPE", "local")
    monkeypatch.setenv("MEDIA_STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("MEDIA_BASE_URL", "https://media.example.test/media")
    # Keep the focused suite isolated, including all worker sessions and commits.
    engine = create_engine(f"sqlite:///{tmp_path}/video.sqlite")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(video_tasks, "SESSION_LOCAL", factory)
    first, second = str(uuid4()), str(uuid4())
    with factory() as session:
        session.add_all([TenantOrm(id=first, name="First"), TenantOrm(id=second, name="Second")])
        session.commit()
        image = tmp_path / "source.png"
        image.write_bytes(PNG)
        material = store_video_material(session, first, image)
        yield session, first, second, material, tmp_path
    engine.dispose()


def request(material, **kwargs):
    return VideoCreate(brief="A product demo", request_key=kwargs.pop("request_key", str(uuid4())),
        scenes=[{"asset_id": material["media_asset_id"], "duration_seconds": 1, "caption": "Demo"}], **kwargs)


def test_upload_probes_actual_content_and_rejects_invalid(state):
    session, tenant, _, material, tmp = state
    assert material["kind"] == "image"
    assert material["url"].endswith(".png")
    assert session.get(MediaAssetOrm, material["media_asset_id"]).content_type == "image/png"
    bad = tmp / "fake.mp4"
    bad.write_text("<script>alert('not media')</script>")
    with error("VIDEO_INVALID_MEDIA"):
        store_video_material(session, tenant, bad)


@pytest.mark.parametrize("extension,content_type", [("jpg", "image/jpeg"), ("webp", "image/webp")])
def test_supported_image_formats_keep_correct_mime_and_extension(state, extension, content_type):
    import subprocess
    session, tenant, _, _, tmp = state
    converted = tmp / f"converted.{extension}"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(tmp / "source.png"),
                    "-vf", "scale=2:2", "-frames:v", "1", "-threads", "1", "-filter_threads", "1", str(converted)],
                   check=True, capture_output=True, timeout=20)
    result = store_video_material(session, tenant, converted)
    assert result["kind"] == "image"
    assert result["url"].endswith(f".{extension}")
    assert session.get(MediaAssetOrm, result["media_asset_id"]).content_type == content_type


def test_cross_tenant_material_and_project_are_inaccessible(state):
    session, tenant, other, material, _ = state
    with error("VIDEO_ASSET_NOT_FOUND"):
        create_project(session, other, request(material))
    session.rollback()
    project = create_project(session, tenant, request(material))
    assert list_projects(session, other)["items"] == []
    with error("VIDEO_PROJECT_NOT_FOUND"):
        get_project(session, other, project["id"])


def test_replayed_create_does_not_enqueue_again_and_rejects_changed_body(state):
    session, tenant, _, material, _ = state
    payload = request(material)
    first = create_project(session, tenant, payload)
    again = create_project(session, tenant, payload)
    assert first["id"] == again["id"]
    assert session.scalar(select(func.count()).select_from(VideoProductionJobOrm)) == 1
    with error("VIDEO_REQUEST_CONFLICT"):
        create_project(session, tenant, payload.model_copy(update={"brief": "Different"}))


def test_empty_project_is_an_honest_draft(state):
    session, tenant, _, _, _ = state
    project = create_project(session, tenant, VideoCreate(brief="An idea", request_key=str(uuid4())))
    assert project["current"]["status"] == "draft"
    assert project["current"]["job_id"] is None


def test_revision_conflicts_and_replays_preserve_inputs(state):
    session, tenant, _, material, _ = state
    project = create_project(session, tenant, request(material))
    payload = VideoRevise(**request(material).model_dump(), expected_version=1)
    revised = revise_project(session, tenant, project["id"], payload)
    assert revised["current_version"] == 2
    assert len(revised["revisions"]) == 2
    assert revise_project(session, tenant, project["id"], payload)["current_version"] == 2
    stale = payload.model_copy(update={"request_key": str(uuid4())})
    with error("VIDEO_VERSION_CONFLICT"):
        revise_project(session, tenant, project["id"], stale)
    assert session.scalar(select(func.count()).select_from(VideoRevisionOrm)) == 2


def test_replayed_create_keeps_original_job_after_a_new_revision(state):
    session, tenant, _, material, _ = state
    original = request(material)
    project = create_project(session, tenant, original)
    revised = revise_project(session, tenant, project["id"], VideoRevise(**request(material).model_dump(), expected_version=1))
    replay = create_project(session, tenant, original)
    assert replay["current_version"] == 2
    assert replay["current"]["job_id"] == revised["current"]["job_id"]
    assert replay["requested_revision"]["job_id"] == project["current"]["job_id"]
    assert project_by_request(session, tenant, original.request_key)["requested_revision"]["version"] == 1


def test_request_key_cannot_refer_to_two_projects_in_one_tenant(state):
    session, tenant, other, _, _ = state
    first = VideoCreate(brief="First", request_key=str(uuid4()))
    original = create_project(session, tenant, first)
    second = create_project(session, tenant, VideoCreate(brief="Second", request_key=str(uuid4())))
    with error("VIDEO_REQUEST_CONFLICT"):
        revise_project(session, tenant, second["id"], VideoRevise(
            brief="Second", request_key=first.request_key, expected_version=1))
    session.rollback()
    # Request namespaces are tenant-scoped.
    assert create_project(session, other, first)["id"] != original["id"]


def test_queue_limit_blocks_unbounded_work(state):
    session, tenant, _, material, _ = state
    create_project(session, tenant, request(material))
    create_project(session, tenant, request(material))
    with error("VIDEO_QUEUE_FULL"):
        create_project(session, tenant, request(material))


def test_cancelled_job_never_creates_an_output(state):
    session, tenant, _, material, _ = state
    project = create_project(session, tenant, request(material))
    cancelled = cancel_project(session, tenant, project["id"], 1)
    assert cancelled["current"]["status"] == "cancelled"
    video_tasks.process_video_job.run(project["current"]["job_id"])
    assert session.scalar(select(func.count()).select_from(MediaAssetOrm)) == 1


def test_real_render_has_video_audio_and_idempotent_completion(state):
    session, tenant, _, material, tmp = state
    project = create_project(session, tenant, request(material))
    job_id = project["current"]["job_id"]
    video_tasks.process_video_job.run(job_id)
    session.expire_all()
    rendered = get_project(session, tenant, project["id"])
    assert rendered["current"]["status"] == "succeeded"
    output = session.get(MediaAssetOrm, rendered["current"]["output_asset_id"])
    metadata = inspect_media(tmp / "storage" / output.object_key)
    assert (metadata["width"], metadata["height"]) == (1080, 1920)
    assert metadata["has_audio"]
    assert 0.9 < metadata["duration_seconds"] < 1.2
    video_tasks.process_video_job.run(job_id)
    assert rendered["current"]["scenes"][0]["preview_url"].endswith(".jpg")
    assert session.scalar(select(func.count()).select_from(MediaAssetOrm)) == 4


def test_old_job_finishing_does_not_replace_current_revision(state):
    session, tenant, _, material, _ = state
    project = create_project(session, tenant, request(material))
    second = revise_project(session, tenant, project["id"], VideoRevise(**request(material).model_dump(), expected_version=1))
    video_tasks.process_video_job.run(project["current"]["job_id"])
    session.expire_all()
    current = get_project(session, tenant, second["id"])
    assert current["current_version"] == 2
    assert current["current"]["status"] == "queued"
    assert current["revisions"][1]["status"] == "succeeded"


def test_active_lease_prevents_concurrent_worker_and_expired_attempt_limit(state):
    session, tenant, _, material, _ = state
    project = create_project(session, tenant, request(material))
    job = session.get(VideoProductionJobOrm, project["current"]["job_id"])
    job.status, job.lease_until, job.attempts = "running", datetime.now(UTC) + timedelta(minutes=5), 1
    session.commit()
    video_tasks.process_video_job.run(job.id)
    session.refresh(job)
    assert job.attempts == 1
    job.lease_until, job.attempts = datetime.now(UTC) - timedelta(minutes=1), 3
    session.commit()
    video_tasks.process_video_job.run(job.id)
    session.refresh(job)
    assert job.status == "failed" and job.error_code == "VIDEO_ATTEMPT_LIMIT"


def test_expired_worker_lease_recovers_the_same_job(state):
    session, tenant, _, material, _ = state
    project = create_project(session, tenant, request(material))
    job = session.get(VideoProductionJobOrm, project["current"]["job_id"])
    job.status, job.lease_until, job.attempts = "running", datetime.now(UTC) - timedelta(seconds=1), 1
    session.commit()
    video_tasks.process_video_job.run(job.id)
    session.refresh(job)
    assert job.status == "succeeded"
    assert job.attempts == 2
    assert session.scalar(select(func.count()).select_from(VideoProductionJobOrm)) == 1


def test_referenced_input_and_output_cannot_be_deleted(state):
    session, tenant, _, material, _ = state
    project = create_project(session, tenant, request(material))
    with error("VALIDATION_MEDIA_ASSET_IN_USE"):
        delete_media_asset(session, tenant_id=tenant, media_asset_id=material["media_asset_id"])
    session.rollback()
    video_tasks.process_video_job.run(project["current"]["job_id"])
    session.expire_all()
    output_id = get_project(session, tenant, project["id"])["current"]["output_asset_id"]
    with error("VALIDATION_MEDIA_ASSET_IN_USE"):
        delete_media_asset(session, tenant_id=tenant, media_asset_id=output_id)


def test_disabled_studio_does_not_create_work(state, monkeypatch):
    session, tenant, _, material, _ = state
    monkeypatch.setenv("VIDEO_STUDIO_ENABLED", "0")
    with error("VIDEO_STUDIO_DISABLED"):
        create_project(session, tenant, request(material))


def test_missing_broker_keeps_job_in_database_for_dispatch(state, monkeypatch):
    session, tenant, _, material, _ = state
    project = create_project(session, tenant, request(material))
    def unavailable(*args, **kwargs):
        raise ConnectionError("broker unavailable")
    monkeypatch.setattr(video_tasks.celery_app, "send_task", unavailable)
    video_tasks.dispatch_video_jobs.run()
    session.expire_all()
    assert get_project(session, tenant, project["id"])["current"]["status"] == "queued"


def test_service_api_checks_auth_and_rejects_invalid_payloads(state):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from postbridge.api.video_studio import router
    from postbridge.db import get_db_session
    from postbridge.domain.errors import PostbridgeError
    session, tenant, _, material, _ = state
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_session] = lambda: session
    @app.exception_handler(PostbridgeError)
    async def error_response(request, exc):
        return JSONResponse({"code": exc.code}, status_code=400)
    client = TestClient(app)
    headers = {"Authorization": "Bearer svc-test-secret", "X-Tenant-Id": tenant}
    assert client.get("/internal/service/videos/capabilities").status_code == 400
    assert client.get("/internal/service/videos/capabilities", headers=headers).json()["enabled"]
    assert client.post("/internal/service/videos", headers=headers,
        json={"brief": "", "request_key": "short"}).status_code == 422
    response = client.post("/internal/service/videos", headers=headers, json=request(material).model_dump())
    assert response.status_code == 201, response.text
    assert response.json()["current"]["status"] == "queued"


@pytest.mark.parametrize("filename", ["20261019_video_studio.py", "20261019_video_autopilot.py"])
def test_migration_works_when_baseline_already_created_tables(state, filename):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import runpy
    session, *_ = state
    migration = runpy.run_path(str(Path(__file__).resolve().parents[1] / "alembic/versions" / filename))
    with session.get_bind().begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
            migration["upgrade"]()


def test_autopilot_migration_upgrades_existing_video_tables(tmp_path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    import runpy
    engine = create_engine(f"sqlite:///{tmp_path}/upgrade.sqlite")
    try:
        with engine.begin() as connection:
            TenantOrm.__table__.create(connection)
            MediaAssetOrm.__table__.create(connection)
            directory = Path(__file__).resolve().parents[1] / "alembic/versions"
            with Operations.context(MigrationContext.configure(connection)):
                runpy.run_path(str(directory / "20261019_video_studio.py"))["upgrade"]()
                runpy.run_path(str(directory / "20261019_video_autopilot.py"))["upgrade"]()
            assert "production_options" in {c["name"] for c in inspect(connection).get_columns("video_revisions")}
            assert "progress_json" in {c["name"] for c in inspect(connection).get_columns("video_production_jobs")}
            assert next(i for i in inspect(connection).get_indexes("video_revisions") if i["name"] == "uq_video_tenant_request")["unique"]
    finally:
        engine.dispose()


def test_cancellation_during_render_does_not_attach_output(state, monkeypatch):
    session, tenant, _, material, _ = state
    project = create_project(session, tenant, request(material))
    original = video_tasks.render_montage
    def render_and_cancel(*args, **kwargs):
        output = original(*args, **kwargs)
        cancel_project(session, tenant, project["id"], 1)
        return output
    monkeypatch.setattr(video_tasks, "render_montage", render_and_cancel)
    video_tasks.process_video_job.run(project["current"]["job_id"])
    session.expire_all()
    assert get_project(session, tenant, project["id"])["current"]["status"] == "cancelled"
    assert session.scalar(select(func.count()).select_from(MediaAssetOrm)) == 1


def test_mixed_clip_and_screenshot_normalize_audio_and_keep_caption_literal(state):
    import subprocess
    session, tenant, _, material, tmp = state
    clip = tmp / "clip.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x120:r=24",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "1", "-c:v", "libx264", "-threads", "1",
        "-c:a", "aac", str(clip)], check=True, capture_output=True, timeout=20)
    video = store_video_material(session, tenant, clip)
    body = request(material).model_dump()
    body["scenes"][0]["caption"] = "100% %{pts} 'quoted' Привет"
    body["scenes"].append({"asset_id": video["media_asset_id"], "duration_seconds": 1, "caption": "Second scene"})
    project = create_project(session, tenant, VideoCreate(**body))
    video_tasks.process_video_job.run(project["current"]["job_id"])
    session.expire_all()
    current = get_project(session, tenant, project["id"])["current"]
    assert current["status"] == "succeeded"
    output = session.get(MediaAssetOrm, current["output_asset_id"])
    path = tmp / "storage" / output.object_key
    assert 1.9 < inspect_media(path)["duration_seconds"] < 2.3
    # Decode the complete output to catch concat errors beyond container metadata.
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
                   check=True, capture_output=True, timeout=20)


def test_separate_cover_is_owned_versioned_and_uses_scene_text_not_private_brief(state):
    from postbridge.services.video_post_drafts import PrepareVideoPost, prepare_post_draft
    from postbridge.services.postbridge_workspace_content import _load_extra
    from postbridge.models.domain import ContentItemOrm
    from postbridge.services.video_library import MaterialUpdate, update_material, get_material
    session, tenant, other, material, _ = state
    project = create_project(session, tenant, request(material))
    video_tasks.process_video_job.run(project["current"]["job_id"])
    session.expire_all()
    result = get_project(session, tenant, project["id"])
    assert result["current"]["cover_url"].endswith(".jpg")
    assert result["current"]["cover_title"] == "Demo"
    post = prepare_post_draft(session, tenant, project["id"], PrepareVideoPost(expected_version=1, caption="Approved text"))
    extra = _load_extra(session.get(ContentItemOrm, post["content_item_id"]).body_structured_json)
    assert extra["cover_image_url"] == result["current"]["cover_url"]
    assert extra["video_origin"]["version"] == 1
    original = get_material(session, tenant, material["media_asset_id"])
    update_material(session, tenant, material["media_asset_id"], MaterialUpdate(name="Renamed", folder="New", expected_name=original["name"], expected_folder=""))
    with error("VIDEO_MATERIAL_CONFLICT"):
        update_material(session, tenant, material["media_asset_id"], MaterialUpdate(name="Other", expected_name=original["name"]))
    session.rollback()
    with error("VIDEO_ASSET_NOT_FOUND"):
        get_material(session, other, material["media_asset_id"])


def test_cancellation_after_cover_upload_cleans_unattached_media(state, monkeypatch):
    session, tenant, _, material, tmp = state
    project = create_project(session, tenant, request(material))
    upload = video_tasks.upload_media_file
    jpeg_uploads = []
    def cancel_after_cover(key, path, content_type):
        upload(key, path, content_type)
        if key.endswith(".jpg"):
            jpeg_uploads.append(key)
            if len(jpeg_uploads) == 2:
                cancel_project(session, tenant, project["id"], 1)
    monkeypatch.setattr(video_tasks, "upload_media_file", cancel_after_cover)
    video_tasks.process_video_job.run(project["current"]["job_id"])
    session.expire_all()
    assert get_project(session, tenant, project["id"])["current"]["status"] == "cancelled"
    assert session.scalar(select(func.count()).select_from(MediaAssetOrm)) == 1
    assert not list((tmp / "storage").rglob("*.jpg"))
    assert not list((tmp / "storage").rglob("*.mp4"))
