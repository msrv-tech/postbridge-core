from datetime import UTC, datetime, timedelta
from pathlib import Path
import runpy

import pytest
from pydantic import ValidationError as PayloadError
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from postbridge.models.domain import ContentItemOrm, PublicationPlanOrm, PublicationTargetOrm, VideoPublicationApprovalOrm
from postbridge.services import video_delivery_time as timing, video_publication as publication_service
from postbridge.services.publication_target_executor import PublicationTargetExecutor
from postbridge.services.video_publication import ApproveVideoPublication, approve_publication, publication_view
from postbridge.workers import video_publication_tasks
from tests.test_video_studio import state, error
from tests.test_video_post_drafts import ready
from tests.test_video_publication import publication, Publisher, approval


@pytest.fixture
def clock(monkeypatch):
    values = {"now": datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)}
    monkeypatch.setattr(timing, "now_utc", lambda: values["now"])
    monkeypatch.setattr(publication_service, "now_utc", lambda: values["now"])
    return values


def scheduled(publication, clock):
    db, tenant, _, post_id, channels = publication
    due = clock["now"] + timedelta(hours=2)
    view = publication_view(db, tenant, post_id)
    body = ApproveVideoPublication(expected_snapshot=view["snapshot_hash"], channel_ids=[ch.id for ch in channels],
        publish_at=due, timezone="Asia/Novosibirsk")
    return approve_publication(db, tenant, post_id, body), body, due


def test_scheduled_delivery_cannot_start_early_even_with_manual_worker_dispatch(publication, clock, monkeypatch):
    db, tenant, _, post_id, _ = publication
    view, body, due = scheduled(publication, clock)
    assert approve_publication(db, tenant, post_id, body)["approval_id"] == view["approval_id"]
    plan = db.scalar(select(PublicationPlanOrm))
    assert timing.utc(plan.publish_at) == due and plan.timezone == "Asia/Novosibirsk"
    assert all(timing.utc(row.scheduled_at) == due for row in db.scalars(select(PublicationTargetOrm)))
    monkeypatch.setattr(video_publication_tasks, "SESSION_LOCAL", sessionmaker(db.get_bind()))
    queued = []
    monkeypatch.setattr(video_publication_tasks.celery_app, "send_task", lambda name, **kwargs: queued.append(kwargs))
    assert video_publication_tasks.dispatch_video_publications.run() == 0
    publisher = Publisher()
    for target in view["targets"]:
        assert PublicationTargetExecutor(db, publisher).run(target["id"]) == 0
    assert publisher.calls == [] and queued == []
    clock["now"] = due + timedelta(seconds=1)
    assert video_publication_tasks.dispatch_video_publications.run() == 2
    for target in view["targets"]:
        assert PublicationTargetExecutor(db, publisher).run(target["id"]) == 1
    assert len(publisher.calls) == 2
    assert db.get(ContentItemOrm, post_id).status == "published"


def test_missed_window_expires_without_sending_and_can_be_rescheduled_once(publication, clock, monkeypatch):
    db, tenant, _, post_id, _ = publication
    view, original, due = scheduled(publication, clock)
    clock["now"] = due + timing.DELIVERY_GRACE
    monkeypatch.setattr(video_publication_tasks, "SESSION_LOCAL", sessionmaker(db.get_bind()))
    monkeypatch.setenv("VIDEO_PUBLICATION_ENABLED", "0")
    assert video_publication_tasks.dispatch_video_publications.run() == 0
    db.expire_all()
    assert {item["status"] for item in publication_view(db, tenant, post_id)["targets"]} == {"expired"}
    monkeypatch.setenv("VIDEO_PUBLICATION_ENABLED", "1")
    publisher = Publisher()
    for target in view["targets"]:
        assert PublicationTargetExecutor(db, publisher).run(target["id"]) == 0
    new_time = clock["now"] + timedelta(hours=1)
    body = timing.RescheduleVideoPublication(expected_schedule_version=1, publish_at=new_time, timezone="UTC")
    revised = timing.reschedule_publication(db, tenant, post_id, body)
    assert revised["delivery"]["version"] == 2 and len(revised["delivery"]["history"]) == 2
    assert {item["status"] for item in revised["targets"]} == {"pending"}
    assert timing.reschedule_publication(db, tenant, post_id, body)["delivery"]["version"] == 2
    # An acknowledgement lost before rescheduling still replays its original approval.
    assert approve_publication(db, tenant, post_id, original)["delivery"]["version"] == 2
    with error("VIDEO_SCHEDULE_CONFLICT"):
        timing.reschedule_publication(db, tenant, post_id, body.model_copy(update={"publish_at": new_time + timedelta(hours=1)}))
    assert db.scalar(select(func.count()).select_from(PublicationPlanOrm)) == 1
    assert db.scalar(select(func.count()).select_from(VideoPublicationApprovalOrm)) == 1
    assert publisher.calls == []


def test_partial_success_is_preserved_when_only_unsent_destinations_move(publication, clock):
    db, tenant, _, post_id, _ = publication
    view, _ = approval(publication)
    first, second = [item["id"] for item in view["targets"]]
    publisher = Publisher()
    PublicationTargetExecutor(db, publisher).run(first)
    due = clock["now"] + timedelta(hours=1)
    moved = timing.reschedule_publication(db, tenant, post_id,
        timing.RescheduleVideoPublication(expected_schedule_version=1, publish_at=due, timezone="UTC"))
    assert {item["id"]: item["status"] for item in moved["targets"]} == {first: "published", second: "pending"}
    clock["now"] = due
    assert PublicationTargetExecutor(db, publisher).run(first) == 0
    assert PublicationTargetExecutor(db, publisher).run(second) == 1
    assert len(publisher.calls) == 2


def test_unknown_or_claimed_delivery_cannot_be_reset_to_pending(publication, clock):
    db, tenant, _, post_id, _ = publication
    view, _ = approval(publication)
    target = db.get(PublicationTargetOrm, view["targets"][0]["id"])
    for status in ("unknown", "publishing"):
        target.status = status
        db.commit()
        with error("VIDEO_RESCHEDULE_UNAVAILABLE"):
            timing.reschedule_publication(db, tenant, post_id, timing.RescheduleVideoPublication(expected_schedule_version=1))
        assert target.status == status


def test_request_started_before_deadline_can_finish_after_it(publication, clock):
    db, tenant, _, post_id, _ = publication
    view, _, due = scheduled(publication, clock)
    clock["now"] = due
    class SlowPublisher(Publisher):
        def publish_post(self, *args, **kwargs):
            clock["now"] = due + timedelta(hours=1)
            return super().publish_post(*args, **kwargs)
    publisher = SlowPublisher()
    assert PublicationTargetExecutor(db, publisher).run(view["targets"][0]["id"]) == 1
    assert PublicationTargetExecutor(db, publisher).run(view["targets"][1]["id"]) == 0
    assert {item["status"] for item in publication_view(db, tenant, post_id)["targets"]} == {"published", "expired"}
    assert len(publisher.calls) == 1


@pytest.mark.parametrize("body", [
    {"publish_at": "2026-10-20T09:00:00", "timezone": "UTC"},
    {"publish_at": "2026-10-20T09:00:00Z"},
    {"timezone": "UTC"},
    {"timezone": ""},
    {"publish_at": "2026-10-20T09:00:00Z", "timezone": "Unknown/Zone"},
    {"publish_at": "2026-10-20T09:00:01Z", "timezone": "UTC"},
])
def test_scheduled_payload_requires_an_unambiguous_instant_and_valid_timezone(body):
    with pytest.raises(PayloadError):
        timing.RescheduleVideoPublication(expected_schedule_version=1, **body)


def test_new_approval_cannot_be_backdated_or_exceed_the_horizon(publication, clock):
    db, tenant, _, post_id, channels = publication
    view = publication_view(db, tenant, post_id)
    for at in [clock["now"], clock["now"] + timedelta(days=366)]:
        with error("VIDEO_DELIVERY_TIME_INVALID"):
            approve_publication(db, tenant, post_id, ApproveVideoPublication(expected_snapshot=view["snapshot_hash"],
                channel_ids=[channels[0].id], publish_at=at, timezone="UTC"))
    assert db.scalar(select(func.count()).select_from(PublicationPlanOrm)) == 0


def test_timing_migration_preserves_legacy_approvals_and_create_all_baseline(publication, clock):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    db, *_ = publication
    approval(publication)
    db.close()
    migration = runpy.run_path(str(Path(__file__).resolve().parents[1] / "alembic/versions/20261019_video_delivery_time.py"))
    with db.get_bind().begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
            from alembic import op
            for name in ["deadline_at", "schedule_history", "schedule_version", "initial_timing"]:
                op.drop_column("video_publication_approvals", name)
            migration["upgrade"]()
            migration["upgrade"]()
    row = db.scalar(select(VideoPublicationApprovalOrm))
    assert row.schedule_version == 1 and row.deadline_at is None and row.schedule_history == []


def test_downgrade_cannot_leave_pending_deliveries_without_their_time_guard(publication, clock):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    db, *_ = publication
    scheduled(publication, clock)
    db.close()
    migration = runpy.run_path(str(Path(__file__).resolve().parents[1] / "alembic/versions/20261019_video_delivery_time.py"))
    with db.get_bind().begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="Cancel pending"):
                migration["downgrade"]()
        assert "deadline_at" in {column["name"] for column in inspect(connection).get_columns("video_publication_approvals")}
