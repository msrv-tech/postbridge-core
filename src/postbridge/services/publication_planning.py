"""Создание цепочки content_item → publication_plan → publication_targets (фаза 1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from postbridge.models.domain import (
    ChannelOrm,
    ContentItemOrm,
    PublicationPlanOrm,
    PublicationTargetOrm,
    RenderVariantOrm,
)


@dataclass(slots=True)
class PublicationChainResult:
    content_item_id: str
    publication_plan_id: str
    render_variant_ids: list[str]
    publication_target_ids: list[str]


def _preview_body(title: str | None, body_markdown: str | None) -> str:
    if body_markdown and body_markdown.strip():
        return body_markdown.strip()[:8000]
    if title:
        return title.strip()
    return ""


def create_content_with_plan_and_targets(
    session: Session,
    *,
    tenant_id: str,
    channel_ids: Sequence[str],
    author_user_id: str | None = None,
    source_type: str = "manual",
    title: str | None = None,
    body_markdown: str | None = None,
    body_structured_json: str | None = None,
    language: str | None = None,
    content_status: str = "draft",
    media_url: str | None = None,
    media_urls: list[str] | None = None,
    plan_strategy: str = "immediate",
    plan_status: str = "draft",
    target_status: str = "pending",
) -> PublicationChainResult:
    """
    Создаёт контент, по одному render_variant на канал, план и target на каждый канал.
    Все строки получают один и тот же tenant_id; каналы должны существовать и принадлежать tenant.
    """
    ids = list(channel_ids)
    if not ids:
        raise ValueError("channel_ids must not be empty")

    stmt = select(ChannelOrm).where(ChannelOrm.tenant_id == tenant_id, ChannelOrm.id.in_(ids))
    channels = list(session.scalars(stmt).all())
    if len(channels) != len(ids):
        raise ValueError("one or more channels are missing or belong to another tenant")

    by_id = {c.id: c for c in channels}
    ordered = [by_id[i] for i in ids]

    content_id = str(uuid4())
    plan_id = str(uuid4())
    preview = _preview_body(title, body_markdown)

    content = ContentItemOrm(
        id=content_id,
        tenant_id=tenant_id,
        author_user_id=author_user_id,
        source_type=source_type,
        title=title,
        body_markdown=body_markdown,
        body_structured_json=body_structured_json,
        language=language,
        status=content_status,
        media_url=media_url,
        media_urls=media_urls,
    )
    session.add(content)

    render_ids: list[str] = []
    for ch in ordered:
        rv_id = str(uuid4())
        render_ids.append(rv_id)
        session.add(
            RenderVariantOrm(
                id=rv_id,
                tenant_id=tenant_id,
                content_item_id=content_id,
                channel_id=ch.id,
                platform=ch.platform,
                language=language,
                title=title,
                body_text=preview or None,
                created_by="system",
                version=1,
            )
        )

    plan = PublicationPlanOrm(
        id=plan_id,
        tenant_id=tenant_id,
        content_item_id=content_id,
        strategy=plan_strategy,
        publish_at=None,
        timezone=None,
        status=plan_status,
    )
    session.add(plan)
    # PostgreSQL: publication_targets FK → render_variants — сначала flush цепочку до targets.
    session.flush()

    target_ids: list[str] = []
    for ch, rv_id in zip(ordered, render_ids, strict=True):
        tid = str(uuid4())
        target_ids.append(tid)
        session.add(
            PublicationTargetOrm(
                id=tid,
                tenant_id=tenant_id,
                publication_plan_id=plan_id,
                channel_id=ch.id,
                platform=ch.platform,
                render_variant_id=rv_id,
                status=target_status,
                retry_count=0,
            )
        )

    session.flush()

    assert content.tenant_id == tenant_id
    assert plan.tenant_id == tenant_id
    for ch in ordered:
        assert ch.tenant_id == tenant_id

    return PublicationChainResult(
        content_item_id=content_id,
        publication_plan_id=plan_id,
        render_variant_ids=render_ids,
        publication_target_ids=target_ids,
    )


def create_plan_and_targets_for_content_item(
    session: Session,
    *,
    tenant_id: str,
    content_item: ContentItemOrm,
    channel_ids: Sequence[str],
    plan_strategy: str = "immediate",
    plan_status: str = "scheduled",
    target_status: str = "pending",
    scheduled_at: datetime | None = None,
) -> PublicationChainResult:
    """Create a publication plan and targets for an existing content item."""
    if content_item.tenant_id != tenant_id:
        raise ValueError("content item belongs to another tenant")

    ids = list(channel_ids)
    if not ids:
        raise ValueError("channel_ids must not be empty")

    stmt = select(ChannelOrm).where(ChannelOrm.tenant_id == tenant_id, ChannelOrm.id.in_(ids))
    channels = list(session.scalars(stmt).all())
    if len(channels) != len(ids):
        raise ValueError("one or more channels are missing or belong to another tenant")

    by_id = {c.id: c for c in channels}
    ordered = [by_id[i] for i in ids]

    plan_id = str(uuid4())
    preview = _preview_body(content_item.title, content_item.body_markdown)

    render_ids: list[str] = []
    for ch in ordered:
        rv_id = str(uuid4())
        render_ids.append(rv_id)
        session.add(
            RenderVariantOrm(
                id=rv_id,
                tenant_id=tenant_id,
                content_item_id=content_item.id,
                channel_id=ch.id,
                platform=ch.platform,
                language=content_item.language,
                title=content_item.title,
                body_text=preview or None,
                created_by="system",
                version=1,
            )
        )

    plan = PublicationPlanOrm(
        id=plan_id,
        tenant_id=tenant_id,
        content_item_id=content_item.id,
        strategy=plan_strategy,
        publish_at=scheduled_at,
        timezone="UTC" if scheduled_at is not None else None,
        status=plan_status,
    )
    session.add(plan)
    session.flush()

    target_ids: list[str] = []
    for ch, rv_id in zip(ordered, render_ids, strict=True):
        tid = str(uuid4())
        target_ids.append(tid)
        session.add(
            PublicationTargetOrm(
                id=tid,
                tenant_id=tenant_id,
                publication_plan_id=plan_id,
                channel_id=ch.id,
                platform=ch.platform,
                render_variant_id=rv_id,
                status=target_status,
                scheduled_at=scheduled_at,
                retry_count=0,
            )
        )

    session.flush()
    return PublicationChainResult(
        content_item_id=content_item.id,
        publication_plan_id=plan_id,
        render_variant_ids=render_ids,
        publication_target_ids=target_ids,
    )
