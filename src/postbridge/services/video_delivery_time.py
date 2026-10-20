"""Versioned video delivery windows, independent of production and broker timing."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError
from sqlalchemy import select, update

from postbridge.models.domain import ContentItemOrm, PublicationPlanOrm, PublicationTargetOrm, VideoPostDraftOrm, VideoPublicationApprovalOrm
from postbridge.services.video_media import video_error
from postbridge.domain.errors import PostbridgeError
from postbridge.storage.publication_status_event_outbox import enqueue_publication_target_status_changed

DELIVERY_GRACE = timedelta(minutes=15)
MAX_SCHEDULE_HORIZON = timedelta(days=365)


class DeliveryTiming(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    publish_at: AwareDatetime | None = None
    timezone: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_zone(self):
        if bool(self.publish_at) != bool(self.timezone) or self.timezone == "":
            raise PydanticCustomError("video_delivery_time_invalid", "Scheduled delivery needs an instant and an IANA timezone together")
        if self.timezone:
            try:
                ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise PydanticCustomError("video_delivery_time_invalid", "Unknown IANA timezone") from exc
            self.publish_at = self.publish_at.astimezone(UTC)
            if self.publish_at.second or self.publish_at.microsecond:
                raise PydanticCustomError("video_delivery_time_invalid", "Delivery times must use whole minutes")
        return self


class RescheduleVideoPublication(DeliveryTiming):
    expected_schedule_version: int = Field(ge=1)


def now_utc():
    return datetime.now(UTC)


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def timing_value(payload):
    return {"publish_at": payload.publish_at.isoformat() if payload.publish_at else None, "timezone": payload.timezone}


def validate_future(payload, now):
    if payload.publish_at and not now + timedelta(minutes=1) <= payload.publish_at <= now + MAX_SCHEDULE_HORIZON:
        raise video_error("VIDEO_DELIVERY_TIME_INVALID", "Choose a delivery time at least one minute ahead and within a year")


def delivery_view(session, approval):
    if not approval:
        return None
    plan = session.get(PublicationPlanOrm, approval.publication_plan_id, populate_existing=True)
    return {"version": approval.schedule_version, "publish_at": utc(plan.publish_at).isoformat() if plan.publish_at else None,
        "timezone": plan.timezone, "deadline_at": utc(approval.deadline_at).isoformat() if approval.deadline_at else None,
        "history": approval.schedule_history or []}


def _lock_approval(session, approval):
    receipt = session.get(VideoPostDraftOrm, approval.post_draft_id)
    session.scalar(select(ContentItemOrm).where(ContentItemOrm.id == receipt.content_item_id)
        .with_for_update().execution_options(populate_existing=True))
    session.refresh(approval)
    return approval


def assert_window(approval, now):
    history = approval.schedule_history or []
    timing = history[-1] if history else {"publish_at": None}
    due = datetime.fromisoformat(timing["publish_at"]) if timing.get("publish_at") else None
    if due and utc(due) > now:
        raise video_error("VIDEO_DELIVERY_NOT_DUE", "The approved delivery time has not arrived")
    if approval.deadline_at and utc(approval.deadline_at) <= now:
        raise video_error("VIDEO_DELIVERY_MISSED", "The delivery window was missed; confirm a new time")


def expire_pending(session, approval):
    ids = session.scalars(update(PublicationTargetOrm).where(
        PublicationTargetOrm.publication_plan_id == approval.publication_plan_id,
        PublicationTargetOrm.status == "pending").values(status="expired", error_code="VIDEO_DELIVERY_MISSED",
        error_message="Delivery did not start within its approved window", updated_at=now_utc())
        .returning(PublicationTargetOrm.id)).all()
    for target_id in ids:
        enqueue_publication_target_status_changed(session, session.get(PublicationTargetOrm, target_id), correlation_id="video-delivery-missed")
    return len(ids)


def admit_target(session, approval):
    """Serialize claims with rescheduling/cancellation on the canonical content row."""
    _lock_approval(session, approval)
    try:
        assert_window(approval, now_utc())
    except PostbridgeError as exc:
        if getattr(exc, "code", None) == "VIDEO_DELIVERY_MISSED":
            expire_pending(session, approval)
            session.commit()
            return False
        if getattr(exc, "code", None) == "VIDEO_DELIVERY_NOT_DUE":
            session.rollback()
            return False
        raise
    return True


def expire_due_approvals(session, *, limit=50):
    now = now_utc()
    candidates = session.scalars(select(VideoPublicationApprovalOrm).where(
        VideoPublicationApprovalOrm.deadline_at <= now,
        VideoPublicationApprovalOrm.publication_plan_id.in_(select(PublicationTargetOrm.publication_plan_id).where(
            PublicationTargetOrm.status == "pending"))).order_by(VideoPublicationApprovalOrm.deadline_at).limit(limit)).all()
    expired = 0
    for approval in candidates:
        _lock_approval(session, approval)
        if approval.deadline_at and utc(approval.deadline_at) <= now:
            expired += expire_pending(session, approval)
        session.commit()
    return expired


def reschedule_publication(session, tenant_id, content_id, payload):
    from postbridge.config import get_settings
    from postbridge.services.video_publication import _post, approval_for_content, approved_payload, fingerprint, publication_view
    post, _ = _post(session, tenant_id, content_id, lock=True)
    approval = approval_for_content(session, post.id)
    if not approval:
        raise video_error("VIDEO_APPROVAL_REQUIRED", "Confirm the video before changing its delivery time")
    session.refresh(approval)
    timing = timing_value(payload)
    request_hash = fingerprint({**timing, "expected_schedule_version": payload.expected_schedule_version})
    if payload.expected_schedule_version != approval.schedule_version:
        history = approval.schedule_history or []
        if payload.expected_schedule_version == approval.schedule_version - 1 and history and history[-1].get("request_hash") == request_hash:
            return publication_view(session, tenant_id, content_id)
        raise video_error("VIDEO_SCHEDULE_CONFLICT", "The delivery schedule changed; refresh before rescheduling")
    if not get_settings().video_publication_enabled:
        raise video_error("VIDEO_PUBLICATION_DISABLED", "Video publication is paused")
    targets = session.scalars(select(PublicationTargetOrm).where(
        PublicationTargetOrm.publication_plan_id == approval.publication_plan_id).execution_options(populate_existing=True)).all()
    if any(target.status in {"publishing", "processing", "unknown"} for target in targets):
        raise video_error("VIDEO_RESCHEDULE_UNAVAILABLE", "Verify in-progress or unknown deliveries before rescheduling")
    eligible = [target for target in targets if target.status in {"pending", "expired", "cancelled", "failed"}]
    if not eligible:
        raise video_error("VIDEO_RESCHEDULE_UNAVAILABLE", "No unsent destinations remain")
    now = now_utc()
    validate_future(payload, now)
    for target in eligible:
        approved_payload(session, target, post, None, check_time=False)
    initial = approval.initial_timing or {"publish_at": None, "timezone": None}
    history = approval.schedule_history or [{**initial, "version": 1, "created_at": utc(approval.created_at).isoformat()}]
    if len(history) >= 100:
        raise video_error("VIDEO_SCHEDULE_LIMIT", "This post has reached its schedule revision limit")
    approval.schedule_version += 1
    approval.deadline_at = (payload.publish_at or now) + DELIVERY_GRACE
    approval.schedule_history = [*history, {**timing, "version": approval.schedule_version,
        "created_at": now.isoformat(), "request_hash": request_hash}]
    plan = session.get(PublicationPlanOrm, approval.publication_plan_id)
    plan.publish_at, plan.timezone = payload.publish_at, payload.timezone
    for target in eligible:
        target.status, target.error_code, target.error_message = "pending", None, None
        target.scheduled_at, target.updated_at = payload.publish_at, now
        enqueue_publication_target_status_changed(session, target, correlation_id="video-rescheduled")
    session.commit()
    return publication_view(session, tenant_id, content_id)
