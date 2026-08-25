"""Celery: live-sync публикация в Core API."""

from __future__ import annotations

import logging

from postbridge.services.live_sync_queue import (
    deliver_edit_to_core,
    deliver_publish_to_core,
    queue_live_sync_edit as enqueue_edit,
    queue_live_sync_publish as enqueue_publish,
)
from postbridge.workers.celery_app import celery_app
from postbridge.workers.media_group_buffer import get_media_group_generation, pop_media_group

logger = logging.getLogger(__name__)

do_publish_to_core = deliver_publish_to_core
do_edit_to_core = deliver_edit_to_core


@celery_app.task(name="postbridge.live_sync.publish_post")
def publish_live_sync_post(
    source_channel: str,
    target_channel: str,
    post: dict,
    workspace_id: str,
    target_platform: str = "max",
    *,
    core_tenant_id: str,
    target_core_channel_id: str,
    producer: str | None = None,
) -> dict[str, str]:
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


@celery_app.task(name="postbridge.live_sync.edit_post")
def edit_live_sync_post(
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
    return deliver_edit_to_core(
        source_channel=source_channel,
        target_channel=target_channel,
        post=post,
        target_platform=target_platform,
        workspace_id=workspace_id,
        core_tenant_id=core_tenant_id,
        target_core_channel_id=target_core_channel_id,
        producer=producer,
    )


@celery_app.task(name="postbridge.live_sync.publish_media_group")
def publish_live_sync_media_group(
    source_channel: str,
    target_channel: str,
    workspace_id: str,
    media_group_id: str,
    target_platform: str = "max",
    *,
    core_tenant_id: str,
    target_core_channel_id: str,
    producer: str | None = None,
    buffer_generation: int | None = None,
) -> dict[str, str]:
    if buffer_generation is not None:
        current_generation = get_media_group_generation(source_channel, media_group_id)
        if current_generation != buffer_generation:
            logger.info(
                "media_group superseded: chat=%s mg=%s expected=%s current=%s",
                source_channel,
                media_group_id,
                buffer_generation,
                current_generation,
            )
            return {"status": "skipped", "reason": "superseded"}

    items = pop_media_group(source_channel, media_group_id)
    if not items:
        logger.info("media_group empty or expired: chat=%s mg=%s", source_channel, media_group_id)
        return {"status": "skipped", "reason": "empty"}

    text_parts = [it["text"] for it in items if (it.get("text") or "").strip()]
    text = text_parts[0] if text_parts else ""
    media_urls = [it["media_url"] for it in items if it.get("media_url")]
    source_post_id = f"mg:{media_group_id}"

    post = {
        "source_post_id": source_post_id,
        "text": text,
        "media_url": None,
        "media_urls": media_urls,
    }
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
