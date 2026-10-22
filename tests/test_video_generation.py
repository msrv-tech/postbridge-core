"""Asynchronous video scenes reuse their gateway intent and retain cancelled costs."""
import base64
from datetime import UTC, datetime, timedelta
import subprocess
from uuid import uuid4

import httpx
import pytest

from postbridge.models.domain import VideoProductionJobOrm
from postbridge.services import video_generation
from postbridge.services.video_studio import VideoCreate, create_project, get_project, cancel_project
from postbridge.services.video_media import inspect_media
from postbridge.workers import video_tasks
from tests.test_video_studio import state, error
from tests.test_video_autopilot import auto


@pytest.fixture
def native(auto, monkeypatch):
    monkeypatch.setenv("VIDEO_GENERATION_ENABLED", "1")
    db, tenant, _, material, tmp = auto
    path = tmp / "native.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=1080x1920:r=24",
        "-t", "8", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-threads", "1", str(path)],
        check=True, capture_output=True, timeout=30)
    calls, operations = [], {}
    phase = {"ready": False, "lose_ack": False}
    def gateway(method, route, payload=None):
        calls.append((method, route, payload))
        if method == "POST":
            operations[payload["request_key"]] = True
            if phase["lose_ack"]:
                raise httpx.ReadTimeout("Accepted, reply lost")
            return {"id": "gateway-test-operation", "status": "queued", "usage_tokens_charged": 0}
        key = route.rsplit("/", 1)[-1]
        if key not in operations:
            request = httpx.Request("GET", "https://gateway.example.test" + route)
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("Not admitted", request=request, response=response)
        if not phase["ready"]:
            return {"id": "gateway-test-operation", "status": "generating", "usage_tokens_charged": 0}
        return {"id": "gateway-test-operation", "status": "ready", "usage_tokens_charged": 123,
            "video": {"b64_json": base64.b64encode(path.read_bytes()).decode()}}
    monkeypatch.setattr(video_generation, "gateway_video", gateway)
    return *auto, phase, calls


def make(native):
    db, tenant, _, material, *_ = native
    return create_project(db, tenant, VideoCreate(brief="A mixed demo", request_key=str(uuid4()), scenes=[
        {"duration_seconds": 2, "caption": "Generated intro", "generation_kind": "video", "image_prompt": "Calm blue abstract animation"},
        {"asset_id": material["media_asset_id"], "duration_seconds": 1, "caption": "Real product"}]))


def release_lease(db, job_id):
    db.expire_all()
    db.get(VideoProductionJobOrm, job_id).lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()


def test_native_scene_yields_and_resumes_without_new_paid_submission(native):
    db, tenant, _, material, tmp, phase, calls = native
    project = make(native)
    job_id = project["current"]["job_id"]
    video_tasks.process_video_job.run(job_id)
    db.expire_all()
    pending = get_project(db, tenant, project["id"])["current"]
    assert pending["status"] == "queued" and pending["stage"] == "generating_video"
    assert pending["usage"]["uncertain"]
    for _ in range(4):
        release_lease(db, job_id)
        video_tasks.process_video_job.run(job_id)
    db.expire_all()
    assert db.get(VideoProductionJobOrm, job_id).attempts == 0
    phase["ready"] = True
    release_lease(db, job_id)
    video_tasks.process_video_job.run(job_id)
    db.expire_all()
    current = get_project(db, tenant, project["id"])["current"]
    assert current["status"] == "succeeded", current
    assert current["usage"]["tokens_charged"] == 123 and not current["usage"]["uncertain"]
    assert current["scenes"][0]["kind"] == "video" and current["scenes"][1]["asset_id"] == material["media_asset_id"]
    assert len([c for c in calls if c[0] == "POST"]) == 1


def test_lost_gateway_ack_is_recovered_by_key_and_cancelled_cost_settles(native):
    db, tenant, *_, phase, calls = native
    phase["lose_ack"] = True
    project = make(native)
    job_id = project["current"]["job_id"]
    video_tasks.process_video_job.run(job_id)
    release_lease(db, job_id)
    video_tasks.process_video_job.run(job_id)
    db.expire_all()
    cancel_project(db, tenant, project["id"], 1)
    phase["ready"] = True
    release_lease(db, job_id)
    video_tasks.settle_video_scenes.run(job_id)
    db.expire_all()
    current = get_project(db, tenant, project["id"])["current"]
    assert current["status"] == "cancelled" and current["output_url"] is None
    assert current["usage"]["tokens_charged"] == 123 and not current["usage"]["uncertain"]
    assert len([c for c in calls if c[0] == "POST"]) == 1


def test_disabled_video_model_rejects_scene_before_production(auto):
    db, tenant, *_ = auto
    with error("VIDEO_GENERATION_UNAVAILABLE"):
        create_project(db, tenant, VideoCreate(brief="A scene", request_key=str(uuid4()),
            scenes=[{"generation_kind": "video", "image_prompt": "Abstract animation", "duration_seconds": 4}]))
