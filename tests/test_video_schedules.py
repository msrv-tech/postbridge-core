from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from postbridge.agent.scheduling import next_run_at_from_cron
from postbridge.agent.storage import create_agent_task, pause_agent_task, resume_agent_task
from postbridge.models.domain import (AgentRunOrm, AgentTaskOrm, ChannelOrm, VideoAgentExecutionOrm,
    VideoProductionJobOrm, VideoProjectOrm)
from postbridge.services.video_schedules import enqueue_video_agent_run, synchronize_video_agent_runs
from postbridge.services.video_studio import get_project
from postbridge.workers import tasks, video_schedule_tasks, video_tasks
from tests.test_video_studio import state, error
from tests.test_video_autopilot import auto, mock_gateway


@pytest.fixture
def scheduled(state, monkeypatch):
    db, tenant, other, material, _ = state
    factory = video_tasks.SESSION_LOCAL
    monkeypatch.setattr(video_schedule_tasks, "SESSION_LOCAL", factory)
    monkeypatch.setattr(tasks, "SESSION_LOCAL", factory)
    channel = str(uuid4())
    db.add(ChannelOrm(id=channel, tenant_id=tenant, platform="postbridge", kind="source", title="Editorial", external_id="editorial", status="connected"))
    db.commit()
    return db, tenant, other, material, channel


def create(scheduled, *, auto=False, run_budget=None, monthly_budget=None, key=None):
    db, tenant, _, material, channel = scheduled
    key = key or str(uuid4())
    return create_agent_task(db, tenant_id=tenant, channel_id=channel, mode="video_autopilot", goal_text="Product demo",
        editorial_instructions=None, schedule_cron="0 9 * * 1", timezone="Asia/Novosibirsk", max_candidates_per_run=1,
        autonomy_mode="draft_approval", provider_config_id=None, model_name=None, content_item_id=None, created_by="owner",
        task_config={"request_key": key, "video": {"brief": "Product demo", "request_key": key, "mode": "autopilot" if auto else "manual",
            "duration_seconds": 10, "language": "en", "scenes": [] if auto else [
                {"asset_id": material["media_asset_id"], "duration_seconds": 1, "caption": "Original"}]},
            "max_units_per_run": run_budget, "max_units_per_month": monthly_budget})


def test_schedule_timezone_is_converted_to_utc_and_resumes_in_local_time(scheduled):
    assert next_run_at_from_cron("0 9 * * 1", base=datetime(2026, 10, 18, 20, tzinfo=UTC),
        timezone="Asia/Novosibirsk") == datetime(2026, 10, 19, 2, tzinfo=UTC)
    db, tenant, *_ = scheduled
    task = create(scheduled)
    db.commit()
    pause_agent_task(db, tenant_id=tenant, task_id=task.id)
    assert task.next_run_at is None
    resume_agent_task(db, tenant_id=tenant, task_id=task.id)
    assert task.next_run_at.hour == 2 and task.next_run_at.weekday() == 0


def test_schedule_creation_and_occurrence_replays_preserve_snapshot(scheduled):
    db, tenant, *_ = scheduled
    key = str(uuid4())
    task = create(scheduled, key=key)
    db.commit()
    assert create(scheduled, key=key).id == task.id
    run = enqueue_video_agent_run(db, tenant_id=tenant, task_id=task.id, occurrence_key="manual-replay-key")
    db.commit()
    assert enqueue_video_agent_run(db, tenant_id=tenant, task_id=task.id, occurrence_key="manual-replay-key").id == run.id
    task.goal_text = "New future task"
    db.commit()
    snapshot = db.get(VideoAgentExecutionOrm, run.id).input_snapshot
    assert snapshot["video"]["brief"] == "Product demo"
    assert snapshot["video"]["scenes"][0]["caption"] == "Original"
    assert db.scalar(select(func.count()).select_from(AgentRunOrm)) == 1


def test_existing_due_scheduler_enqueues_once_and_skips_overlap(scheduled):
    db, tenant, *_ = scheduled
    task = create(scheduled)
    task.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    assert tasks.process_due_agent_tasks_task.run() == 1
    assert tasks.process_due_agent_tasks_task.run() == 0
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(AgentRunOrm)) == 1
    task = db.get(AgentTaskOrm, task.id)
    task.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    tasks.process_due_agent_tasks_task.run()
    db.expire_all()
    skipped = db.scalar(select(AgentRunOrm).where(AgentRunOrm.status == "cancelled"))
    assert skipped.error_code == "VIDEO_SCHEDULE_OVERLAP"
    assert db.scalar(select(func.count()).select_from(VideoProjectOrm)) == 0


def test_manual_preparation_real_montage_and_agent_result_are_linked(scheduled):
    db, tenant, *_ = scheduled
    task = create(scheduled)
    run = enqueue_video_agent_run(db, tenant_id=tenant, task_id=task.id, occurrence_key=str(uuid4()))
    db.commit()
    # Pausing only affects future occurrences, not this admitted run.
    pause_agent_task(db, tenant_id=tenant, task_id=task.id)
    db.commit()
    video_schedule_tasks.prepare_video_agent_run.run(run.id)
    db.expire_all()
    execution = db.get(VideoAgentExecutionOrm, run.id)
    project = get_project(db, tenant, execution.project_id)
    assert project["origin"] == {"agent_run_id": run.id, "agent_task_id": task.id}
    video_schedule_tasks.prepare_video_agent_run.run(run.id)
    assert db.scalar(select(func.count()).select_from(VideoProductionJobOrm)) == 1
    video_tasks.process_video_job.run(project["current"]["job_id"])
    db.expire_all()
    synchronize_video_agent_runs(db)
    db.commit()
    db.refresh(run)
    assert run.status == "awaiting_review" and json.loads(run.token_usage_json) == {}
    assert json.loads(run.trace_json)["result"]["video_project_id"] == project["id"]
    assert db.get(AgentTaskOrm, task.id).next_run_at is None


def test_lost_preparation_response_reuses_the_same_project(scheduled, monkeypatch):
    db, tenant, *_ = scheduled
    task = create(scheduled)
    run = enqueue_video_agent_run(db, tenant_id=tenant, task_id=task.id, occurrence_key=str(uuid4()))
    db.commit()
    original = video_schedule_tasks.create_project
    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError("commit acknowledgement lost")
    monkeypatch.setattr(video_schedule_tasks, "create_project", lost)
    video_schedule_tasks.prepare_video_agent_run.run(run.id)
    db.expire_all()
    execution = db.get(VideoAgentExecutionOrm, run.id)
    assert execution.project_id is None
    execution.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    monkeypatch.setattr(video_schedule_tasks, "create_project", original)
    video_schedule_tasks.prepare_video_agent_run.run(run.id)
    db.expire_all()
    assert db.get(VideoAgentExecutionOrm, run.id).project_id
    assert db.scalar(select(func.count()).select_from(VideoProjectOrm)) == 1
    assert db.scalar(select(func.count()).select_from(VideoProductionJobOrm)) == 1


def test_video_schedule_rejects_cross_tenant_context(scheduled):
    db, _, other, _, channel = scheduled
    db.get(ChannelOrm, channel).tenant_id = other
    db.commit()
    with error("VIDEO_SCHEDULE_CONTEXT_INVALID"):
        create(scheduled)


def test_selfhost_schedule_monthly_budget_stops_before_new_generation(scheduled, auto, monkeypatch):
    db, tenant, *_ = scheduled
    calls = mock_gateway(monkeypatch)
    task = create(scheduled, auto=True, run_budget=60, monthly_budget=100)
    run = enqueue_video_agent_run(db, tenant_id=tenant, task_id=task.id, occurrence_key=str(uuid4()))
    db.commit()
    video_schedule_tasks.prepare_video_agent_run.run(run.id)
    db.expire_all()
    project = get_project(db, tenant, db.get(VideoAgentExecutionOrm, run.id).project_id)
    video_tasks.process_video_job.run(project["current"]["job_id"])
    db.expire_all()
    synchronize_video_agent_runs(db)
    db.commit()
    assert len(calls) == 3
    second = enqueue_video_agent_run(db, tenant_id=tenant, task_id=task.id, occurrence_key=str(uuid4()))
    db.commit()
    video_schedule_tasks.prepare_video_agent_run.run(second.id)
    db.refresh(second)
    assert second.status == "failed" and second.error_code == "VIDEO_SCHEDULE_MONTHLY_BUDGET"
    assert len(calls) == 3


def test_schedule_materials_are_protected_before_first_video_exists(scheduled):
    from postbridge.services.media_assets import delete_media_asset
    db, tenant, _, material, _ = scheduled
    create(scheduled)
    db.commit()
    with error("VALIDATION_MEDIA_ASSET_IN_USE"):
        delete_media_asset(db, tenant_id=tenant, media_asset_id=material["media_asset_id"])


def test_schedule_migration_preserves_existing_agent_tasks(scheduled):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from pathlib import Path
    from sqlalchemy import inspect
    import runpy
    db, *_ = scheduled
    task = create(scheduled)
    db.commit()
    migration = runpy.run_path(str(Path(__file__).resolve().parents[1] / "alembic/versions/20261019_video_agent_schedules.py"))
    with db.get_bind().begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
            migration["upgrade"]()
        assert "video_agent_executions" in inspect(connection).get_table_names()
    db.refresh(task)
    assert task.mode == "video_autopilot"


def test_schedule_migration_upgrades_the_legacy_mode_constraint(scheduled):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from pathlib import Path
    import runpy
    db, *_ = scheduled
    task = create(scheduled)
    task.mode = "topic_scout"
    db.commit()
    with db.get_bind().begin() as connection:
        VideoAgentExecutionOrm.__table__.drop(connection)
        with Operations.context(MigrationContext.configure(connection)):
            from alembic import op
            with op.batch_alter_table("agent_tasks") as batch:
                batch.drop_constraint("ck_agent_tasks_mode", type_="check")
                batch.create_check_constraint("ck_agent_tasks_mode", "mode IN ('post_copilot', 'topic_scout')")
            migration = runpy.run_path(str(Path(__file__).resolve().parents[1] / "alembic/versions/20261019_video_agent_schedules.py"))
            migration["upgrade"]()
    db.refresh(task)
    assert task.mode == "topic_scout"
    assert create(scheduled).mode == "video_autopilot"


def test_core_agent_api_exposes_template_run_and_origin(scheduled):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from postbridge.api.agent_internal import router
    from postbridge.db import get_db_session
    from postbridge.domain.errors import PostbridgeError
    db, tenant, _, _, _ = scheduled
    task = create(scheduled)
    db.commit()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_session] = lambda: db
    @app.exception_handler(PostbridgeError)
    async def error_response(request, exc):
        return JSONResponse({"code": exc.code}, status_code=400)
    client = TestClient(app)
    headers = {"Authorization": "Bearer svc-test-secret", "X-Tenant-Id": tenant, "Idempotency-Key": str(uuid4())}
    first = client.post(f"/internal/service/agent/tasks/{task.id}/run", headers=headers)
    assert first.status_code == 200, first.text
    run_id = first.json()["run"]["id"]
    assert first.json()["run"]["video_execution"]["input_snapshot"]["created_by"] == "owner"
    assert client.post(f"/internal/service/agent/tasks/{task.id}/run", headers=headers).json()["run"]["id"] == run_id
    video_schedule_tasks.prepare_video_agent_run.run(run_id)
    detail = client.get(f"/internal/service/agent/runs/{run_id}", headers=headers).json()
    assert detail["video_execution"]["project_id"]
    assert detail["video_execution"]["task_id"] == task.id
