"""Live-sync: ingest + очередь postbridge.publication.process_target; edit — HTTP edit-single."""

from __future__ import annotations

import logging
import threading

import httpx

from postbridge.config import get_settings
from postbridge.db import SESSION_LOCAL
from postbridge.observability.metrics import inc_live_publish_failed

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 30.0


def _core_base_url() -> str:
    return get_settings().core_base_url.rstrip("/")


def _sync_publish_headers() -> dict[str, str]:
    token = get_settings().sync_publish_token
    if token:
        return {"X-Sync-Publish-Token": token}
    return {}


def deliver_publish_to_core(
    source_channel: str,
    target_channel: str,
    post: dict,
    saas_workspace_id: str,
    target_platform: str = "max",
    *,
    tenant_id: str,
    target_core_channel_id: str,
    producer: str | None = None,
    persist_failure: bool = True,
) -> dict[str, str]:
    """Ingest + постановка в postbridge.publication.process_target (единая очередь исполнения)."""
    from postbridge.services.live_sync_publish_service import (
        ingest_live_sync_publication,
        live_sync_executor_task_kwargs,
    )
    from postbridge.workers.tasks import process_publication_target_task

    prod = f" producer={producer}" if producer else ""
    try:
        media = post.get("media_url") or post.get("media_urls")
        logger.info(
            "live_sync publishing:%s source=%s target=%s platform=%s media=%s",
            prod,
            source_channel,
            target_channel,
            target_platform,
            "yes" if media else "no",
        )
        corr = f"live-sync-{producer}" if producer else "live-sync-queue"
        session = SESSION_LOCAL()
        try:
            ing = ingest_live_sync_publication(
                session,
                tenant_id=tenant_id,
                target_core_channel_id=target_core_channel_id,
                source_channel=source_channel,
                target_channel=target_channel,
                target_platform=target_platform,
                post=post,
                correlation_id=corr,
            )
            if ing.skipped:
                return {"status": "ok", "source_post_id": ing.source_post_id}
            ls_kw = live_sync_executor_task_kwargs(
                source_channel=source_channel,
                source_post_id=ing.source_post_id,
                target_channel=target_channel,
                target_platform=target_platform,
                post=post,
                tenant_id=tenant_id,
                target_core_channel_id=target_core_channel_id,
                workspace_id=saas_workspace_id,
            )
            process_publication_target_task.delay(ing.target_id, corr, **ls_kw)
        finally:
            session.close()
        logger.info(
            "live_sync queued:%s source=%s post=%s target=%s",
            prod,
            source_channel,
            post.get("source_post_id"),
            target_channel,
        )
        return {"status": "ok", "source_post_id": post.get("source_post_id", "")}
    except Exception as e:
        if persist_failure:
            inc_live_publish_failed()
        err_str = str(e)
        logger.warning(
            "live_sync publish failed:%s source=%s target=%s: %s",
            prod,
            source_channel,
            target_channel,
            err_str,
        )
        return {"status": "failed", "source_post_id": post.get("source_post_id", "")}


def deliver_edit_to_core(
    source_channel: str,
    target_channel: str,
    post: dict,
    target_platform: str = "max",
    workspace_id: str = "",
    *,
    core_tenant_id: str = "",
    target_core_channel_id: str = "",
    producer: str | None = None,
) -> dict[str, str]:
    """POST /internal/sync/edit-single; при 404 — fallback на deliver_publish_to_core."""
    if not core_tenant_id or not target_core_channel_id:
        raise ValueError(
            "deliver_edit_to_core requires core_tenant_id and target_core_channel_id"
        )
    headers = {**_sync_publish_headers(), "Content-Type": "application/json"}
    body: dict = {
        "source_channel": source_channel,
        "target_channel": target_channel,
        "post": post,
        "target_platform": target_platform,
        "tenant_id": core_tenant_id,
        "target_core_channel_id": target_core_channel_id,
    }
    prod = f" producer={producer}" if producer else ""
    try:
        r = httpx.post(
            f"{_core_base_url()}/internal/sync/edit-single",
            headers=headers,
            json=body,
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        logger.info(
            "live_sync edited:%s source=%s post=%s target=%s",
            prod,
            source_channel,
            post.get("source_post_id"),
            target_channel,
        )
        return {"status": "ok", "source_post_id": post.get("source_post_id", "")}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.info(
                "live_sync edit 404, fallback to publish:%s source=%s target=%s",
                prod,
                source_channel,
                target_channel,
            )
            return deliver_publish_to_core(
                source_channel=source_channel,
                target_channel=target_channel,
                post=post,
                saas_workspace_id=workspace_id,
                target_platform=target_platform,
                tenant_id=core_tenant_id,
                target_core_channel_id=target_core_channel_id,
                producer=producer,
            )
        logger.warning(
            "live_sync edit failed:%s source=%s target=%s status=%s: %s",
            prod,
            source_channel,
            target_channel,
            e.response.status_code,
            e.response.text[:200],
        )
        return {"status": "failed", "source_post_id": post.get("source_post_id", ""), "error": str(e)}
    except Exception as e:
        logger.warning(
            "live_sync edit failed:%s source=%s target=%s: %s",
            prod,
            source_channel,
            target_channel,
            e,
        )
        return {"status": "failed", "source_post_id": post.get("source_post_id", ""), "error": str(e)}


def _celery_eager() -> bool:
    return bool(get_settings().celery_task_always_eager)


def queue_live_sync_publish(
    source_channel: str,
    target_channel: str,
    post: dict,
    workspace_id: str,
    target_platform: str = "max",
    *,
    core_tenant_id: str,
    target_core_channel_id: str,
    producer: str | None = None,
) -> None:
    if _celery_eager():
        threading.Thread(
            target=deliver_publish_to_core,
            kwargs={
                "source_channel": source_channel,
                "target_channel": target_channel,
                "post": post,
                "saas_workspace_id": workspace_id,
                "target_platform": target_platform,
                "tenant_id": core_tenant_id,
                "target_core_channel_id": target_core_channel_id,
                "producer": producer,
            },
            daemon=True,
        ).start()
        return
    from postbridge.workers.live_sync_tasks import publish_live_sync_post

    publish_live_sync_post.delay(
        source_channel=source_channel,
        target_channel=target_channel,
        post=post,
        workspace_id=workspace_id,
        target_platform=target_platform,
        core_tenant_id=core_tenant_id,
        target_core_channel_id=target_core_channel_id,
        producer=producer,
    )


def queue_live_sync_edit(
    source_channel: str,
    target_channel: str,
    post: dict,
    target_platform: str = "max",
    workspace_id: str = "",
    *,
    core_tenant_id: str = "",
    target_core_channel_id: str = "",
    producer: str | None = None,
) -> None:
    if _celery_eager():
        threading.Thread(
            target=deliver_edit_to_core,
            kwargs={
                "source_channel": source_channel,
                "target_channel": target_channel,
                "post": post,
                "target_platform": target_platform,
                "workspace_id": workspace_id,
                "core_tenant_id": core_tenant_id,
                "target_core_channel_id": target_core_channel_id,
                "producer": producer,
            },
            daemon=True,
        ).start()
        return
    from postbridge.workers.live_sync_tasks import edit_live_sync_post

    edit_live_sync_post.delay(
        source_channel=source_channel,
        target_channel=target_channel,
        post=post,
        target_platform=target_platform,
        workspace_id=workspace_id,
        core_tenant_id=core_tenant_id,
        target_core_channel_id=target_core_channel_id,
        producer=producer,
    )
