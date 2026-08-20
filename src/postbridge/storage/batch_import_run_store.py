from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from postbridge.db import (
    BatchImportFetchedPostOrm,
    BatchImportEnqueuedPostOrm,
    BatchImportRunOrm,
    PublishedPostOrm,
    StatusEventOutboxOrm,
)
from postbridge.models.domain import ChannelOrm, PublicationTargetOrm
from postbridge.domain.errors import PostbridgeError, ValidationError
from postbridge.domain.models import BatchImportRun, BatchImportRunStatus, PostPayload
from postbridge.integrations.status_event_client import CONTRACT_VERSION
from postbridge.observability.metrics import inc_status_events_outbox_enqueued


class BatchImportRunStore:
    """Хранилище batch import runs, fetched posts, status event outbox."""

    def __init__(self, session: Session):
        self.session = session

    def create_run(
        self,
        tenant_id: str,
        source_channel: str,
        target_channel: str,
        requested_limit: int,
        correlation_id: str,
        target_core_channel_id: str,
        idempotency_key: str | None = None,
        source_platform: str | None = None,
        target_platform: str | None = None,
        source_core_channel_id: str | None = None,
    ) -> tuple[BatchImportRun, bool]:
        """Создаёт run. Возвращает (run, True) при создании, (run, False) при dedup."""
        if idempotency_key:
            existing = self.session.scalar(
                select(BatchImportRunOrm).where(
                    BatchImportRunOrm.tenant_id == tenant_id,
                    BatchImportRunOrm.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return self._to_domain(existing), False

        if source_core_channel_id is None:
            raise ValidationError(
                code="VALIDATION_MIGRATION_REQUIRES_SOURCE_CORE_CHANNEL",
                message="migration run requires source_core_channel_id (Core channels.id UUID)",
                details={},
            )
        ch_src = self.session.get(ChannelOrm, source_core_channel_id)
        if ch_src is None or ch_src.tenant_id != tenant_id:
            raise ValidationError(
                code="VALIDATION_CHANNEL_NOT_FOUND",
                message="source_core_channel_id missing or wrong tenant",
                details={"source_core_channel_id": source_core_channel_id},
            )
        ch_tgt = self.session.get(ChannelOrm, target_core_channel_id)
        if ch_tgt is None or ch_tgt.tenant_id != tenant_id:
            raise ValidationError(
                code="VALIDATION_CHANNEL_NOT_FOUND",
                message="target_core_channel_id missing or wrong tenant",
                details={"target_core_channel_id": target_core_channel_id},
            )

        now = datetime.now(UTC)
        run = BatchImportRunOrm(
            id=str(uuid4()),
            tenant_id=tenant_id,
            source_channel=source_channel,
            target_channel=target_channel,
            status=BatchImportRunStatus.PENDING.value,
            requested_limit=requested_limit,
            processed_posts=0,
            retry_count=0,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            source_platform=source_platform,
            target_platform=target_platform,
            source_core_channel_id=source_core_channel_id,
            target_core_channel_id=target_core_channel_id,
            batch_import_dispatch_enqueued_at=None,
            created_at=now,
            updated_at=now,
        )
        self.session.add(run)
        self.session.flush()
        self._enqueue_status_event_for_run(run, correlation_id=correlation_id, occurred_at=now)
        self.session.commit()
        self.session.refresh(run)
        return self._to_domain(run), True

    def get_run(self, run_id: str, *, tenant_id: str | None = None) -> BatchImportRun | None:
        run = self.session.get(BatchImportRunOrm, run_id)
        if run is None:
            return None
        if tenant_id is not None and run.tenant_id != tenant_id:
            return None
        return self._to_domain(run)

    def list_runs(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[BatchImportRun]:
        stmt = (
            select(BatchImportRunOrm)
            .where(BatchImportRunOrm.tenant_id == tenant_id)
            .order_by(BatchImportRunOrm.created_at.desc())
        )
        if status:
            stmt = stmt.where(BatchImportRunOrm.status == status)
        stmt = stmt.limit(limit).offset(offset)
        runs = list(self.session.scalars(stmt).all())
        return [self._to_domain(r) for r in runs]

    def acquire_for_processing(self, run_id: str) -> BatchImportRun | None:
        now = datetime.now(UTC)
        result = self.session.execute(
            update(BatchImportRunOrm)
            .where(
                BatchImportRunOrm.id == run_id,
                BatchImportRunOrm.status == BatchImportRunStatus.PENDING.value,
            )
            .values(
                status=BatchImportRunStatus.RUNNING.value,
                started_at=now,
                updated_at=now,
            )
        )
        if result.rowcount == 0:
            existing = self.session.get(BatchImportRunOrm, run_id)
            if existing is None:
                raise ValidationError(
                    code="VALIDATION_MIGRATION_RUN_NOT_FOUND",
                    message="migration run not found",
                    details={"run_id": run_id},
                )
            return None
        run = self.session.get(BatchImportRunOrm, run_id)
        if run is None:
            raise ValidationError(
                code="VALIDATION_MIGRATION_RUN_NOT_FOUND",
                message="migration run not found",
                details={"run_id": run_id},
            )
        self._enqueue_status_event_for_run(run, correlation_id=run.correlation_id, occurred_at=now)
        self.session.commit()
        return self._to_domain(run)

    def mark_completed(self, run_id: str, processed_posts: int) -> None:
        run = self._get_or_raise(run_id)
        now = datetime.now(UTC)
        run.status = BatchImportRunStatus.COMPLETED.value
        run.processed_posts = processed_posts
        run.error_code = None
        run.error_message = None
        run.error_source = None
        run.error_retryable = None
        run.error_details_json = None
        run.completed_at = now
        run.updated_at = now
        self._enqueue_status_event_for_run(run, correlation_id=run.correlation_id, occurred_at=now)
        self.session.commit()

    def update_run_progress(self, run_id: str, processed_posts: int) -> None:
        """Обновляет счётчик без смены статуса и без outbox (промежуточный прогресс)."""
        run = self._get_or_raise(run_id)
        run.processed_posts = processed_posts
        run.updated_at = datetime.now(UTC)
        self.session.commit()

    def mark_dispatch_phase_complete(self, run_id: str) -> None:
        run = self._get_or_raise(run_id)
        now = datetime.now(UTC)
        run.batch_import_dispatch_enqueued_at = now
        run.updated_at = now
        self.session.commit()

    def insert_enqueued_publication(
        self,
        *,
        batch_import_run_id: str,
        source_post_id: str,
        publication_target_id: str,
    ) -> None:
        now = datetime.now(UTC)
        self.session.add(
            BatchImportEnqueuedPostOrm(
                batch_import_run_id=batch_import_run_id,
                source_post_id=source_post_id,
                publication_target_id=publication_target_id,
                created_at=now,
            )
        )
        self.session.commit()

    def insert_enqueued_skip(
        self,
        *,
        batch_import_run_id: str,
        source_post_id: str,
    ) -> None:
        """Пост пропущен из-за глобального dedup (claim_publish=false); target не создаётся."""
        now = datetime.now(UTC)
        self.session.add(
            BatchImportEnqueuedPostOrm(
                batch_import_run_id=batch_import_run_id,
                source_post_id=source_post_id,
                publication_target_id=None,
                created_at=now,
            )
        )
        self.session.commit()

    def list_enqueued_target_ids(self, batch_import_run_id: str) -> list[str]:
        rows = list(
            self.session.scalars(
                select(BatchImportEnqueuedPostOrm.publication_target_id).where(
                    BatchImportEnqueuedPostOrm.batch_import_run_id == batch_import_run_id,
                    BatchImportEnqueuedPostOrm.publication_target_id.is_not(None),
                )
            ).all()
        )
        return [tid for tid in rows if tid is not None]

    def delete_enqueued_posts_for_run(self, batch_import_run_id: str) -> None:
        self.session.execute(
            delete(BatchImportEnqueuedPostOrm).where(
                BatchImportEnqueuedPostOrm.batch_import_run_id == batch_import_run_id
            )
        )
        self.session.commit()

    def count_fetched_posts(self, run_id: str) -> int:
        count = self.session.scalar(
            select(func.count())
            .select_from(BatchImportFetchedPostOrm)
            .where(BatchImportFetchedPostOrm.batch_import_run_id == run_id)
        )
        return int(count or 0)

    def claim_publish(
        self,
        source_channel: str,
        source_post_id: str,
        target_channel: str,
    ) -> bool:
        now = datetime.now(UTC)
        row = PublishedPostOrm(
            source_channel=source_channel,
            source_post_id=source_post_id,
            target_channel=target_channel,
            published_at=now,
        )
        self.session.add(row)
        try:
            self.session.flush()
            return True
        except IntegrityError:
            self.session.rollback()
            return False

    def release_claim(
        self,
        source_channel: str,
        source_post_id: str,
        target_channel: str,
    ) -> None:
        self.session.execute(
            delete(PublishedPostOrm).where(
                PublishedPostOrm.source_channel == source_channel,
                PublishedPostOrm.source_post_id == source_post_id,
                PublishedPostOrm.target_channel == target_channel,
            )
        )
        self.session.flush()

    def update_max_message_id(
        self,
        source_channel: str,
        source_post_id: str,
        target_channel: str,
        max_message_id: str,
    ) -> None:
        self.session.execute(
            update(PublishedPostOrm)
            .where(
                PublishedPostOrm.source_channel == source_channel,
                PublishedPostOrm.source_post_id == source_post_id,
                PublishedPostOrm.target_channel == target_channel,
            )
            .values(max_message_id=max_message_id)
        )
        self.session.flush()

    def get_published_post(
        self,
        source_channel: str,
        source_post_id: str,
        target_channel: str,
    ) -> PublishedPostOrm | None:
        return self.session.scalar(
            select(PublishedPostOrm).where(
                PublishedPostOrm.source_channel == source_channel,
                PublishedPostOrm.source_post_id == source_post_id,
                PublishedPostOrm.target_channel == target_channel,
            )
        )

    def has_fetched_posts(self, run_id: str) -> bool:
        count = self.session.scalar(
            select(func.count())
            .select_from(BatchImportFetchedPostOrm)
            .where(BatchImportFetchedPostOrm.batch_import_run_id == run_id)
        )
        return (count or 0) > 0

    def store_fetched_posts(self, run_id: str, posts: list[PostPayload]) -> None:
        now = datetime.now(UTC)
        self.session.execute(
            delete(BatchImportFetchedPostOrm).where(
                BatchImportFetchedPostOrm.batch_import_run_id == run_id
            )
        )
        for idx, post in enumerate(posts):
            row = BatchImportFetchedPostOrm(
                batch_import_run_id=run_id,
                source_post_id=post.source_post_id,
                text=post.text,
                media_url=post.media_url,
                sort_order=idx,
                fetched_at=now,
            )
            self.session.add(row)
        self.session.commit()

    def list_posts_for_publish(self, run_id: str) -> list[PostPayload]:
        fetched = list(
            self.session.scalars(
                select(BatchImportFetchedPostOrm)
                .where(BatchImportFetchedPostOrm.batch_import_run_id == run_id)
                .order_by(BatchImportFetchedPostOrm.sort_order)
            ).all()
        )
        if not fetched:
            return []
        post_ids = [f.source_post_id for f in fetched]
        handled_ids = {
            row[0]
            for row in self.session.execute(
                select(BatchImportEnqueuedPostOrm.source_post_id).where(
                    BatchImportEnqueuedPostOrm.batch_import_run_id == run_id,
                    BatchImportEnqueuedPostOrm.source_post_id.in_(post_ids),
                )
            ).all()
        }
        return [
            PostPayload(
                source_post_id=f.source_post_id,
                text=f.text,
                media_url=f.media_url,
            )
            for f in fetched
            if f.source_post_id not in handled_ids
        ]

    def count_successful_deliveries(self, run_id: str) -> int:
        """Число постов run, учтённых как успешно «закрытые» без target (dedup-skip)."""
        count = self.session.scalar(
            select(func.count())
            .select_from(BatchImportEnqueuedPostOrm)
            .where(
                BatchImportEnqueuedPostOrm.batch_import_run_id == run_id,
                BatchImportEnqueuedPostOrm.publication_target_id.is_(None),
            )
        )
        return int(count or 0)

    def mark_failed(self, run_id: str, error: PostbridgeError, correlation_id: str) -> None:
        run = self._get_or_raise(run_id)
        now = datetime.now(UTC)
        run.status = BatchImportRunStatus.FAILED.value
        run.correlation_id = correlation_id
        run.error_code = error.code
        run.error_message = error.message
        run.error_source = error.source
        run.error_retryable = error.retryable
        run.error_details_json = json.dumps(error.details, ensure_ascii=True)
        run.completed_at = now
        run.updated_at = now
        self._enqueue_status_event_for_run(run, correlation_id=correlation_id, occurred_at=now)
        self.session.commit()

    def release_retryable_claims_for_run(self, run_id: str) -> None:
        """Снимает dedup-claim для постов run, которые не были успешно опубликованы."""
        run = self._get_or_raise(run_id)
        enqueued_rows = list(
            self.session.scalars(
                select(BatchImportEnqueuedPostOrm).where(
                    BatchImportEnqueuedPostOrm.batch_import_run_id == run_id
                )
            ).all()
        )
        published_source_ids: set[str] = set()
        skipped_source_ids: set[str] = set()
        for row in enqueued_rows:
            if row.publication_target_id is None:
                skipped_source_ids.add(row.source_post_id)
                continue
            target = self.session.get(PublicationTargetOrm, row.publication_target_id)
            if target is not None and target.status == "published":
                published_source_ids.add(row.source_post_id)

        fetched = list(
            self.session.scalars(
                select(BatchImportFetchedPostOrm).where(
                    BatchImportFetchedPostOrm.batch_import_run_id == run_id
                )
            ).all()
        )
        for fetched_post in fetched:
            source_post_id = fetched_post.source_post_id
            if source_post_id in published_source_ids or source_post_id in skipped_source_ids:
                continue
            if self.get_published_post(
                run.source_channel, source_post_id, run.target_channel
            ) is not None:
                self.release_claim(run.source_channel, source_post_id, run.target_channel)
        self.session.commit()

    def retry_manual(self, run_id: str, correlation_id: str) -> bool:
        run = self._get_or_raise(run_id)
        if run.status != BatchImportRunStatus.FAILED.value:
            return False
        if run.target_core_channel_id and run.batch_import_dispatch_enqueued_at is not None:
            return False
        now = datetime.now(UTC)
        if run.target_core_channel_id:
            self.release_retryable_claims_for_run(run_id)
            self.delete_enqueued_posts_for_run(run_id)
            run.batch_import_dispatch_enqueued_at = None
        run.status = BatchImportRunStatus.PENDING.value
        run.correlation_id = correlation_id
        run.retry_count = 0
        run.error_message = None
        run.error_code = None
        run.error_source = None
        run.error_retryable = None
        run.error_details_json = None
        run.started_at = None
        run.completed_at = None
        run.updated_at = now
        self._enqueue_status_event_for_run(run, correlation_id=correlation_id, occurred_at=now)
        self.session.commit()
        return True

    def schedule_retry(self, job_id: str, correlation_id: str, max_retries: int) -> bool:
        run = self._get_or_raise(job_id)
        if run.status != BatchImportRunStatus.FAILED.value:
            return False
        if not run.error_retryable:
            return False
        if run.retry_count >= max_retries:
            return False

        now = datetime.now(UTC)
        run.retry_count += 1
        run.status = BatchImportRunStatus.PENDING.value
        run.correlation_id = correlation_id
        run.error_message = None
        run.error_code = None
        run.error_source = None
        run.error_retryable = None
        run.error_details_json = None
        run.started_at = None
        run.completed_at = None
        run.updated_at = now
        self._enqueue_status_event_for_run(run, correlation_id=correlation_id, occurred_at=now)
        self.session.commit()
        return True

    def recover_stuck_running_runs(
        self,
        *,
        timeout_seconds: int,
        correlation_id: str,
    ) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=timeout_seconds)
        stuck = list(
            self.session.scalars(
                select(BatchImportRunOrm).where(
                    BatchImportRunOrm.status == BatchImportRunStatus.RUNNING.value,
                    BatchImportRunOrm.started_at.is_not(None),
                    BatchImportRunOrm.started_at <= cutoff,
                    or_(
                        BatchImportRunOrm.target_core_channel_id.is_(None),
                        BatchImportRunOrm.batch_import_dispatch_enqueued_at.is_(None),
                    ),
                )
            ).all()
        )
        if not stuck:
            return 0
        now = datetime.now(UTC)
        for run in stuck:
            run.status = BatchImportRunStatus.FAILED.value
            run.correlation_id = correlation_id
            run.error_code = "INTERNAL_JOB_STUCK_TIMEOUT"
            run.error_message = "job exceeded running timeout and was auto-recovered"
            run.error_source = "core"
            run.error_retryable = True
            run.error_details_json = json.dumps(
                {"started_at": run.started_at.isoformat() if run.started_at else None},
                ensure_ascii=True,
            )
            run.completed_at = now
            run.updated_at = now
            self._enqueue_status_event_for_run(run, correlation_id=correlation_id, occurred_at=now)
        self.session.commit()
        return len(stuck)

    def list_due_status_events_outbox(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[StatusEventOutboxOrm]:
        return list(
            self.session.scalars(
                select(StatusEventOutboxOrm)
                .where(
                    StatusEventOutboxOrm.status.in_(("pending", "failed")),
                    StatusEventOutboxOrm.next_attempt_at <= now,
                )
                .order_by(StatusEventOutboxOrm.id.asc())
                .limit(limit)
            ).all()
        )

    def mark_status_event_outbox_sent(self, outbox_id: int) -> None:
        row = self.session.get(StatusEventOutboxOrm, outbox_id)
        if row is None:
            return
        now = datetime.now(UTC)
        row.status = "sent"
        row.sent_at = now
        row.updated_at = now
        self.session.commit()

    def mark_status_event_outbox_failed(
        self,
        *,
        outbox_id: int,
        last_error: str,
        next_attempt_at: datetime,
        exhausted: bool,
    ) -> None:
        row = self.session.get(StatusEventOutboxOrm, outbox_id)
        if row is None:
            return
        row.attempt_count += 1
        row.last_error = last_error[:1024]
        row.next_attempt_at = next_attempt_at
        row.status = "failed" if not exhausted else "exhausted"
        row.updated_at = datetime.now(UTC)
        self.session.commit()

    def _get_or_raise(self, run_id: str) -> BatchImportRunOrm:
        run = self.session.get(BatchImportRunOrm, run_id)
        if run is None:
            raise ValidationError(
                code="VALIDATION_MIGRATION_RUN_NOT_FOUND",
                message="migration run not found",
                details={"run_id": run_id},
            )
        return run

    def _enqueue_status_event_for_run(
        self,
        run: BatchImportRunOrm,
        *,
        correlation_id: str | None,
        occurred_at: datetime,
    ) -> None:
        error_payload: dict[str, Any] | None = None
        if (
            run.error_code is not None
            and run.error_message is not None
            and run.error_source is not None
            and run.error_retryable is not None
        ):
            details = {}
            if run.error_details_json:
                details = json.loads(run.error_details_json)
            error_payload = {
                "code": run.error_code,
                "message": run.error_message,
                "details": details,
                "source": run.error_source,
                "retryable": run.error_retryable,
                "correlation_id": correlation_id or run.correlation_id or "unknown",
            }

        payload = {
            "event_id": str(uuid4()),
            "contract_version": CONTRACT_VERSION,
            "event_type": "batch_import_run.status.changed",
            "occurred_at": occurred_at.isoformat(),
            "batch_import_run": {
                "id": run.id,
                "status": run.status,
                "processed_posts": run.processed_posts,
                "retry_count": run.retry_count,
                "correlation_id": correlation_id or run.correlation_id or "unknown",
                "error": error_payload,
            },
        }
        self.session.add(
            StatusEventOutboxOrm(
                event_id=payload["event_id"],
                batch_import_run_id=run.id,
                correlation_id=correlation_id or run.correlation_id or "unknown",
                contract_version=CONTRACT_VERSION,
                event_type="batch_import_run.status.changed",
                payload_json=json.dumps(payload, ensure_ascii=True),
                status="pending",
                attempt_count=0,
                next_attempt_at=occurred_at,
                last_error=None,
                sent_at=None,
                created_at=occurred_at,
                updated_at=occurred_at,
            )
        )
        inc_status_events_outbox_enqueued()

    @staticmethod
    def _to_domain(run: BatchImportRunOrm) -> BatchImportRun:
        error_details: dict[str, Any] | None = None
        if run.error_details_json:
            error_details = json.loads(run.error_details_json)
        return BatchImportRun(
            id=run.id,
            tenant_id=run.tenant_id,
            source_channel=run.source_channel,
            target_channel=run.target_channel,
            status=BatchImportRunStatus(run.status),
            requested_limit=run.requested_limit,
            processed_posts=run.processed_posts,
            idempotency_key=run.idempotency_key,
            created_at=run.created_at,
            updated_at=run.updated_at,
            correlation_id=run.correlation_id,
            error_code=run.error_code,
            error_message=run.error_message,
            error_details=error_details,
            error_source=run.error_source,
            error_retryable=run.error_retryable,
            retry_count=run.retry_count,
            started_at=run.started_at,
            completed_at=run.completed_at,
            source_platform=run.source_platform,
            target_platform=run.target_platform,
            source_core_channel_id=run.source_core_channel_id,
            target_core_channel_id=run.target_core_channel_id,
        )
