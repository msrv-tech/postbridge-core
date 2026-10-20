"""The approved plan itself is the delivery outbox; broker failures do not lose it."""
from sqlalchemy import select, or_
from postbridge.config import get_settings
from postbridge.db import SESSION_LOCAL
from postbridge.models.domain import PublicationTargetOrm, VideoPublicationApprovalOrm, PublicationPlanOrm
from postbridge.services import video_delivery_time
from postbridge.workers.celery_app import celery_app


@celery_app.task(name="postbridge.video.dispatch_publications")
def dispatch_video_publications():
    with SESSION_LOCAL() as session:
        video_delivery_time.expire_due_approvals(session)
    if not get_settings().video_publication_enabled:
        return 0
    with SESSION_LOCAL() as session:
        ids = session.scalars(select(PublicationTargetOrm.id).join(VideoPublicationApprovalOrm,
            VideoPublicationApprovalOrm.publication_plan_id == PublicationTargetOrm.publication_plan_id)
            .join(PublicationPlanOrm, PublicationPlanOrm.id == PublicationTargetOrm.publication_plan_id).where(
                PublicationTargetOrm.status.in_(["pending", "processing"]), or_(PublicationPlanOrm.publish_at.is_(None), PublicationPlanOrm.publish_at <= video_delivery_time.now_utc()))
            .order_by(PublicationTargetOrm.created_at).limit(50)).all()
    sent = 0
    for target_id in ids:
        celery_app.send_task("postbridge.publication.process_target", args=[target_id, "video-publication"])
        sent += 1
    return sent
