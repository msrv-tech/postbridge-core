"""Ingest live-sync: claim + content/plan/target, без исполнения (очередь process_publication_target)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from postbridge.domain.errors import PostbridgeError
from postbridge.services.publication_planning import create_content_with_plan_and_targets
from postbridge.storage.batch_import_run_store import BatchImportRunStore

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LiveSyncIngestResult:
    """Результат ingest: либо пропуск (уже в ledger), либо id target для очереди исполнения."""

    skipped: bool
    source_post_id: str
    target_id: str | None = None


def ingest_live_sync_publication(
    session: Session,
    *,
    tenant_id: str,
    target_core_channel_id: str,
    source_channel: str,
    target_channel: str,
    target_platform: str,
    post: dict,
    correlation_id: str,
) -> LiveSyncIngestResult:
    """
    claim_publish → create_content_with_plan_and_targets → commit.
    При ошибке до commit — release_claim и rollback.
    """
    post_data = post
    source_post_id = post_data.get("source_post_id") or str(post_data.get("message_id", ""))
    text = post_data.get("text") or post_data.get("caption") or ""
    media_url = post_data.get("media_url")
    media_urls = post_data.get("media_urls")

    job_store = BatchImportRunStore(session)
    claimed = job_store.claim_publish(source_channel, source_post_id, target_channel)
    if not claimed:
        return LiveSyncIngestResult(skipped=True, source_post_id=source_post_id)

    body = (text or "").strip() or " "
    media_list: list[str] | None = None
    if media_urls:
        if isinstance(media_urls, list):
            media_list = [u for u in media_urls if isinstance(u, str) and u]
        if not media_list:
            media_list = None

    try:
        result = create_content_with_plan_and_targets(
            session,
            tenant_id=tenant_id,
            channel_ids=[target_core_channel_id],
            author_user_id=None,
            source_type="imported",
            title=None,
            body_markdown=body,
            content_status="ready",
            plan_strategy="immediate",
            plan_status="scheduled",
            target_status="pending",
            media_url=media_url if isinstance(media_url, str) and media_url else None,
            media_urls=media_list,
        )
        session.commit()
        target_id = result.publication_target_ids[0]
        logger.info(
            "live_sync ingest ok: source=%s post=%s target_id=%s corr=%s",
            source_channel,
            source_post_id,
            target_id,
            correlation_id,
        )
        return LiveSyncIngestResult(
            skipped=False,
            source_post_id=source_post_id,
            target_id=target_id,
        )
    except PostbridgeError:
        job_store.release_claim(source_channel, source_post_id, target_channel)
        session.rollback()
        raise
    except Exception:
        job_store.release_claim(source_channel, source_post_id, target_channel)
        session.rollback()
        raise


def live_sync_executor_task_kwargs(
    *,
    source_channel: str,
    source_post_id: str,
    target_channel: str,
    target_platform: str,
    post: dict,
    tenant_id: str,
    target_core_channel_id: str,
    workspace_id: str = "",
) -> dict[str, str | None]:
    """Kwargs для process_publication_target_task.delay(..., **kwargs) в live-sync."""
    return {
        "live_sync_source_channel": source_channel,
        "live_sync_source_post_id": source_post_id,
        "live_sync_target_channel": target_channel,
        "live_sync_target_platform": target_platform,
        "live_sync_workspace_id": workspace_id or None,
        "live_sync_post_json": json.dumps(post, ensure_ascii=False),
        "live_sync_tenant_id": tenant_id,
        "live_sync_target_core_channel_id": target_core_channel_id,
    }
