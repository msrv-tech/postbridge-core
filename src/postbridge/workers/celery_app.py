import sentry_sdk
from celery import Celery
from sentry_sdk.integrations.celery import CeleryIntegration

from postbridge.config import get_settings, validate_base_settings


def create_celery_app() -> Celery:
    """Создаёт и конфигурирует Celery-приложение (Redis, beat schedule)."""
    settings = get_settings()
    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.app_env,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            integrations=[CeleryIntegration(monitor_beat_tasks=True)],
        )
    validate_base_settings(settings)
    app = Celery(
        "postbridge",
        broker=settings.redis_url,
        backend=settings.redis_url,
        include=[
            "postbridge.workers.tasks",
            "postbridge.workers.live_sync_tasks",
            "postbridge.workers.media_generation_tasks",
        ],
    )
    app.conf.update(
        task_always_eager=settings.celery_task_always_eager,
        task_eager_propagates=True,
        beat_schedule={
            "recover-stuck-sync-jobs": {
                "task": "postbridge.sync.recover_stuck_jobs",
                "schedule": 60.0,
            },
            "recover-stuck-publication-targets": {
                "task": "postbridge.publication.recover_stuck_targets",
                "schedule": 60.0,
            },
            "reconcile-batch-import-runs": {
                "task": "postbridge.sync.reconcile_batch_import_runs",
                "schedule": 30.0,
            },
            "dispatch-status-events-outbox": {
                "task": "postbridge.sync.dispatch_status_event_outbox",
                "schedule": 5.0,
            },
            "process-scheduled-postbridge-publishes": {
                "task": "postbridge.postbridge.process_scheduled_publishes",
                "schedule": 60.0,
            },
            "process-due-agent-tasks": {
                "task": "postbridge.agent.process_due_tasks",
                "schedule": 60.0,
            },
            "cleanup-agent-runtime": {
                "task": "postbridge.agent.cleanup_runtime",
                "schedule": 3600.0,
            },
            "reindex-agent-embedding-drift": {
                "task": "postbridge.agent.reindex_embedding_drift",
                "schedule": float(settings.agent_embedding_drift_reindex_interval_seconds),
            },
            "maintain-agent-embeddings": {
                "task": "postbridge.agent.maintain_embeddings",
                "schedule": float(settings.agent_embedding_maintenance_interval_seconds),
            },
            "compact-agent-embeddings": {
                "task": "postbridge.agent.compact_embeddings",
                "schedule": float(settings.agent_embedding_compaction_interval_seconds),
            },
        },
    )
    return app


celery_app = create_celery_app()
