"""Live-sync endpoint: publish single post from Telegram channel_post to MAX."""

from __future__ import annotations

import inspect
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from postbridge.api.internal_auth import check_sync_publish_auth
from postbridge.db import get_db_session
from postbridge.domain.errors import ExternalApiError, PostbridgeError, ValidationError
from postbridge.domain.models import PostPayload
from postbridge.integrations.channel_credentials import load_channel_credential_row
from postbridge.integrations.registry import (
    decode_publish_credentials_for_platform,
    get_fetcher,
    get_platform_capabilities,
    get_publisher,
    resolve_fetch_credentials_for_core_channel,
)
from postbridge.models.domain import ChannelOrm, RssFeedOrm
from postbridge.observability.metrics import inc_live_publish_failed, inc_live_publish_ok
from postbridge.db import RssFeedItemOrm
from postbridge.storage.batch_import_run_store import BatchImportRunStore

logger = logging.getLogger(__name__)
LIVE_PUBLISH_RETRY_ATTEMPTS = 3
LIVE_PUBLISH_RETRY_BASE_DELAY = 1.0

router = APIRouter()


class PublishSingleRequest(BaseModel):
    """Тело запроса live-sync: каналы и пост для публикации."""

    source_channel: str
    target_channel: str
    post: dict  # {"source_post_id": str, "text": str, "media_url": str | None, "media_urls": list[str] | None}
    target_platform: str = "max"
    tenant_id: str = Field(min_length=36, max_length=36)
    target_core_channel_id: str = Field(min_length=36, max_length=36)


class EditSingleRequest(BaseModel):
    """Тело запроса edit-single: каналы и пост для редактирования."""

    source_channel: str
    target_channel: str
    post: dict  # {"source_post_id": str, "text": str, "media_url": str | None, "media_urls": list[str] | None}
    target_platform: str = "max"
    tenant_id: str = Field(min_length=36, max_length=36)
    target_core_channel_id: str = Field(min_length=36, max_length=36)


class DeleteSingleRequest(BaseModel):
    """Тело запроса delete-single: каналы и source_post_id для удаления."""

    source_channel: str
    target_channel: str
    source_post_id: str
    target_platform: str = "max"
    tenant_id: str = Field(min_length=36, max_length=36)
    target_core_channel_id: str = Field(min_length=36, max_length=36)


class FetchPostsRequest(BaseModel):
    """Тело запроса fetch-posts: забор постов из источника (для RSS и т.д.)."""

    source_platform: str = Field(min_length=1, max_length=32)
    source_channel: str = Field(min_length=1, max_length=256)
    limit: int = Field(default=25, ge=1, le=100)
    tenant_id: str = Field(min_length=36, max_length=36)
    source_core_channel_id: str = Field(min_length=36, max_length=36)


class PostItem(BaseModel):
    """Один пост в ответе fetch-posts."""

    source_post_id: str
    text: str
    media_url: str | None = None
    media_urls: list[str] | None = None


class FetchPostsResponse(BaseModel):
    """Ответ fetch-posts: список постов."""

    posts: list[PostItem]


@router.post("/internal/sync/publish-single", include_in_schema=False)
def publish_single(
    payload: PublishSingleRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Ingest live-sync + постановка в общую очередь postbridge.publication.process_target."""
    check_sync_publish_auth(request)
    from postbridge.services.live_sync_publish_service import (
        ingest_live_sync_publication,
        live_sync_executor_task_kwargs,
    )
    from postbridge.workers.tasks import process_publication_target_task

    post_data = payload.post
    source_post_id = post_data.get("source_post_id") or str(
        post_data.get("message_id", "")
    )
    corr = request.headers.get("X-Correlation-Id") or "live-sync"

    try:
        ing = ingest_live_sync_publication(
            session,
            tenant_id=payload.tenant_id,
            target_core_channel_id=payload.target_core_channel_id,
            source_channel=payload.source_channel,
            target_channel=payload.target_channel,
            target_platform=payload.target_platform,
            post=payload.post,
            correlation_id=corr,
        )
        if ing.skipped:
            return {"status": "ok", "source_post_id": ing.source_post_id}
        ls_kw = live_sync_executor_task_kwargs(
            source_channel=payload.source_channel,
            source_post_id=ing.source_post_id,
            target_channel=payload.target_channel,
            target_platform=payload.target_platform,
            post=payload.post,
            tenant_id=payload.tenant_id,
            target_core_channel_id=payload.target_core_channel_id,
        )
        process_publication_target_task.delay(ing.target_id, corr, **ls_kw)
        return {"status": "ok", "source_post_id": source_post_id}
    except PostbridgeError:
        inc_live_publish_failed()
        raise
    except Exception:
        inc_live_publish_failed()
        raise


@router.post("/internal/sync/edit-single", include_in_schema=False)
def edit_single(
    payload: EditSingleRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Редактирует пост в MAX по source_post_id (требует сохранённый max_message_id)."""
    check_sync_publish_auth(request)
    post_data = payload.post
    source_post_id = post_data.get("source_post_id") or str(post_data.get("message_id", ""))
    text = post_data.get("text") or post_data.get("caption") or ""
    media_url = post_data.get("media_url")
    media_urls = post_data.get("media_urls")

    job_store = BatchImportRunStore(session)
    row = job_store.get_published_post(
        payload.source_channel, source_post_id, payload.target_channel
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="error.live_sync.post_not_found",
        )
    if not row.max_message_id:
        raise HTTPException(
            status_code=409,
            detail="error.live_sync.post_missing_tracking_for_edit",
        )

    tgt_ch = session.get(ChannelOrm, payload.target_core_channel_id)
    if tgt_ch is None or tgt_ch.tenant_id != payload.tenant_id:
        raise HTTPException(
            status_code=404,
            detail="error.live_sync.target_channel_not_found",
        )
    if tgt_ch.platform != payload.target_platform:
        raise HTTPException(
            status_code=422,
            detail="error.live_sync.target_platform_mismatch",
        )
    caps = get_platform_capabilities(payload.target_platform)
    if caps is not None and not caps.live_sync_publish_supported:
        raise HTTPException(
            status_code=501,
            detail="error.live_sync.publish_edit_not_supported",
        )
    cred_row = load_channel_credential_row(
        session, payload.target_core_channel_id, payload.tenant_id
    )
    creds = decode_publish_credentials_for_platform(tgt_ch.platform, cred_row)

    publisher = get_publisher(payload.target_platform)
    edit_fn = getattr(publisher, "edit_message", None)
    if not edit_fn:
        raise HTTPException(
            status_code=501,
            detail="error.live_sync.edit_not_supported",
        )
    try:
        edit_fn(
            message_id=row.max_message_id,
            text=text,
            media_url=media_url,
            media_urls=media_urls,
            credentials=creds,
            target_channel=payload.target_channel,
        )
    except ExternalApiError as e:
        detail_msg = e.message or str(e) or "Unknown external API error"
        # VK: wall.edit недоступен с community token ("group auth"). Fallback: удалить старый пост и переопубликовать.
        if (
            payload.target_platform == "vk"
            and "group auth" in detail_msg.lower()
        ):
            logger.info(
                "edit-single VK group auth fallback: delete+republish for source=%s post=%s target=%s",
                payload.source_channel,
                source_post_id,
                payload.target_channel,
            )
            # Пытаемся удалить старый пост (wall.delete тоже может быть недоступен с community token)
            delete_fn = getattr(publisher, "delete_post", None)
            if delete_fn:
                try:
                    delete_fn(
                        target_channel=payload.target_channel,
                        post_id=row.max_message_id,
                        credentials=creds,
                    )
                    logger.info("VK delete_post succeeded for post_id=%s", row.max_message_id)
                except ExternalApiError as del_err:
                    del_msg = del_err.message or str(del_err) or ""
                    if "group auth" in del_msg.lower():
                        logger.info(
                            "VK wall.delete unavailable with community token, skipping delete"
                        )
                    else:
                        raise
            job_store.release_claim(
                payload.source_channel, source_post_id, payload.target_channel
            )
            session.flush()
            claimed = job_store.claim_publish(
                payload.source_channel, source_post_id, payload.target_channel
            )
            if not claimed:
                session.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="error.live_sync.concurrent_republish_conflict",
                )
            post = PostPayload(
                source_post_id=source_post_id,
                text=text,
                media_url=media_url,
                media_urls=media_urls,
            )
            try:
                max_message_id = publisher.publish_post(
                    target_channel=payload.target_channel,
                    payload=post,
                    credentials=creds,
                )
                if max_message_id and payload.target_platform != "rss":
                    job_store.update_max_message_id(
                        payload.source_channel,
                        source_post_id,
                        payload.target_channel,
                        max_message_id,
                    )
                session.commit()
                inc_live_publish_ok()
                return {"status": "ok", "source_post_id": source_post_id}
            except Exception:
                job_store.release_claim(
                    payload.source_channel, source_post_id, payload.target_channel
                )
                session.rollback()
                inc_live_publish_failed()
                raise
        logger.warning("edit-single %s API error: %s", payload.target_platform, detail_msg)
        raise HTTPException(status_code=502, detail=detail_msg) from e

    return {"status": "ok", "source_post_id": source_post_id}


@router.post("/internal/sync/delete-single", include_in_schema=False)
def delete_single(
    payload: DeleteSingleRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Удаляет пост в MAX по source_post_id (требует сохранённый max_message_id)."""
    check_sync_publish_auth(request)
    job_store = BatchImportRunStore(session)
    row = job_store.get_published_post(
        payload.source_channel, payload.source_post_id, payload.target_channel
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="error.live_sync.post_not_found",
        )
    if not row.max_message_id:
        raise HTTPException(
            status_code=409,
            detail="error.live_sync.post_missing_tracking_for_delete",
        )

    tgt_ch = session.get(ChannelOrm, payload.target_core_channel_id)
    if tgt_ch is None or tgt_ch.tenant_id != payload.tenant_id:
        raise HTTPException(
            status_code=404,
            detail="error.live_sync.target_channel_not_found",
        )
    if tgt_ch.platform != payload.target_platform:
        raise HTTPException(
            status_code=422,
            detail="error.live_sync.target_platform_mismatch",
        )
    caps = get_platform_capabilities(payload.target_platform)
    if caps is not None and not caps.live_sync_publish_supported:
        raise HTTPException(
            status_code=501,
            detail="error.live_sync.delete_not_supported",
        )
    cred_row = load_channel_credential_row(
        session, payload.target_core_channel_id, payload.tenant_id
    )
    creds = decode_publish_credentials_for_platform(tgt_ch.platform, cred_row)

    publisher = get_publisher(payload.target_platform)
    delete_fn = getattr(publisher, "delete_message", None)
    if not delete_fn:
        raise HTTPException(
            status_code=501,
            detail="error.live_sync.delete_not_supported_for_platform",
        )
    try:
        kwargs: dict = {"message_id": row.max_message_id}
        if "credentials" in inspect.signature(delete_fn).parameters:
            kwargs["credentials"] = creds
        delete_fn(**kwargs)
    except ExternalApiError as e:
        logger.warning("delete-single MAX API error: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e

    return {"status": "ok", "source_post_id": payload.source_post_id}


@router.post("/internal/fetch-posts", include_in_schema=False)
async def fetch_posts(
    payload: FetchPostsRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> FetchPostsResponse:
    """Забирает посты из источника (Telegram, MAX, VK, RSS)."""
    check_sync_publish_auth(request)
    src_ch = session.get(ChannelOrm, payload.source_core_channel_id)
    if src_ch is None or src_ch.tenant_id != payload.tenant_id:
        raise HTTPException(
            status_code=404,
            detail="error.live_sync.source_channel_not_found",
        )
    if src_ch.platform != payload.source_platform:
        raise HTTPException(
            status_code=422,
            detail="error.live_sync.source_platform_mismatch",
        )
    try:
        creds = resolve_fetch_credentials_for_core_channel(
            session,
            tenant_id=payload.tenant_id,
            source_core_channel_id=payload.source_core_channel_id,
            source_platform=payload.source_platform,
        )
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.message) from e
    fetcher = get_fetcher(payload.source_platform)
    posts = await fetcher.fetch_posts(
        source_channel=payload.source_channel,
        limit=payload.limit,
        credentials=creds,
        tenant_id=payload.tenant_id,
    )
    return FetchPostsResponse(
        posts=[
            PostItem(
                source_post_id=p.source_post_id,
                text=p.text,
                media_url=p.media_url,
                media_urls=p.media_urls,
            )
            for p in posts
        ]
    )


class RssFeedItemResponse(BaseModel):
    """Один пост в ответе rss feed items."""

    source_post_id: str
    text: str
    media_url: str | None = None
    media_urls: list[str] | None = None
    published_at: str


@router.get("/internal/rss/{feed_id}/items", include_in_schema=False)
def get_rss_feed_items(
    feed_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> dict:
    """Возвращает кэшированные посты RSS-ленты (для SaaS generate_rss_xml)."""
    check_sync_publish_auth(request)
    rows = list(
        session.scalars(
            select(RssFeedItemOrm)
            .where(RssFeedItemOrm.feed_id == feed_id)
            .order_by(RssFeedItemOrm.published_at.desc())
            .limit(100)
        ).all()
    )
    items = []
    for r in rows:
        media_urls = None
        if r.media_urls_json:
            try:
                media_urls = json.loads(r.media_urls_json)
            except (json.JSONDecodeError, TypeError):
                pass
        items.append(
            RssFeedItemResponse(
                source_post_id=r.source_post_id,
                text=r.text or "",
                media_url=r.media_url,
                media_urls=media_urls,
                published_at=r.published_at.isoformat() if r.published_at else "",
            )
        )
    return {"items": [i.model_dump() for i in items]}


@router.get("/internal/rss-feeds/meta/{feed_id}", include_in_schema=False)
def get_rss_feed_meta_by_secret(
    feed_id: str,
    request: Request,
    secret_token: str = Query(..., min_length=1, max_length=128),
    session: Session = Depends(get_db_session),
) -> dict:
    """Метаданные ленты по id+secret (SaaS публичный RSS без X-Tenant-Id)."""
    check_sync_publish_auth(request)
    row = session.scalar(
        select(RssFeedOrm).where(
            RssFeedOrm.id == feed_id,
            RssFeedOrm.secret_token == secret_token,
        )
    )
    if row is None:
        raise ValidationError(
            code="VALIDATION_RSS_FEED_NOT_FOUND",
            message="rss feed not found",
            message_key="error.validation.rss_feed_not_found",
            details={"feed_id": feed_id},
        )
    return {
        "tenant_id": row.tenant_id,
        "source_channel_id": row.source_channel_id,
        "saas_user_id": row.saas_user_id,
    }


class EnqueueLiveSyncPublishRequest(BaseModel):
    """Постановка задачи Celery: публикация live-sync (вызов от SaaS BFF)."""

    source_channel: str
    target_channel: str
    post: dict
    workspace_id: str
    target_platform: str = "max"
    core_tenant_id: str = Field(min_length=36, max_length=36)
    target_core_channel_id: str = Field(min_length=36, max_length=36)
    producer: str | None = "saas_http"


class EnqueueLiveSyncEditRequest(BaseModel):
    """Постановка задачи Celery: редактирование live-sync (вызов от SaaS BFF)."""

    source_channel: str
    target_channel: str
    post: dict
    workspace_id: str = ""
    target_platform: str = "max"
    core_tenant_id: str = Field(min_length=36, max_length=36)
    target_core_channel_id: str = Field(min_length=36, max_length=36)
    producer: str | None = "saas_http"


@router.post("/internal/sync/enqueue-live-publish", include_in_schema=False)
def enqueue_live_publish(
    payload: EnqueueLiveSyncPublishRequest,
    request: Request,
) -> dict[str, str]:
    check_sync_publish_auth(request)
    from postbridge.services.live_sync_queue import queue_live_sync_publish

    queue_live_sync_publish(
        source_channel=payload.source_channel,
        target_channel=payload.target_channel,
        post=payload.post,
        workspace_id=payload.workspace_id,
        target_platform=payload.target_platform,
        core_tenant_id=payload.core_tenant_id,
        target_core_channel_id=payload.target_core_channel_id,
        producer=payload.producer,
    )
    return {"status": "queued"}


@router.post("/internal/sync/enqueue-live-edit", include_in_schema=False)
def enqueue_live_edit(
    payload: EnqueueLiveSyncEditRequest,
    request: Request,
) -> dict[str, str]:
    check_sync_publish_auth(request)
    from postbridge.services.live_sync_queue import queue_live_sync_edit

    queue_live_sync_edit(
        source_channel=payload.source_channel,
        target_channel=payload.target_channel,
        post=payload.post,
        target_platform=payload.target_platform,
        workspace_id=payload.workspace_id,
        core_tenant_id=payload.core_tenant_id,
        target_core_channel_id=payload.target_core_channel_id,
        producer=payload.producer,
    )
    return {"status": "queued"}
