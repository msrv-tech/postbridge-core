"""Internal API: постановка publication_target в очередь Celery."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from postbridge.api.internal_auth import check_sync_publish_auth
from postbridge.db import get_db_session
from postbridge.domain.errors import ValidationError
from postbridge.models.domain import PublicationTargetOrm
from postbridge.workers.tasks import process_publication_target_task

router = APIRouter()


@router.post("/internal/publication-targets/{target_id}/dispatch", include_in_schema=False)
def dispatch_publication_target(
    target_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Ставит задачу публикации в очередь (исполнение в worker)."""
    check_sync_publish_auth(request)
    row = session.get(PublicationTargetOrm, target_id)
    if row is None:
        raise ValidationError(
            code="VALIDATION_PUBLICATION_TARGET_NOT_FOUND",
            message="publication target not found",
            message_key="error.validation.publication_target_not_found",
            details={"target_id": target_id},
        )
    process_publication_target_task.delay(target_id, None)
    return {"status": "enqueued", "target_id": target_id}
