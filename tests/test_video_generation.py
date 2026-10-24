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


def test_generation_price_forwards_the_funded_gateway_quote_without_conversion(auto, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from postbridge.api.video_studio import build_router
    monkeypatch.setenv("VIDEO_GENERATION_ENABLED", "1")
    seen = []
    def gateway(method, path, payload=None, **kwargs):
        seen.append((method, path, payload, kwargs))
        return {"enabled": True, "models": [{"id": "other", "quotes": []},
            {"id": "veo-3.1-fast-generate-001", "resolution": "1080p",
             "billing_units_per_second": 9999999, "quotes": [{"duration_seconds": 8, "billing_units": 321071}]}]}
    monkeypatch.setattr(video_generation, "gateway_video", gateway)
    app = FastAPI()
    app.include_router(build_router("/test/videos", lambda: auto[1]))
    with TestClient(app) as client:
        response = client.get("/test/videos/generation-price")
    assert response.status_code == 200
    assert response.json() == {"source": "gitsell", "model": "veo-3.1-fast-generate-001",
        "resolution": "1080p", "duration_seconds": 8, "units_per_scene": 321071}
    assert seen == [("GET", "/v1/videos/models", None, {"timeout": 10})]


@pytest.mark.parametrize("units", [None, True, 0, -1, "321071", 321071.5, 100000001])
def test_generation_price_rejects_missing_or_invalid_quote(auto, monkeypatch, units):
    monkeypatch.setenv("VIDEO_GENERATION_ENABLED", "1")
    monkeypatch.setattr(video_generation, "gateway_video", lambda *a, **kw: {"enabled": True, "models": [
        {"id": "veo-3.1-fast-generate-001", "quotes": [{"duration_seconds": 8, "billing_units": units}]}]})
    with error("VIDEO_PRICING_UNAVAILABLE"):
        video_generation.generation_price()


def test_generation_price_has_no_legacy_multiplier_fallback(auto, monkeypatch):
    monkeypatch.setenv("VIDEO_GENERATION_ENABLED", "1")
    monkeypatch.setattr(video_generation, "gateway_video", lambda *a, **kw: {"enabled": True, "models": [
        {"id": "veo-3.1-fast-generate-001", "billing_units_per_second": 166666}]})
    with error("VIDEO_PRICING_UNAVAILABLE"):
        video_generation.generation_price()


def test_image_seed_is_compact_full_resolution_jpeg(native):
    db,tenant,_,material,tmp,phase,calls=native
    project=create_project(db,tenant,VideoCreate(brief='Animate this image',request_key=str(uuid4()),scenes=[{
        'asset_id':material['media_asset_id'],'duration_seconds':2,'caption':'Demo','regenerate_video':True,'image_prompt':'Subtle motion'}]))
    video_tasks.process_video_job.run(project['current']['job_id'])
    payload=next(c[2] for c in calls if c[0]=='POST')
    raw=base64.b64decode(payload['image']['b64_json'])
    assert payload['image']['mime_type']=='image/jpeg' and len(raw)<=650*1024
    frame=tmp/'submitted.jpg';frame.write_bytes(raw)
    meta=inspect_media(frame);assert (meta['width'],meta['height'])==(1080,1920)


def test_oversize_gateway_rejection_stops_without_paid_retry(native,monkeypatch):
    db,tenant,*_=native
    original=video_generation.gateway_video
    submitted=[]
    def gateway(method,route,payload=None):
        if method=='POST':
            submitted.append(payload)
            request=httpx.Request('POST','https://gateway.example.test'+route)
            response=httpx.Response(413,request=request)
            raise httpx.HTTPStatusError('Too large',request=request,response=response)
        return original(method,route,payload)
    monkeypatch.setattr(video_generation,'gateway_video',gateway)
    project=make(native);job=project['current']['job_id']
    video_tasks.process_video_job.run(job);release_lease(db,job);video_tasks.process_video_job.run(job);db.expire_all()
    current=get_project(db,tenant,project['id'])['current']
    assert current['status']=='failed' and current['error_code']=='VIDEO_GENERATION_NOT_AUTHORIZED'
    assert current['usage']['tokens_charged']==0 and not current['usage']['uncertain']
    assert len(submitted)==1


def test_failed_neighbour_keeps_paid_scene_available_for_a_new_revision(native,monkeypatch):
    from postbridge.services.video_studio import VideoRevise,VideoScene,revise_project
    db,tenant,_,material,tmp,phase,calls=native
    original=video_generation.gateway_video
    submits=[]
    def gateway(method,route,payload=None):
        if method=='POST':
            submits.append(payload)
            if len(submits)==2:
                request=httpx.Request('POST','https://gateway.example.test'+route)
                response=httpx.Response(422,request=request)
                raise httpx.HTTPStatusError('Rejected',request=request,response=response)
        return original(method,route,payload)
    monkeypatch.setattr(video_generation,'gateway_video',gateway)
    from postbridge.services.video_studio import store_video_material
    seed_material=store_video_material(db,tenant,tmp/'native.mp4')
    project=create_project(db,tenant,VideoCreate(brief='Recover a paid visual',request_key=str(uuid4()),scenes=[
        {'asset_id':seed_material['media_asset_id'],'source_start_seconds':1,'duration_seconds':2,'caption':'Keep','regenerate_video':True,'image_prompt':'Animate the image'},
        {'duration_seconds':2,'caption':'Replace','generation_kind':'video','image_prompt':'Abstract animation'}]))
    job=project['current']['job_id'];video_tasks.process_video_job.run(job)
    phase['ready']=True;release_lease(db,job);video_tasks.process_video_job.run(job);db.expire_all()
    failed=get_project(db,tenant,project['id'])
    assert failed['current']['status']=='failed' and failed['current']['usage']['tokens_charged']==123
    first,second=failed['current']['scenes']
    assert first['asset_id']!=material['media_asset_id'] and first['kind']=='video'
    assert not first.get('regenerate_video') and not first.get('source_start_seconds') and not second.get('asset_id')
    scenes=[{k:v for k,v in scene.items() if k in VideoScene.model_fields} for scene in [first,second]]
    revised=revise_project(db,tenant,project['id'],VideoRevise(brief=failed['brief'],request_key=str(uuid4()),expected_version=1,scenes=scenes))
    new_job=revised['current']['job_id'];video_tasks.process_video_job.run(new_job)
    release_lease(db,new_job);video_tasks.process_video_job.run(new_job);db.expire_all()
    result=get_project(db,tenant,project['id'])['current']
    assert result['status']=='succeeded' and result['usage']['tokens_charged']==123
    assert result['scenes'][0]['asset_id']==first['asset_id']
    assert len(submits)==3  # two successful scenes plus one definitive rejection, no paid duplicate.
