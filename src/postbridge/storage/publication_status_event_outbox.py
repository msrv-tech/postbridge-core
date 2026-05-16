"""Outbox событий publication.target.status.changed (SaaS контракт v1.4)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from postbridge.db import PublicationStatusEventOutboxOrm
from postbridge.models.domain import PublicationTargetOrm
from postbridge.observability.metrics import inc_publication_status_events_outbox_enqueued

PUBLICATION_STATUS_EVENT_TYPE = "publication.target.status.changed"
PUBLICATION_STATUS_CONTRACT_VERSION = "1.4"


def enqueue_publication_target_status_changed(
    session: Session,
    target: PublicationTargetOrm,
    *,
    correlation_id: str | None,
) -> None:
    """Добавляет запись outbox (без commit — вызывающий делает commit)."""
    now = datetime.now(UTC)
    cid = correlation_id or "unknown"
    err: dict[str, str] | None = None
    if target.error_code:
        err = {
            "code": target.error_code,
            "message": (target.error_message or "")[:1024],
            "correlation_id": cid,
        }
    payload = {
        "event_id": str(uuid4()),
        "contract_version": PUBLICATION_STATUS_CONTRACT_VERSION,
        "event_type": PUBLICATION_STATUS_EVENT_TYPE,
        "occurred_at": now.isoformat(),
        "tenant_id": target.tenant_id,
        "publication_target": {
            "id": target.id,
            "status": target.status,
            "channel_id": target.channel_id,
            "platform": target.platform,
            "error": err,
        },
    }
    session.add(
        PublicationStatusEventOutboxOrm(
            event_id=payload["event_id"],
            publication_target_id=target.id,
            tenant_id=target.tenant_id,
            correlation_id=cid,
            contract_version=PUBLICATION_STATUS_CONTRACT_VERSION,
            event_type=PUBLICATION_STATUS_EVENT_TYPE,
            payload_json=json.dumps(payload, ensure_ascii=True),
            status="pending",
            attempt_count=0,
            next_attempt_at=now,
            last_error=None,
            sent_at=None,
            created_at=now,
            updated_at=now,
        )
    )
    inc_publication_status_events_outbox_enqueued()


class PublicationStatusEventOutboxStore:
    """CRUD outbox публикаций для Celery dispatch."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_due(self, *, now: datetime, limit: int) -> list[PublicationStatusEventOutboxOrm]:
        return list(
            self.session.scalars(
                select(PublicationStatusEventOutboxOrm)
                .where(
                    PublicationStatusEventOutboxOrm.status.in_(("pending", "failed")),
                    PublicationStatusEventOutboxOrm.next_attempt_at <= now,
                )
                .order_by(PublicationStatusEventOutboxOrm.id.asc())
                .limit(limit)
            ).all()
        )

    def mark_sent(self, outbox_id: int) -> None:
        row = self.session.get(PublicationStatusEventOutboxOrm, outbox_id)
        if row is None:
            return
        now = datetime.now(UTC)
        row.status = "sent"
        row.sent_at = now
        row.updated_at = now
        self.session.commit()

    def mark_failed(
        self,
        *,
        outbox_id: int,
        last_error: str,
        next_attempt_at: datetime,
        exhausted: bool,
    ) -> None:
        row = self.session.get(PublicationStatusEventOutboxOrm, outbox_id)
        if row is None:
            return
        row.attempt_count += 1
        row.last_error = last_error[:1024]
        row.next_attempt_at = next_attempt_at
        row.status = "failed" if not exhausted else "exhausted"
        row.updated_at = datetime.now(UTC)
        self.session.commit()
