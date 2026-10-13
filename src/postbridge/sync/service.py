import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from postbridge.api.schemas import TelegramCredentials
from postbridge.domain.errors import InternalError, PostbridgeError, ValidationError
from postbridge.infrastructure.crypto.credentials import decrypt_credential_secret
from postbridge.integrations.base import SourceFetcher
from postbridge.integrations.registry import get_fetcher, resolve_fetch_credentials_for_core_channel
from postbridge.models.domain import InstallationSecretOrm
from postbridge.observability.logging import (
    log_job_completed,
    log_job_failed,
    log_job_processing_skipped,
)
from postbridge.observability.metrics import (
    inc_jobs_completed,
    inc_jobs_failed,
    observe_queue_lag_seconds,
    observe_job_duration_seconds,
)
from postbridge.storage.batch_import_run_store import BatchImportRunStore


class SyncService:
    """Сервис batch-миграции: fetch через registry; публикация только через publication_target (unified)."""

    DEFAULT_SOURCE_PLATFORM = "telegram"

    def __init__(
        self,
        session: Session,
        fetcher: SourceFetcher | None = None,
    ):
        """Инициализирует сервис с сессией БД. fetcher — для тестов, иначе из registry."""
        self.job_store = BatchImportRunStore(session)
        self._fetcher = fetcher

    def run_job(self, job_id: str, correlation_id: str | None = None) -> int:
        """Выполняет migration run. До reconcile возвращает 0 (посты уходят в publication_target)."""
        job = self.job_store.acquire_for_processing(job_id)
        if job is None:
            log_job_processing_skipped(job_id, correlation_id or "unknown", "duplicate")
            return 0
        if job.created_at.tzinfo is None:
            queue_lag_seconds = (datetime.now(UTC) - job.created_at.replace(tzinfo=UTC)).total_seconds()
        else:
            queue_lag_seconds = (datetime.now(UTC) - job.created_at).total_seconds()
        if queue_lag_seconds >= 0:
            observe_queue_lag_seconds(queue_lag_seconds)

        source_platform = getattr(job, "source_platform", None) or self.DEFAULT_SOURCE_PLATFORM

        source_creds = self._resolve_fetch_credentials(job, source_platform)

        fetcher = self._fetcher or get_fetcher(source_platform)

        try:
            if self.job_store.has_fetched_posts(job_id):
                posts_for_publish = self.job_store.list_posts_for_publish(job_id)
            else:
                posts = asyncio.run(
                    fetcher.fetch_posts(
                        source_channel=job.source_channel,
                        limit=job.requested_limit,
                        credentials=source_creds,
                        tenant_id=job.tenant_id,
                    )
                )
                self.job_store.store_fetched_posts(job_id, posts)
                posts_for_publish = self.job_store.list_posts_for_publish(job_id)

            if job.target_core_channel_id is None:
                raise ValidationError(
                    code="VALIDATION_MIGRATION_REQUIRES_TARGET_CORE_CHANNEL",
                    message="migration run requires target_core_channel_id (Core channels.id UUID)",
                    details={"job_id": job_id},
                )

            return self._run_unified_publication_targets(
                job_id=job_id,
                job=job,
                posts_for_publish=posts_for_publish,
                correlation_id=correlation_id,
            )
        except PostbridgeError as exc:
            failed_correlation_id = correlation_id or job.correlation_id or "unknown"
            self.job_store.mark_failed(job_id, exc, failed_correlation_id)
            inc_jobs_failed()
            log_job_failed(
                job_id, failed_correlation_id, exc.code, exc.retryable, job.retry_count
            )
            raise
        except Exception as exc:
            failed_correlation_id = correlation_id or job.correlation_id or "unknown"
            internal_error = InternalError(
                "Unexpected processing error",
                details={
                    "exception_type": type(exc).__name__,
                    "job_id": job_id,
                },
            )
            self.job_store.mark_failed(job_id, internal_error, failed_correlation_id)
            inc_jobs_failed()
            log_job_failed(
                job_id,
                failed_correlation_id,
                internal_error.code,
                internal_error.retryable,
                job.retry_count,
            )
            raise internal_error from exc

    def _resolve_fetch_credentials(self, job, source_platform: str):
        cid = job.source_core_channel_id
        if not cid:
            raise ValidationError(
                code="VALIDATION_MIGRATION_REQUIRES_SOURCE_CORE_CHANNEL",
                message="migration run requires source_core_channel_id (Core channels.id UUID)",
                details={"job_id": job.id},
            )
        try:
            return resolve_fetch_credentials_for_core_channel(
                self.job_store.session,
                tenant_id=job.tenant_id,
                source_core_channel_id=cid,
                source_platform=source_platform,
            )
        except ValidationError as exc:
            if source_platform == "telegram" and exc.code == "VALIDATION_CHANNEL_CREDENTIALS_MISSING":
                fallback = self._telegram_import_credentials_from_installation_secret(job.tenant_id)
                if fallback is not None:
                    return fallback
            raise

    def _telegram_import_credentials_from_installation_secret(self, tenant_id: str) -> TelegramCredentials | None:
        row = self.job_store.session.query(InstallationSecretOrm).filter_by(
            tenant_id=tenant_id,
            category="telegram_import",
        ).one_or_none()
        if row is None or not (row.encrypted_secret or "").strip():
            return None
        raw = decrypt_credential_secret(row.encrypted_secret)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        api_id = str(data.get("api_id") or "").strip()
        api_hash = str(data.get("api_hash") or "").strip()
        session_string = str(data.get("session_string") or "").strip() or None
        if not api_id or not api_hash:
            return None
        return TelegramCredentials(
            api_id=api_id,
            api_hash=api_hash,
            session_string=session_string,
        )

    def _run_unified_publication_targets(
        self,
        *,
        job_id: str,
        job,
        posts_for_publish: list,
        correlation_id: str | None,
    ) -> int:
        from postbridge.services.publication_planning import create_content_with_plan_and_targets
        from postbridge.workers.celery_app import celery_app

        tid = job.target_core_channel_id
        corr = correlation_id or job.correlation_id or "unknown"
        session = self.job_store.session

        for post in posts_for_publish:
            claimed = self.job_store.claim_publish(
                job.source_channel, post.source_post_id, job.target_channel
            )
            if not claimed:
                self.job_store.insert_enqueued_skip(
                    batch_import_run_id=job_id,
                    source_post_id=post.source_post_id,
                )
                continue
            try:
                body = (post.text or "").strip() or " "
                media_list: list[str] | None = None
                if post.media_urls:
                    media_list = [u for u in post.media_urls if isinstance(u, str) and u]
                    if not media_list:
                        media_list = None
                media_url = post.media_url if isinstance(post.media_url, str) and post.media_url else None
                result = create_content_with_plan_and_targets(
                    session,
                    tenant_id=job.tenant_id,
                    channel_ids=[tid],
                    author_user_id=None,
                    source_type="imported",
                    title=None,
                    body_markdown=body,
                    media_url=media_url,
                    media_urls=media_list,
                    content_status="ready",
                    plan_strategy="immediate",
                    plan_status="scheduled",
                    target_status="pending",
                )
                session.commit()
                target_id = result.publication_target_ids[0]
                self.job_store.insert_enqueued_publication(
                    batch_import_run_id=job_id,
                    source_post_id=post.source_post_id,
                    publication_target_id=target_id,
                )
                celery_app.send_task(
                    "postbridge.publication.process_target",
                    args=[target_id, corr],
                )
            except PostbridgeError as exc:
                self.job_store.release_claim(
                    job.source_channel, post.source_post_id, job.target_channel
                )
                raise
            except Exception as exc:
                self.job_store.release_claim(
                    job.source_channel, post.source_post_id, job.target_channel
                )
                internal_error = InternalError(
                    "Unexpected enqueue error",
                    details={
                        "exception_type": type(exc).__name__,
                        "job_id": job_id,
                        "source_post_id": post.source_post_id,
                    },
                )
                raise internal_error from exc

        self.job_store.mark_dispatch_phase_complete(job_id)

        if not posts_for_publish:
            processed = self.job_store.count_successful_deliveries(job_id)
            self.job_store.mark_completed(job_id, processed_posts=processed)
            if job.started_at:
                started = job.started_at
                if started.tzinfo is None:
                    started = started.replace(tzinfo=UTC)
                duration_sec = (datetime.now(UTC) - started).total_seconds()
                observe_job_duration_seconds(duration_sec)
            inc_jobs_completed()
            log_job_completed(job_id, corr, processed, job.retry_count)

        return 0
