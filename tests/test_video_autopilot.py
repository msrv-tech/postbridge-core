import base64
from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from postbridge.models.domain import MediaAssetOrm, VideoProductionJobOrm
from postbridge.services.video_studio import VideoCreate, create_project, get_project, cancel_project, project_by_request
from postbridge.services import video_autopilot
from postbridge.workers import video_tasks
from tests.test_video_studio import state, PNG


@pytest.fixture
def auto(state, monkeypatch):
    for key, value in {"VIDEO_AI_ENABLED": "1", "POSTBRIDGE_APP_MODE": "selfhost",
        "VIDEO_AI_SELFHOST_BUDGET_UNITS": "1000", "AI_GATEWAY_BASE_URL": "https://gateway.example.test",
        "AI_GATEWAY_DEFAULT_MODEL": "storyboard-model", "AI_IMAGE_GENERATION_MODEL": "image-model"}.items():
        monkeypatch.setenv(key, value)
    return state


def request(scenes=None):
    return VideoCreate(brief="A product demo", request_key=str(uuid4()), mode="autopilot", duration_seconds=10,
                       scenes=scenes or [], language="en")


def mock_gateway(monkeypatch, *, assets=None, fail_image=False, no_usage=False):
    calls = []
    def gateway(path, body, correlation_id):
        calls.append((path, body, correlation_id))
        if ":analysis:" in correlation_id:
            return {"usage": {"total_tokens": 7}, "choices": [{"message": {"content": json.dumps({
                "description": "A light screenshot", "visible_text": "", "suggested_start_seconds": 0,
                "selection_reason": "Use the visible source"})}}]}
        if path.endswith("completions"):
            scenes = [{"asset_id": asset, "duration_seconds": 5, "caption": f"Scene {index + 1}",
                       "image_prompt": "A calm product illustration" if not asset else ""}
                      for index, asset in enumerate(assets or [None, None])]
            return {"usage": {"total_tokens": 10}, "choices": [{"message": {"content": json.dumps({"scenes": scenes})}}]}
        if fail_image:
            raise httpx.ReadTimeout("paid response lost")
        return {"usage": {} if no_usage else {"total_tokens": 20},
                "data": [{"b64_json": base64.b64encode(PNG).decode()}]}
    monkeypatch.setattr(video_autopilot, "gateway_json", gateway)
    return calls


def test_autopilot_without_uploads_creates_images_and_real_mp4(auto, monkeypatch):
    session, tenant, _, _, _ = auto
    calls = mock_gateway(monkeypatch)
    body = request()
    project = create_project(session, tenant, body)
    assert project["current"]["status"] == "queued"
    video_tasks.process_video_job.run(project["current"]["job_id"])
    session.expire_all()
    result = get_project(session, tenant, project["id"])
    assert result["current"]["status"] == "succeeded"
    assert result["current"]["usage"]["tokens_charged"] == 50
    assert not result["current"]["usage"]["uncertain"]
    assert len(result["current"]["scenes"]) == 2
    assert all(s["url"].endswith(".png") for s in result["current"]["scenes"])
    assert len(calls) == 3
    video_tasks.process_video_job.run(project["current"]["job_id"])
    assert len(calls) == 3
    assert project_by_request(session, tenant, body.request_key)["requested_revision"]["usage"]["tokens_charged"] == 50


def test_uploaded_materials_are_reused_without_paid_image_generation(auto, monkeypatch):
    session, tenant, _, material, _ = auto
    calls = mock_gateway(monkeypatch, assets=[material["media_asset_id"], material["media_asset_id"]])
    project = create_project(session, tenant, request([{"asset_id": material["media_asset_id"], "duration_seconds": 5}]))
    video_tasks.process_video_job.run(project["current"]["job_id"])
    session.expire_all()
    result = get_project(session, tenant, project["id"])
    assert result["current"]["status"] == "succeeded"
    assert len(calls) == 2
    analysis_call = calls[0][1]
    assert any(part.get("type") == "image_url" and part["image_url"]["url"].startswith("data:image/jpeg;base64,") for part in analysis_call["messages"][1]["content"])
    assert {s["asset_id"] for s in result["current"]["scenes"]} == {material["media_asset_id"]}


def test_lost_paid_response_is_not_retried_and_preserves_known_usage(auto, monkeypatch):
    session, tenant, _, _, _ = auto
    calls = mock_gateway(monkeypatch, fail_image=True)
    project = create_project(session, tenant, request())
    video_tasks.process_video_job.run(project["current"]["job_id"])
    session.expire_all()
    result = get_project(session, tenant, project["id"])["current"]
    assert result["status"] == "failed" and result["error_code"] == "VIDEO_AI_RECONCILIATION_REQUIRED"
    assert result["usage"]["tokens_charged"] == 10 and result["usage"]["uncertain"]
    video_tasks.process_video_job.run(project["current"]["job_id"])
    assert len(calls) == 2


def test_missing_gateway_usage_never_becomes_a_guessed_bill(auto, monkeypatch):
    session, tenant, _, _, _ = auto
    mock_gateway(monkeypatch, no_usage=True)
    project = create_project(session, tenant, request())
    video_tasks.process_video_job.run(project["current"]["job_id"])
    session.expire_all()
    usage = get_project(session, tenant, project["id"])["current"]["usage"]
    assert usage["tokens_charged"] == 10 and usage["uncertain"]


def test_storage_failure_is_detected_before_any_paid_call(auto, monkeypatch):
    session, tenant, _, _, _ = auto
    calls = mock_gateway(monkeypatch)
    def unavailable(*args):
        raise OSError("storage unavailable")
    monkeypatch.setattr(video_tasks, "upload_media_file", unavailable)
    project = create_project(session, tenant, request())
    video_tasks.process_video_job.run(project["current"]["job_id"])
    session.expire_all()
    current = get_project(session, tenant, project["id"])["current"]
    assert current["status"] == "failed" and not calls
    assert current["usage"]["tokens_charged"] == 0 and not current["usage"]["uncertain"]


def test_hosted_worker_requires_fresh_budget_grant_before_each_paid_call(auto, monkeypatch):
    session, tenant, _, _, _ = auto
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "saas")
    monkeypatch.setenv("SAAS_BASE_URL", "https://saas.example.test")
    monkeypatch.setenv("STATUS_EVENT_WEBHOOK_TOKEN", "test-event-token")
    calls = mock_gateway(monkeypatch)
    grants = []
    def post(client, url, *, headers, json):
        grants.append((url, headers, json))
        return httpx.Response(200 if len(grants) == 1 else 409, json={"authorized": len(grants) == 1})
    monkeypatch.setattr(httpx.Client, "post", post)
    payload = request().model_copy(update={"billing_reservation_id": str(uuid4()), "billing_budget_units": 1000})
    project = create_project(session, tenant, payload)
    video_tasks.process_video_job.run(project["current"]["job_id"])
    session.expire_all()
    current = get_project(session, tenant, project["id"])["current"]
    assert len(calls) == 1 and len(grants) == 2
    assert grants[1][2]["spent_units"] == 10
    assert grants[0][2]["reservation_id"] == payload.billing_reservation_id
    assert grants[0][0] == "https://saas.example.test/internal/video/authorize"
    assert current["error_code"] == "VIDEO_BUDGET_NOT_AUTHORIZED"
    assert current["usage"]["tokens_charged"] == 10 and not current["usage"]["uncertain"]


def test_budget_stops_before_the_next_paid_call(auto, monkeypatch):
    session, tenant, _, _, _ = auto
    monkeypatch.setenv("VIDEO_AI_SELFHOST_BUDGET_UNITS", "5")
    calls = mock_gateway(monkeypatch)
    project = create_project(session, tenant, request())
    video_tasks.process_video_job.run(project["current"]["job_id"])
    session.expire_all()
    current = get_project(session, tenant, project["id"])["current"]
    assert current["error_code"] == "VIDEO_BUDGET_EXHAUSTED"
    assert len(calls) == 1
    assert current["usage"]["tokens_charged"] == 10 and not current["usage"]["uncertain"]


def test_storyboard_cannot_reference_a_material_not_supplied(auto, monkeypatch):
    session, tenant, _, _, _ = auto
    calls = mock_gateway(monkeypatch, assets=[str(uuid4()), str(uuid4())])
    project = create_project(session, tenant, request())
    video_tasks.process_video_job.run(project["current"]["job_id"])
    session.expire_all()
    current = get_project(session, tenant, project["id"])["current"]
    assert current["error_code"] == "VIDEO_AI_INVALID_STORYBOARD" and len(calls) == 1
    assert current["usage"]["tokens_charged"] == 10 and not current["usage"]["uncertain"]


def test_cancel_while_gateway_responds_still_records_actual_usage(auto, monkeypatch):
    session, tenant, _, _, _ = auto
    project = create_project(session, tenant, request())
    original = mock_gateway(monkeypatch)
    generate = video_autopilot.gateway_json
    def cancel_after_response(*args):
        body = generate(*args)
        cancel_project(session, tenant, project["id"], 1)
        return body
    monkeypatch.setattr(video_autopilot, "gateway_json", cancel_after_response)
    video_tasks.process_video_job.run(project["current"]["job_id"])
    session.expire_all()
    current = get_project(session, tenant, project["id"])["current"]
    assert current["status"] == "cancelled" and len(original) == 1
    assert current["usage"]["tokens_charged"] == 10 and not current["usage"]["uncertain"]


def test_recovery_reuses_paid_results_after_worker_dies_before_montage(auto, monkeypatch):
    session, tenant, _, _, _ = auto
    calls = mock_gateway(monkeypatch)
    project = create_project(session, tenant, request())
    render = video_tasks.render_montage
    def crashed(*args, **kwargs):
        raise KeyboardInterrupt("worker died")
    monkeypatch.setattr(video_tasks, "render_montage", crashed)
    with pytest.raises(KeyboardInterrupt):
        video_tasks.process_video_job.run(project["current"]["job_id"])
    session.expire_all()
    job = session.get(VideoProductionJobOrm, project["current"]["job_id"])
    job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    monkeypatch.setattr(video_tasks, "render_montage", render)
    video_tasks.process_video_job.run(job.id)
    session.expire_all()
    assert get_project(session, tenant, project["id"])["current"]["status"] == "succeeded"
    assert len(calls) == 3


def test_saved_visual_analysis_is_reused_for_another_storyboard(auto, monkeypatch):
    session, tenant, _, material, _ = auto
    aid = material["media_asset_id"]
    calls = mock_gateway(monkeypatch, assets=[aid, aid])
    first = create_project(session, tenant, request([{"asset_id": aid}]))
    video_tasks.process_video_job.run(first["current"]["job_id"])
    second = create_project(session, tenant, request([{"asset_id": aid}]))
    video_tasks.process_video_job.run(second["current"]["job_id"])
    session.expire_all()
    assert get_project(session, tenant, second["id"])["current"]["status"] == "succeeded"
    assert len(calls) == 3  # One vision analysis and two distinct scripts.
    second_context = json.loads(calls[-1][1]["messages"][1]["content"])
    assert second_context["materials"][0]["analysis"]["description"] == "A light screenshot"


def test_sampled_frame_anchor_moves_back_to_fit_whole_clip_fragment():
    from postbridge.services.video_autopilot import Storyboard, fit_clip_fragments
    aid = str(uuid4())
    plan = Storyboard(scenes=[{'asset_id': aid, 'duration_seconds': 4.5, 'source_start_seconds': 8.925,
        'image_prompt': None, 'caption': 'Postbridge'}])
    result = fit_clip_fragments(plan, {aid: {'kind': 'video', 'duration_seconds': 12}})
    assert result.scenes[0].source_start_seconds == 7.5
    assert result.scenes[0].duration_seconds == 4.5
    assert result.scenes[0].image_prompt is None
