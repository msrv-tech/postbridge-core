import json
import re
from datetime import UTC, datetime
from uuid import uuid4

from postbridge.agent.embeddings import (
    compact_embeddings,
    maintain_embeddings,
    reindex_embedding_drift,
    reindex_channel_content_embeddings,
    reindex_content_item_embedding,
    rotate_channel_content_embeddings,
)
from postbridge.agent import AgentOrchestrator
from postbridge.agent.storage import (
    cleanup_agent_runtime,
    get_agent_run,
    get_agent_task,
    list_due_agent_tasks,
)
from postbridge.db import SESSION_LOCAL
from postbridge.config import get_settings
from postbridge.domain.errors import PostbridgeError
from postbridge.integrations.status_event_client import StatusEventClient
from postbridge.models.domain import PublicationTargetOrm
from sqlalchemy import select
from postbridge.models.domain import TenantOrm
from postbridge.observability.logging import (
    log_agent_cleanup_completed,
    log_jobs_recovered_stuck,
    log_job_processing_start,
    log_job_retry_exhausted,
    log_job_retry_scheduled,
)
from postbridge.observability.metrics import (
    inc_jobs_recovered_stuck,
    inc_jobs_retry_exhausted,
    inc_jobs_retry_scheduled,
)
from postbridge.observability.metrics import inc_live_publish_failed, inc_live_publish_ok
from postbridge.services.publication_target_executor import (
    PublicationTargetExecutor,
    recover_stuck_publication_targets,
    schedule_publication_target_retry,
)
from postbridge.storage.batch_import_run_store import BatchImportRunStore
from postbridge.sync.batch_import_run_reconcile import (
    reconcile_batch_import_runs,
    try_release_batch_import_published_post_claim,
)
from postbridge.sync.service import SyncService
from postbridge.storage.publication_status_event_outbox import PublicationStatusEventOutboxStore
from postbridge.sync.publication_status_events import process_publication_status_event_outbox
from postbridge.sync.status_events import process_status_event_outbox
from postbridge.services.postbridge_scheduled_publish import (
    process_due_scheduled_postbridge_publishes,
)
from postbridge.workers.celery_app import celery_app

_IMAGE_INTENT_RE = re.compile(
    r"(картин|изображ|обложк|иллюстрац|фото|баннер|image|cover|illustration|photo)",
    re.IGNORECASE,
)


def _retry_countdown_seconds(job_id: str, retry_count: int) -> int:
    """Вычисляет countdown до retry с exponential backoff и jitter."""
    settings = get_settings()
    base = settings.batch_import_run_retry_delay_seconds
    factor = settings.batch_import_run_retry_backoff_multiplier
    max_delay = settings.batch_import_run_retry_max_delay_seconds
    exponential = int(base * (factor ** max(retry_count - 1, 0)))
    jitter_window = max(1, min(5, exponential // 5))
    jitter = abs(hash(job_id)) % (jitter_window + 1)
    return min(exponential + jitter, max_delay)


def _live_sync_retry_kwargs(
    *,
    live_sync_source_channel: str | None,
    live_sync_source_post_id: str | None,
    live_sync_target_channel: str | None,
    live_sync_target_platform: str | None,
    live_sync_workspace_id: str | None,
    live_sync_post_json: str | None,
    live_sync_tenant_id: str | None,
    live_sync_target_core_channel_id: str | None,
) -> dict[str, str | None]:
    """Kwargs для apply_async при retry live-sync (Celery сериализует как keyword-only task)."""
    return {
        "live_sync_source_channel": live_sync_source_channel,
        "live_sync_source_post_id": live_sync_source_post_id,
        "live_sync_target_channel": live_sync_target_channel,
        "live_sync_target_platform": live_sync_target_platform,
        "live_sync_workspace_id": live_sync_workspace_id,
        "live_sync_post_json": live_sync_post_json,
        "live_sync_tenant_id": live_sync_tenant_id,
        "live_sync_target_core_channel_id": live_sync_target_core_channel_id,
    }


def _schedule_process_target_retry_apply(
    target_id: str,
    retry_correlation_id: str,
    countdown: int,
    extra_kwargs: dict[str, str | None],
) -> None:
    process_publication_target_task.apply_async(
        args=[target_id, retry_correlation_id],
        kwargs=extra_kwargs,
        countdown=countdown,
    )


@celery_app.task(name="postbridge.sync.process_batch_import_run")
def process_batch_import_run_task(job_id: str, correlation_id: str | None = None) -> int:
    """Celery-задача: выполняет batch import run. При retryable-ошибке планирует retry."""
    corr = correlation_id or "unknown"
    log_job_processing_start(job_id, corr)
    session = SESSION_LOCAL()
    try:
        service = SyncService(session=session)
        store = BatchImportRunStore(session)
        try:
            return service.run_job(job_id, correlation_id=correlation_id)
        except PostbridgeError as exc:
            job = store.get_run(job_id)
            if job is not None and job.target_core_channel_id:
                raise
            if not exc.retryable:
                raise
            settings = get_settings()
            retry_correlation_id = correlation_id or "unknown"
            scheduled = store.schedule_retry(
                job_id=job_id,
                correlation_id=retry_correlation_id,
                max_retries=settings.batch_import_run_max_retries,
            )
            if not scheduled:
                job = store.get_run(job_id)
                if job:
                    log_job_retry_exhausted(
                        job_id, retry_correlation_id, exc.code, job.retry_count
                    )
                    inc_jobs_retry_exhausted()
                raise
            job = store.get_run(job_id)
            log_job_retry_scheduled(
                job_id, retry_correlation_id, job.retry_count if job else 0
            )
            inc_jobs_retry_scheduled()
            retry_count = job.retry_count if job else 1
            countdown = _retry_countdown_seconds(job_id, retry_count)
            process_batch_import_run_task.apply_async(
                args=[job_id, retry_correlation_id],
                countdown=countdown,
            )
            return 0
    finally:
        session.close()


@celery_app.task(name="postbridge.publication.process_target")
def process_publication_target_task(
    target_id: str,
    correlation_id: str | None = None,
    *,
    live_sync_source_channel: str | None = None,
    live_sync_source_post_id: str | None = None,
    live_sync_target_channel: str | None = None,
    live_sync_target_platform: str | None = None,
    live_sync_workspace_id: str | None = None,
    live_sync_post_json: str | None = None,
    live_sync_tenant_id: str | None = None,
    live_sync_target_core_channel_id: str | None = None,
) -> int:
    """Celery: публикует один publication_target; при retryable-ошибке планирует retry.

    Live-sync: тот же retry, что и миграция (schedule_publication_target_retry + apply_async с kwargs);
    claim снимается только при окончательном провале или при сыром Exception; при успехе —
    update_max_message_id и inc_live_publish_ok.
    """
    corr = correlation_id or "unknown"
    log_job_processing_start(target_id, corr)
    session = SESSION_LOCAL()
    is_live_sync = bool(
        live_sync_source_channel
        and live_sync_source_post_id
        and live_sync_target_channel
    )
    live_sync_retry_kwargs = (
        _live_sync_retry_kwargs(
            live_sync_source_channel=live_sync_source_channel,
            live_sync_source_post_id=live_sync_source_post_id,
            live_sync_target_channel=live_sync_target_channel,
            live_sync_target_platform=live_sync_target_platform,
            live_sync_workspace_id=live_sync_workspace_id,
            live_sync_post_json=live_sync_post_json,
            live_sync_tenant_id=live_sync_tenant_id,
            live_sync_target_core_channel_id=live_sync_target_core_channel_id,
        )
        if is_live_sync
        else {}
    )

    def _live_sync_abort_claim() -> None:
        if not is_live_sync:
            return
        store = BatchImportRunStore(session)
        store.release_claim(
            live_sync_source_channel or "",
            live_sync_source_post_id or "",
            live_sync_target_channel or "",
        )
        session.commit()
        inc_live_publish_failed()

    try:
        executor = PublicationTargetExecutor(session=session)
        try:
            ret = executor.run(target_id, correlation_id=correlation_id)
        except PostbridgeError as exc:
            settings = get_settings()
            retry_correlation_id = correlation_id or "unknown"
            scheduled = schedule_publication_target_retry(
                session,
                target_id,
                exc,
                max_retries=settings.batch_import_run_max_retries,
                correlation_id=retry_correlation_id,
            )
            if not scheduled:
                if is_live_sync:
                    _live_sync_abort_claim()
                else:
                    try_release_batch_import_published_post_claim(session, target_id)
                    if exc.retryable:
                        t = session.get(PublicationTargetOrm, target_id)
                        if t:
                            log_job_retry_exhausted(
                                target_id,
                                retry_correlation_id,
                                exc.code,
                                t.retry_count,
                            )
                        inc_jobs_retry_exhausted()
                raise
            t = session.get(PublicationTargetOrm, target_id)
            log_job_retry_scheduled(
                target_id, retry_correlation_id, t.retry_count if t else 0
            )
            inc_jobs_retry_scheduled()
            retry_count = t.retry_count if t else 1
            countdown = _retry_countdown_seconds(target_id, retry_count)
            _schedule_process_target_retry_apply(
                target_id,
                retry_correlation_id,
                countdown,
                live_sync_retry_kwargs,
            )
            return 0
        except Exception:
            if is_live_sync:
                _live_sync_abort_claim()
            raise
        if ret == 1 and is_live_sync:
            target_row = session.get(PublicationTargetOrm, target_id)
            ext = (target_row.external_post_id if target_row else None) or ""
            if ext and live_sync_target_platform != "rss":
                store = BatchImportRunStore(session)
                store.update_max_message_id(
                    live_sync_source_channel or "",
                    live_sync_source_post_id or "",
                    live_sync_target_channel or "",
                    ext,
                )
                session.commit()
            inc_live_publish_ok()
        return ret
    finally:
        session.close()


@celery_app.task(name="postbridge.sync.recover_stuck_jobs")
def recover_stuck_jobs_task() -> int:
    """Celery Beat: помечает зависшие running batch import run как failed (INTERNAL_JOB_STUCK_TIMEOUT).

    Затрагивает только run'ы без unified dispatch: нет target_core_channel_id или не выставлен
    batch_import_dispatch_enqueued_at. См. BatchImportRunStore.recover_stuck_running_runs.
    """
    correlation_id = f"watchdog-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    session = SESSION_LOCAL()
    try:
        settings = get_settings()
        store = BatchImportRunStore(session)
        recovered = store.recover_stuck_running_runs(
            timeout_seconds=settings.batch_import_run_stuck_timeout_seconds,
            correlation_id=correlation_id,
        )
        if recovered > 0:
            inc_jobs_recovered_stuck()
            log_jobs_recovered_stuck(correlation_id, recovered)
        return recovered
    finally:
        session.close()


@celery_app.task(name="postbridge.agent.run_task")
def run_agent_task_task(task_id: str, tenant_id: str) -> dict:
    session = SESSION_LOCAL()
    try:
        task = get_agent_task(session, tenant_id=tenant_id, task_id=task_id)
        orchestrator = AgentOrchestrator(session)
        task_config = orchestrator.parse_task_config(task.task_config_json)
        result = orchestrator.run_once(
            tenant_id=tenant_id,
            channel_id=task.channel_id,
            mode=task.mode,
            user_request=task.goal_text,
            topic_definition=task.goal_text if task.mode == "topic_scout" else None,
            editorial_instructions=task.editorial_instructions,
            content_item_id=task.content_item_id,
            max_candidates=task.max_candidates_per_run,
            agent_task=task,
            image_request=bool(
                isinstance(task.editorial_instructions, str) and _IMAGE_INTENT_RE.search(task.editorial_instructions)
            ),
            seed_urls=task_config.get("seed_urls") if isinstance(task_config.get("seed_urls"), list) else None,
        )
        usage_event_payload: dict | None = None
        run_id = str(result.get("agent_run_id") or "")
        if run_id:
            run = get_agent_run(session, tenant_id=tenant_id, run_id=run_id)
            token_usage = json.loads(run.token_usage_json) if run.token_usage_json else {}
            total_tokens = 0
            if isinstance(token_usage, dict):
                try:
                    total_tokens = max(0, int(token_usage.get("total_tokens") or 0))
                except (TypeError, ValueError):
                    total_tokens = 0
            usage_event_payload = {
                "event_id": str(uuid4()),
                "contract_version": "1.6",
                "event_type": "agent.run.completed",
                "occurred_at": datetime.now(UTC).isoformat(),
                "tenant_id": tenant_id,
                "agent_run": {
                    "id": run.id,
                    "status": run.status,
                    "channel_id": run.channel_id,
                    "trigger_type": run.trigger_type,
                    "agent_task_id": run.agent_task_id,
                    "billed_user_id": task.created_by,
                    "total_tokens": total_tokens,
                    "model": run.model,
                },
            }
        session.commit()
        if usage_event_payload is not None:
            publish_agent_run_usage_event_task.delay(
                usage_event_payload,
                usage_event_payload["event_id"],
            )
        return result
    finally:
        session.close()


@celery_app.task(
    name="postbridge.agent.publish_run_usage_event",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
)
def publish_agent_run_usage_event_task(payload: dict, correlation_id: str | None = None) -> int:
    client = StatusEventClient()
    if not client.is_enabled():
        return 0
    client.publish_json_payload(payload, correlation_id=correlation_id or "agent-run-usage")
    return 1


@celery_app.task(name="postbridge.agent.process_due_tasks")
def process_due_agent_tasks_task() -> int:
    session = SESSION_LOCAL()
    try:
        due = list_due_agent_tasks(session)
        task_refs = [(row.id, row.tenant_id) for row in due]
        session.close()
        for task_id, tenant_id in task_refs:
            run_agent_task_task.delay(task_id, tenant_id)
        return len(task_refs)
    finally:
        try:
            session.close()
        except Exception:
            pass


@celery_app.task(name="postbridge.agent.reindex_channel_embeddings")
def reindex_channel_embeddings_task(tenant_id: str, channel_id: str, limit: int = 100, offset: int = 0) -> dict:
    session = SESSION_LOCAL()
    try:
        result = reindex_channel_content_embeddings(
            session,
            tenant_id=tenant_id,
            channel_id=channel_id,
            limit=limit,
            offset=offset,
        )
        session.commit()
        return result
    finally:
        session.close()


@celery_app.task(name="postbridge.agent.reindex_content_item_embedding")
def reindex_content_item_embedding_task(tenant_id: str, channel_id: str, content_item_id: str) -> dict:
    session = SESSION_LOCAL()
    try:
        result = reindex_content_item_embedding(
            session,
            tenant_id=tenant_id,
            channel_id=channel_id,
            content_item_id=content_item_id,
        )
        session.commit()
        return result
    finally:
        session.close()


@celery_app.task(name="postbridge.agent.rotate_channel_embeddings")
def rotate_channel_embeddings_task(tenant_id: str, channel_id: str, limit: int = 100, offset: int = 0) -> dict:
    session = SESSION_LOCAL()
    try:
        result = rotate_channel_content_embeddings(
            session,
            tenant_id=tenant_id,
            channel_id=channel_id,
            limit=limit,
            offset=offset,
        )
        session.commit()
        return result
    finally:
        session.close()


@celery_app.task(name="postbridge.agent.reindex_embedding_drift")
def reindex_embedding_drift_task(
    tenant_id: str | None = None,
    channel_id: str | None = None,
    channel_limit: int | None = None,
    item_limit: int | None = None,
    channel_offset: int = 0,
) -> dict:
    settings = get_settings()
    effective_channel_limit = channel_limit or settings.agent_embedding_drift_channel_limit
    effective_item_limit = item_limit or settings.agent_embedding_drift_item_limit
    session = SESSION_LOCAL()
    try:
        tenant_ids = [tenant_id] if tenant_id else list(session.scalars(select(TenantOrm.id)).all())
    finally:
        session.close()
    summaries: list[dict] = []
    for current_tenant_id in tenant_ids:
        current_session = SESSION_LOCAL()
        try:
            result = reindex_embedding_drift(
                current_session,
                tenant_id=current_tenant_id,
                channel_id=channel_id,
                channel_limit=effective_channel_limit,
                item_limit=effective_item_limit,
                channel_offset=channel_offset,
            )
            current_session.commit()
            summaries.append(result)
        finally:
            current_session.close()
    return {
        "tenants_processed": len(summaries),
        "channels_reindexed": sum(int(item.get("channels_reindexed", 0)) for item in summaries),
        "rotated_embeddings": sum(int(item.get("rotated_embeddings", 0)) for item in summaries),
        "summaries": summaries,
    }


@celery_app.task(name="postbridge.agent.maintain_embeddings")
def maintain_embeddings_task(
    tenant_id: str | None = None,
    channel_id: str | None = None,
    prune_orphans: bool = True,
    prune_malformed: bool = True,
    optimize_native: bool = True,
    row_limit: int | None = None,
    offset: int = 0,
    after_id: str | None = None,
) -> dict:
    session = SESSION_LOCAL()
    try:
        tenant_ids = [tenant_id] if tenant_id else list(session.scalars(select(TenantOrm.id)).all())
    finally:
        session.close()
    summaries: list[dict] = []
    for current_tenant_id in tenant_ids:
        current_session = SESSION_LOCAL()
        try:
            result = maintain_embeddings(
                current_session,
                tenant_id=current_tenant_id,
                channel_id=channel_id,
                prune_orphans=prune_orphans,
                prune_malformed=prune_malformed,
                optimize_native=optimize_native,
                limit=row_limit,
                offset=offset,
                after_id=after_id,
            )
            current_session.commit()
            summaries.append(result)
        finally:
            current_session.close()
    return {
        "tenants_processed": len(summaries),
        "deleted_orphan_embeddings": sum(int(item.get("deleted_orphan_embeddings", 0)) for item in summaries),
        "deleted_malformed_embeddings": sum(int(item.get("deleted_malformed_embeddings", 0)) for item in summaries),
        "summaries": summaries,
    }


@celery_app.task(name="postbridge.agent.compact_embeddings")
def compact_embeddings_task(
    tenant_id: str | None = None,
    channel_id: str | None = None,
    candidate_retention_days: int | None = None,
    optimize_native: bool = True,
) -> dict:
    settings = get_settings()
    effective_retention = candidate_retention_days or settings.agent_embedding_candidate_retention_days
    session = SESSION_LOCAL()
    try:
        tenant_ids = [tenant_id] if tenant_id else list(session.scalars(select(TenantOrm.id)).all())
    finally:
        session.close()
    summaries: list[dict] = []
    for current_tenant_id in tenant_ids:
        current_session = SESSION_LOCAL()
        try:
            result = compact_embeddings(
                current_session,
                tenant_id=current_tenant_id,
                channel_id=channel_id,
                candidate_retention_days=effective_retention,
                optimize_native=optimize_native,
            )
            current_session.commit()
            summaries.append(result)
        finally:
            current_session.close()
    return {
        "tenants_processed": len(summaries),
        "deleted_candidate_embeddings": sum(int(item.get("deleted_candidate_embeddings", 0)) for item in summaries),
        "summaries": summaries,
    }


@celery_app.task(name="postbridge.agent.cleanup_runtime")
def cleanup_agent_runtime_task(
    tenant_id: str | None = None,
    retention_days: int | None = None,
    trace_retention_days: int | None = None,
    review_retention_days: int | None = None,
    review_body_retention_days: int | None = None,
    fingerprint_retention_days: int | None = None,
) -> dict:
    session = SESSION_LOCAL()
    try:
        settings = get_settings()
        effective_retention = retention_days or settings.agent_cleanup_retention_days
        effective_trace_retention = trace_retention_days or settings.agent_trace_retention_days
        effective_review_retention = review_retention_days or settings.agent_review_retention_days
        effective_review_body_retention = review_body_retention_days or settings.agent_review_body_retention_days
        effective_fingerprint_retention = fingerprint_retention_days or settings.agent_fingerprint_retention_days
        result = cleanup_agent_runtime(
            session,
            tenant_id=tenant_id,
            retention_days=effective_retention,
            trace_retention_days=effective_trace_retention,
            review_retention_days=effective_review_retention,
            review_body_retention_days=effective_review_body_retention,
            fingerprint_retention_days=effective_fingerprint_retention,
        )
        session.commit()
        log_agent_cleanup_completed(
            tenant_id=tenant_id,
            retention_days=effective_retention,
            deleted_runs=result["deleted_runs"],
            deleted_review_items=result["deleted_review_items"],
        )
        return result
    finally:
        session.close()


@celery_app.task(name="postbridge.publication.recover_stuck_targets")
def recover_stuck_publication_targets_task() -> int:
    """Celery Beat: сбрасывает зависшие publishing targets в pending и ставит их в очередь."""
    session = SESSION_LOCAL()
    try:
        settings = get_settings()
        recovered_ids = recover_stuck_publication_targets(
            session, timeout_seconds=settings.batch_import_run_stuck_timeout_seconds
        )
        corr_prefix = f"recover-stuck-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        for target_id in recovered_ids:
            process_publication_target_task.delay(target_id, f"{corr_prefix}-{target_id}")
        return len(recovered_ids)
    finally:
        session.close()


@celery_app.task(name="postbridge.sync.reconcile_batch_import_runs")
def reconcile_batch_import_runs_task() -> int:
    """Celery Beat: завершает batch import runs по статусам publication_targets."""
    session = SESSION_LOCAL()
    try:
        return reconcile_batch_import_runs(session)
    finally:
        session.close()


@celery_app.task(name="postbridge.sync.dispatch_status_event_outbox")
def dispatch_status_event_outbox_task() -> int:
    """Celery Beat: обрабатывает outbox статусных событий (отправка в SaaS)."""
    session = SESSION_LOCAL()
    try:
        store = BatchImportRunStore(session)
        n = process_status_event_outbox(store)
        pub_store = PublicationStatusEventOutboxStore(session)
        n += process_publication_status_event_outbox(pub_store)
        return n
    finally:
        session.close()


@celery_app.task(name="postbridge.postbridge.process_scheduled_publishes")
def process_scheduled_postbridge_publishes_task() -> int:
    """Celery Beat: draft→published по scheduled_publish_at и постановка live-sync в очередь."""
    session = SESSION_LOCAL()
    try:
        return process_due_scheduled_postbridge_publishes(session)
    finally:
        session.close()
