"""Durable text-publication evidence in the existing per-target checkpoint table.

No response/transport flag alone permits a resend. The checkpoint is committed
before entering a publisher; a lost acknowledgement requires reconciliation.
Approved video publications keep their existing independent state machine.
"""
from datetime import UTC, datetime
import json

from sqlalchemy import select

from postbridge.domain.errors import ValidationError
from postbridge.models.domain import PublicationTargetOrm, VideoDeliveryStateOrm

TEXT_DELIVERY_KIND = "text_publication_v1"
SAFE_OUTCOMES = {"not_sent", "rejected"}
UNKNOWN_CODE = "PUBLICATION_DELIVERY_UNKNOWN"
UNKNOWN_MESSAGE = "Delivery acknowledgement is unknown; reconcile the destination before resending"


def text_delivery_state(session, target_id):
    row = session.scalar(select(VideoDeliveryStateOrm).where(VideoDeliveryStateOrm.target_id == target_id)
                         .execution_options(populate_existing=True))
    if row is None:
        return {}
    try:
        # Tagged text receipts contain no credentials, payload or provider URLs.
        # The existing video writer continues to own encrypted video checkpoints.
        state = json.loads(row.encrypted_state)
        if isinstance(state, dict) and state.get("kind") == TEXT_DELIVERY_KIND:
            return state
    except (ValueError, TypeError):
        pass
    # A legacy, unreadable or different receipt is never proof of non-delivery.
    return {"outcome": "unknown"}


def write_text_delivery(session, target_id, outcome, *, attempt_id, error_code=None):
    row = session.get(VideoDeliveryStateOrm, target_id)
    if row is None:
        row = VideoDeliveryStateOrm(target_id=target_id)
        session.add(row)
    state = {"kind": TEXT_DELIVERY_KIND, "attempt_id": attempt_id, "outcome": outcome}
    if error_code:
        state["error_code"] = error_code
    row.encrypted_state = json.dumps(state, sort_keys=True)
    row.updated_at = datetime.now(UTC)
    session.flush()


def delivery_view(session, target):
    if target.status == "unknown":
        outcome = "unknown"
    elif target.status == "published" and target.external_post_id:
        outcome = "sent"
    else:
        state = text_delivery_state(session, target.id)
        outcome = state.get("outcome")
        if outcome and outcome not in SAFE_OUTCOMES:
            outcome = "unknown"
        if not outcome:
            outcome = "not_started" if target.status == "pending" and not target.retry_count else "unknown"
    return {"delivery_outcome": outcome,
            "reconciliation_required": outcome == "unknown",
            "delivery_retry_safe": outcome in SAFE_OUTCOMES}


def retry_is_proven_safe(session, target):
    return text_delivery_state(session, target.id).get("outcome") in SAFE_OUTCOMES


def begin_text_dispatch(session, target_id, attempt_id):
    target = session.scalar(select(PublicationTargetOrm).where(PublicationTargetOrm.id == target_id)
                            .with_for_update().execution_options(populate_existing=True))
    state = text_delivery_state(session, target_id)
    if (target is None or target.status != "publishing" or state.get("attempt_id") != attempt_id
            or state.get("outcome") != "not_sent"):
        session.rollback()
        raise ValidationError(code="VALIDATION_PUBLICATION_CLAIM_LOST",
                              message="Publication claim changed before dispatch", details={"target_id": target_id})
    write_text_delivery(session, target_id, "dispatching", attempt_id=attempt_id)
    session.commit()


def text_failure_outcome(error):
    # These explicit adapter assertions must describe the final publish boundary,
    # not a transport retry hint or an intermediate media-upload response.
    outcome = error.details.get("publication_outcome")
    if outcome in SAFE_OUTCOMES:
        return outcome
    return "unknown"


def mark_text_failure(session, target_id, attempt_id, error, *, outcome, correlation_id=None):
    from postbridge.observability.failure_class import classify_publication_failure
    from postbridge.observability.metrics import inc_publication_failure
    from postbridge.storage.publication_status_event_outbox import enqueue_publication_target_status_changed
    session.rollback()
    target = session.scalar(select(PublicationTargetOrm).where(PublicationTargetOrm.id == target_id)
                            .with_for_update().execution_options(populate_existing=True))
    if target is None or text_delivery_state(session, target_id).get("attempt_id") != attempt_id:
        session.rollback()
        return
    write_text_delivery(session, target_id, outcome, attempt_id=attempt_id, error_code=error.code)
    inc_publication_failure(classify_publication_failure(error))
    target.status = "failed" if outcome in SAFE_OUTCOMES else "unknown"
    target.error_code = error.code if outcome in SAFE_OUTCOMES else UNKNOWN_CODE
    target.error_message = (error.message or "")[:2048] if outcome in SAFE_OUTCOMES else UNKNOWN_MESSAGE
    target.updated_at = datetime.now(UTC)
    enqueue_publication_target_status_changed(session, target, correlation_id=correlation_id)
    session.commit()


def prepare_manual_dispatch(session, target, *, correlation_id=None):
    """Retry only durable, proven refusals; keep approved video dispatch unchanged."""
    from postbridge.services.video_publication import approval_for_plan
    if approval_for_plan(session, target.publication_plan_id):
        return
    from postbridge.storage.publication_status_event_outbox import enqueue_publication_target_status_changed
    target = session.scalar(select(PublicationTargetOrm).where(PublicationTargetOrm.id == target.id)
                            .with_for_update().execution_options(populate_existing=True))
    if target is None:
        session.rollback()
        raise ValidationError(code="VALIDATION_PUBLICATION_TARGET_NOT_FOUND", message="Publication target not found")
    state = text_delivery_state(session, target.id)
    first_dispatch = target.status == "pending" and not target.retry_count and not state
    if target.status == "published" and target.external_post_id:
        session.rollback()
        return
    if not first_dispatch and not (target.status in {"failed", "pending"} and retry_is_proven_safe(session, target)):
        session.rollback()
        raise ValidationError(code="VALIDATION_PUBLICATION_RECONCILIATION_REQUIRED", message=UNKNOWN_MESSAGE,
                              details={"target_id": target.id, "delivery_outcome": "unknown", "reconciliation_required": True})
    if target.status == "failed":
        target.status, target.error_code, target.error_message = "pending", None, None
        target.retry_count += 1
        target.updated_at = datetime.now(UTC)
        enqueue_publication_target_status_changed(session, target, correlation_id=correlation_id)
    session.commit()
