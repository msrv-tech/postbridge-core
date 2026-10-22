"""Batches preserve ownership, idempotency and stop before repeating paid work."""
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from postbridge.models.domain import VideoBatchOrm, VideoBatchItemOrm, VideoProductionJobOrm, VideoProjectOrm
from postbridge.services.video_batches import (ProjectBatchCreate, VoiceBatchCreate, create_project_batch,
    create_voice_batch, get_batch, cancel_batch)
from postbridge.services.video_studio import VideoCreate
from postbridge.services.media_assets import delete_media_asset
from postbridge.workers import video_tasks, video_batch_tasks
from tests.test_video_studio import state, error
from tests.test_video_autopilot import auto, mock_gateway
from tests.test_video_speech import speech, make
from tests.test_video_schedules import scheduled


@pytest.fixture(autouse=True)
def worker_factory(state, monkeypatch):
    monkeypatch.setattr(video_batch_tasks, "SESSION_LOCAL", video_tasks.SESSION_LOCAL)


def test_queued_batch_protects_materials_and_replays_once(state):
    db, tenant, other, material, _ = state
    payload = ProjectBatchCreate(request_key=str(uuid4()), video=VideoCreate(brief="Product", request_key=str(uuid4()),
        scenes=[{"asset_id": material["media_asset_id"], "duration_seconds": 1}]))
    batch = create_project_batch(db, tenant, payload)
    assert create_project_batch(db, tenant, payload)["id"] == batch["id"]
    assert db.scalar(select(func.count()).select_from(VideoBatchItemOrm)) == 1
    with error("VIDEO_BATCH_NOT_FOUND"):
        get_batch(db, other, batch["id"])
    with error("VALIDATION_MEDIA_ASSET_IN_USE"):
        delete_media_asset(db, tenant_id=tenant, media_asset_id=material["media_asset_id"])
    with error("VIDEO_REQUEST_CONFLICT"):
        create_project_batch(db, tenant, payload.model_copy(update={"max_units_total": 1}))
    db.rollback()
    cancel_batch(db, tenant, batch["id"])
    video_batch_tasks.advance_video_batch.run(batch["id"])
    assert db.scalar(select(func.count()).select_from(VideoProjectOrm)) == 0
    delete_media_asset(db, tenant_id=tenant, media_asset_id=material["media_asset_id"])


def test_voice_batch_reuses_visuals_and_original_after_each_variant(speech):
    db, tenant, *_ = speech
    original = make(speech)
    batch = create_voice_batch(db, tenant, original["id"], VoiceBatchCreate(request_key=str(uuid4()),
        expected_version=1, source_version=1, variants=[{"language": "en"}, {"language": "ru", "voice": "Puck"}]))
    for index in range(2):
        video_batch_tasks.advance_video_batch.run(batch["id"])
        db.expire_all()
        current = get_batch(db, tenant, batch["id"])
        video_batch_tasks.advance_video_batch.run(batch["id"])
        assert db.scalar(select(func.count()).select_from(VideoProductionJobOrm)) == index + 2
        video_tasks.process_video_job.run(current["items"][index]["job_id"])
    video_batch_tasks.advance_video_batch.run(batch["id"])
    db.expire_all()
    current = get_batch(db, tenant, batch["id"])
    assert current["status"] == "succeeded", current
    assert [i["version"] for i in current["items"]] == [2, 3]
    assert all(i["output_url"] for i in current["items"])
    assert all(i["payload"]["source_version"] == 1 for i in current["items"])


def test_unknown_child_cost_stops_remaining_paid_calls(auto, monkeypatch):
    import httpx
    db, tenant, *_ = auto
    calls = []
    def lost(*args):
        calls.append(args)
        raise httpx.ReadTimeout("provider reply lost")
    monkeypatch.setattr("postbridge.services.video_autopilot.gateway_json", lost)
    batch = create_project_batch(db, tenant, ProjectBatchCreate(request_key=str(uuid4()), count=2,
        video=VideoCreate(brief="Product", request_key=str(uuid4()), mode="autopilot")))
    video_batch_tasks.advance_video_batch.run(batch["id"])
    db.expire_all()
    current = get_batch(db, tenant, batch["id"])
    video_tasks.process_video_job.run(current["items"][0]["job_id"])
    video_batch_tasks.advance_video_batch.run(batch["id"])
    video_batch_tasks.advance_video_batch.run(batch["id"])
    db.expire_all()
    current = get_batch(db, tenant, batch["id"])
    assert current["status"] == "failed" and current["error_code"] == "VIDEO_RECONCILIATION_REQUIRED"
    assert current["items"][1]["status"] == "cancelled" and len(calls) == 1


def test_exhausted_batch_budget_does_not_admit_another_original(auto, monkeypatch):
    db, tenant, *_ = auto
    mock_gateway(monkeypatch)
    batch = create_project_batch(db, tenant, ProjectBatchCreate(request_key=str(uuid4()), count=2, max_units_total=1,
        video=VideoCreate(brief="Product", request_key=str(uuid4()), mode="autopilot")))
    video_batch_tasks.advance_video_batch.run(batch["id"])
    db.expire_all()
    item = get_batch(db, tenant, batch["id"])["items"][0]
    job = db.get(VideoProductionJobOrm, item["job_id"])
    job.status, job.progress_json = "succeeded", {"steps": {"plan": {"state": "done", "units": 1}}}
    db.commit()
    video_batch_tasks.advance_video_batch.run(batch["id"])
    db.expire_all()
    assert get_batch(db, tenant, batch["id"])["error_code"] == "VIDEO_BATCH_BUDGET_EXHAUSTED"
    assert db.scalar(select(func.count()).select_from(VideoProjectOrm)) == 1


def test_duplicate_voice_choices_and_empty_manual_batch_are_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        VoiceBatchCreate(request_key=str(uuid4()), expected_version=1, source_version=1,
            variants=[{"language": "en"}, {"language": "en"}])
    with pytest.raises(ValidationError):
        ProjectBatchCreate(request_key=str(uuid4()), video=VideoCreate(brief="Empty", request_key=str(uuid4())))


def test_scheduled_batch_keeps_occurrence_snapshot_and_awaits_all_children(scheduled, auto, monkeypatch):
    from tests.test_video_schedules import create
    from postbridge.models.domain import VideoAgentExecutionOrm
    from postbridge.services.video_schedules import enqueue_video_agent_run, synchronize_video_agent_runs
    from postbridge.workers import video_schedule_tasks
    import json
    db, tenant, *_ = scheduled
    task = create(scheduled, auto=True)
    config = json.loads(task.task_config_json)
    config.update(count=2)
    task.task_config_json = json.dumps(config)
    db.commit()
    run = enqueue_video_agent_run(db, tenant_id=tenant, task_id=task.id, occurrence_key="batch-test")
    db.commit()
    video_schedule_tasks.prepare_video_agent_run.run(run.id)
    db.expire_all()
    execution = db.get(VideoAgentExecutionOrm, run.id)
    batch_id = execution.batch_id
    assert batch_id and execution.input_snapshot["count"] == 2
    video_schedule_tasks.prepare_video_agent_run.run(run.id)
    assert db.scalar(select(func.count()).select_from(VideoBatchOrm)) == 1
    for index in range(2):
        video_batch_tasks.advance_video_batch.run(batch_id)
        db.expire_all()
        item = get_batch(db, tenant, batch_id)["items"][index]
        job = db.get(VideoProductionJobOrm, item["job_id"])
        job.status = "succeeded"
        db.commit()
        synchronize_video_agent_runs(db)
        db.commit()
        assert db.get(type(run), run.id).status == "running"
    video_batch_tasks.advance_video_batch.run(batch_id)
    db.expire_all()
    synchronize_video_agent_runs(db)
    db.commit()
    assert db.get(type(run), run.id).status == "awaiting_review"
