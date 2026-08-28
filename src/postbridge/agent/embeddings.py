from __future__ import annotations

import json
import logging

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from postbridge.agent.providers.openai_compatible import OpenAICompatibleProvider, ensure_openai_compatible_provider
from postbridge.agent.tools import (
    find_default_provider,
    find_similar_embeddings,
    fingerprint_text,
    jaccard_similarity,
    upsert_content_embedding,
)
from postbridge.agent.vector_store import get_vector_store
from postbridge.domain.errors import ValidationError
from postbridge.models.domain import (
    ChannelOrm,
    ContentCandidateOrm,
    ContentEmbeddingOrm,
    ContentItemOrm,
    PublicationPlanOrm,
    PublicationTargetOrm,
)


logger = logging.getLogger(__name__)


def search_content_knowledge(
    session: Session,
    *,
    tenant_id: str,
    query: str,
    channel_ids: list[str] | None = None,
    limit: int = 8,
    semantic_enabled: bool = True,
) -> dict[str, object]:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValidationError(
            code="VALIDATION_QUERY_REQUIRED",
            message="query is required",
            details={},
        )
    result_limit = max(1, min(20, int(limit or 8)))
    channels = _channels_for_tenant(
        session,
        tenant_id=tenant_id,
        channel_id=None,
        limit=500,
    )
    available_channel_ids = {row.id for row in channels}
    selected_channel_ids = list(
        dict.fromkeys(
            sorted(available_channel_ids) if channel_ids is None else channel_ids
        )
    )
    missing = sorted(set(selected_channel_ids) - available_channel_ids)
    if missing:
        raise ValidationError(
            code="VALIDATION_CHANNEL_NOT_FOUND",
            message="channel not found",
            details={"channel_ids": missing},
        )

    semantic_matches: dict[str, dict[str, object]] = {}
    token_usage: dict[str, object] = {}
    semantic_available = False
    if semantic_enabled and selected_channel_ids:
        try:
            provider, _embedding_model = _resolve_embedding_provider(
                session, tenant_id=tenant_id
            )
            vector, raw_usage = provider.invoke_embedding(text=normalized_query)
            token_usage = dict(raw_usage) if isinstance(raw_usage, dict) else {}
            semantic_available = True
            per_channel_limit = max(result_limit * 3, 20)
            for channel_id in selected_channel_ids:
                for match in find_similar_embeddings(
                    session,
                    tenant_id=tenant_id,
                    channel_id=channel_id,
                    vector=vector,
                    entity_type="content_item",
                    top_k=per_channel_limit,
                ):
                    entity_id = str(match.get("entity_id") or "")
                    if not entity_id:
                        continue
                    score = float(match.get("score") or 0.0)
                    current = semantic_matches.get(entity_id)
                    if current is None or score > float(current["score"]):
                        semantic_matches[entity_id] = {
                            "score": score,
                            "channel_id": channel_id,
                        }
        except Exception as exc:
            logger.warning(
                "Semantic knowledge search unavailable tenant_id=%s error=%s",
                tenant_id,
                type(exc).__name__,
            )

    embedding_corpus_rows = session.execute(
        select(ContentEmbeddingOrm.entity_id, ContentEmbeddingOrm.channel_id).where(
            ContentEmbeddingOrm.tenant_id == tenant_id,
            ContentEmbeddingOrm.entity_type == "content_item",
            ContentEmbeddingOrm.channel_id.in_(selected_channel_ids),
        )
    ).all()
    publication_corpus_rows = session.execute(
        select(
            PublicationPlanOrm.content_item_id,
            PublicationTargetOrm.channel_id,
        )
        .join(
            PublicationTargetOrm,
            PublicationTargetOrm.publication_plan_id == PublicationPlanOrm.id,
        )
        .where(
            PublicationPlanOrm.tenant_id == tenant_id,
            PublicationTargetOrm.channel_id.in_(selected_channel_ids),
        )
    ).all()
    corpus_channel_by_entity: dict[str, str] = {}
    for entity_id, channel_id in [*embedding_corpus_rows, *publication_corpus_rows]:
        if entity_id not in corpus_channel_by_entity:
            corpus_channel_by_entity[str(entity_id)] = str(channel_id)
    corpus_ids = set(corpus_channel_by_entity)
    rows = (
        list(
            session.scalars(
                select(ContentItemOrm)
                .where(
                    ContentItemOrm.tenant_id == tenant_id,
                    ContentItemOrm.id.in_(corpus_ids),
                )
                .order_by(ContentItemOrm.updated_at.desc())
                .limit(500)
            ).all()
        )
        if corpus_ids
        else []
    )
    query_fingerprint = fingerprint_text(normalized_query)
    ranked: list[dict[str, object]] = []
    for row in rows:
        text = _content_embedding_text(row)
        keyword_score = jaccard_similarity(
            query_fingerprint, fingerprint_text(text)
        )
        semantic = semantic_matches.get(row.id)
        semantic_score = float(semantic["score"]) if semantic else 0.0
        score = max(semantic_score, keyword_score)
        if score <= 0:
            continue
        match_type = (
            "hybrid"
            if semantic_score > 0 and keyword_score > 0
            else ("semantic" if semantic_score > 0 else "keyword")
        )
        ranked.append(
            {
                "content_item_id": row.id,
                "channel_id": (
                    semantic.get("channel_id")
                    if semantic
                    else corpus_channel_by_entity.get(row.id)
                ),
                "status": row.status,
                "title": row.title,
                "snippet": " ".join((row.body_markdown or "").split())[:1600],
                "score": round(score, 6),
                "semantic_score": round(semantic_score, 6),
                "keyword_score": round(keyword_score, 6),
                "match_type": match_type,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        )
    ranked.sort(
        key=lambda item: (float(item["score"]), str(item.get("updated_at") or "")),
        reverse=True,
    )
    items = ranked[:result_limit]
    has_semantic = any(float(item["semantic_score"]) > 0 for item in items)
    has_keyword = any(float(item["keyword_score"]) > 0 for item in items)
    retrieval_mode = (
        "hybrid"
        if has_semantic and has_keyword
        else ("semantic" if has_semantic else "keyword")
    )
    return {
        "query": normalized_query,
        "retrieval_mode": retrieval_mode,
        "semantic_available": semantic_available,
        "channel_ids": selected_channel_ids,
        "items": items,
        "token_usage": token_usage,
    }
def reindex_channel_content_embeddings(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, int | str | bool | float]:
    _require_channel(session, tenant_id=tenant_id, channel_id=channel_id)
    provider, embedding_model = _resolve_embedding_provider(session, tenant_id=tenant_id)
    lifecycle_before = _channel_embedding_lifecycle_stats(
        session,
        tenant_id=tenant_id,
        channel_id=channel_id,
        embedding_model=embedding_model,
    )
    indexed = 0
    contents = _content_items_for_tenant(session, tenant_id=tenant_id, limit=limit, offset=offset)
    for content in contents:
        text = _content_embedding_text(content)
        if not text:
            continue
        vector, _ = provider.invoke_embedding(text=text)
        upsert_content_embedding(
            session,
            tenant_id=tenant_id,
            channel_id=channel_id,
            entity_type="content_item",
            entity_id=content.id,
            model_name=embedding_model,
            vector=vector,
            text_hash=fingerprint_text(text),
        )
        indexed += 1
    session.flush()
    return _embedding_operation_result(
        session,
        tenant_id=tenant_id,
        channel_id=channel_id,
        embedding_model=embedding_model,
        lifecycle_before=lifecycle_before,
        indexed=indexed,
        offset=offset,
        scanned_count=len(contents),
        has_more=len(contents) >= limit,
    )


def reindex_content_item_embedding(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    content_item_id: str,
) -> dict[str, int | str | bool | float]:
    content = session.get(ContentItemOrm, content_item_id)
    if content is None or content.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_CONTENT_ITEM_NOT_FOUND",
            message="content item not found",
            details={"content_item_id": content_item_id},
        )
    _require_channel(session, tenant_id=tenant_id, channel_id=channel_id)
    provider, embedding_model = _resolve_embedding_provider(session, tenant_id=tenant_id)
    lifecycle_before = _channel_embedding_lifecycle_stats(
        session,
        tenant_id=tenant_id,
        channel_id=channel_id,
        embedding_model=embedding_model,
    )
    text = _content_embedding_text(content)
    if not text:
        return {
            **_embedding_operation_result(
                session,
                tenant_id=tenant_id,
                channel_id=channel_id,
                embedding_model=embedding_model,
                lifecycle_before=lifecycle_before,
                indexed=0,
            ),
            "content_item_id": content_item_id,
        }
    vector, _ = provider.invoke_embedding(text=text)
    upsert_content_embedding(
        session,
        tenant_id=tenant_id,
        channel_id=channel_id,
        entity_type="content_item",
        entity_id=content.id,
        model_name=embedding_model,
        vector=vector,
        text_hash=fingerprint_text(text),
    )
    session.flush()
    return {
        **_embedding_operation_result(
            session,
            tenant_id=tenant_id,
            channel_id=channel_id,
            embedding_model=embedding_model,
            lifecycle_before=lifecycle_before,
            indexed=1,
        ),
        "content_item_id": content_item_id,
    }


def rotate_channel_content_embeddings(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, int | str | bool | float]:
    _require_channel(session, tenant_id=tenant_id, channel_id=channel_id)
    provider, embedding_model = _resolve_embedding_provider(session, tenant_id=tenant_id)
    lifecycle_before = _channel_embedding_lifecycle_stats(
        session,
        tenant_id=tenant_id,
        channel_id=channel_id,
        embedding_model=embedding_model,
    )
    rotated = 0
    items = _content_embeddings_join(session, tenant_id=tenant_id, limit=limit, offset=offset)
    for content, embedding in items:
        text = _content_embedding_text(content)
        if not text:
            continue
        text_hash = fingerprint_text(text)
        if embedding is not None and embedding.model_name == embedding_model and embedding.text_hash == text_hash:
            continue
        vector, _ = provider.invoke_embedding(text=text)
        upsert_content_embedding(
            session,
            tenant_id=tenant_id,
            channel_id=channel_id,
            entity_type="content_item",
            entity_id=content.id,
            model_name=embedding_model,
            vector=vector,
            text_hash=text_hash,
        )
        rotated += 1
    deleted_orphans = _delete_orphan_embeddings(session, tenant_id=tenant_id, channel_id=channel_id)
    session.flush()
    return {
        **_embedding_operation_result(
            session,
            tenant_id=tenant_id,
            channel_id=channel_id,
            embedding_model=embedding_model,
            lifecycle_before=lifecycle_before,
            indexed=rotated,
            offset=offset,
            scanned_count=len(items),
            has_more=len(items) >= limit,
        ),
        "channel_id": channel_id,
        "rotated": rotated,
        "deleted_orphan_embeddings": deleted_orphans,
        "compaction_policy": "keep_latest_per_entity",
    }


def get_embedding_lifecycle_overview(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str | None = None,
    limit_channels: int = 100,
    offset_channels: int = 0,
) -> dict[str, object]:
    _, embedding_model = _resolve_embedding_provider(session, tenant_id=tenant_id)
    vector_stats = get_vector_store(session).stats(tenant_id=tenant_id, channel_id=channel_id)
    channels = _channels_for_tenant(
        session,
        tenant_id=tenant_id,
        channel_id=channel_id,
        limit=limit_channels,
        offset=offset_channels,
    )
    rows: list[dict[str, object]] = []
    totals = {
        "content_items_total": 0,
        "stored_embeddings": 0,
        "missing_embeddings": 0,
        "stale_embeddings": 0,
        "stale_model_embeddings": 0,
        "stale_text_embeddings": 0,
    }
    for channel in channels:
        stats = _channel_embedding_lifecycle_stats(
            session,
            tenant_id=tenant_id,
            channel_id=channel.id,
            embedding_model=embedding_model,
        )
        stored_embeddings = int(stats["stored_embeddings"])
        for key in totals:
            totals[key] += int(stats[key])
        rows.append(
            {
                "channel_id": channel.id,
                "channel_title": channel.title,
                "target_embedding_model": embedding_model,
                **stats,
                "coverage": _coverage_ratio(
                    stored_embeddings,
                    int(stats["content_items_total"]),
                ),
            }
        )
    return {
        "tenant_id": tenant_id,
        "channel_id": channel_id,
        "channel_offset": offset_channels,
        "channel_limit": limit_channels,
        "has_more_channels": bool(channel_id is None and len(channels) >= limit_channels),
        "next_channel_offset": (offset_channels + len(channels)) if channel_id is None and len(channels) >= limit_channels else None,
        "target_embedding_model": embedding_model,
        "vector_backend": vector_stats["backend"],
        "pgvector_native": vector_stats["native"],
        "channels": rows,
        **totals,
        "coverage": _coverage_ratio(
            totals["stored_embeddings"],
            totals["content_items_total"],
        ),
    }


def reindex_embedding_drift(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str | None = None,
    channel_limit: int = 20,
    item_limit: int = 100,
    channel_offset: int = 0,
) -> dict[str, object]:
    overview = get_embedding_lifecycle_overview(
        session,
        tenant_id=tenant_id,
        channel_id=channel_id,
        limit_channels=channel_limit,
        offset_channels=channel_offset,
    )
    processed_channels: list[dict[str, object]] = []
    rotated_total = 0
    deleted_orphans_total = 0
    for item in overview["channels"]:
        channel_row = item if isinstance(item, dict) else {}
        stale_embeddings = int(channel_row.get("stale_embeddings", 0))
        missing_embeddings = int(channel_row.get("missing_embeddings", 0))
        if stale_embeddings <= 0 and missing_embeddings <= 0:
            continue
        result = rotate_channel_content_embeddings(
            session,
            tenant_id=tenant_id,
            channel_id=str(channel_row["channel_id"]),
            limit=item_limit,
            offset=0,
        )
        rotated_total += int(result.get("rotated", 0))
        deleted_orphans_total += int(result.get("deleted_orphan_embeddings", 0))
        processed_channels.append(
            {
                "channel_id": result["channel_id"],
                "rotated": result["rotated"],
                "deleted_orphan_embeddings": result["deleted_orphan_embeddings"],
                "coverage_after": result["coverage_after"],
                "target_embedding_model": result["embedding_model"],
            }
        )
    refreshed = get_embedding_lifecycle_overview(
        session,
        tenant_id=tenant_id,
        channel_id=channel_id,
        limit_channels=channel_limit,
        offset_channels=channel_offset,
    )
    return {
        "tenant_id": tenant_id,
        "channel_id": channel_id,
        "channel_offset": channel_offset,
        "channel_limit": channel_limit,
        "channels_scanned": len(overview["channels"]) if isinstance(overview["channels"], list) else 0,
        "channels_reindexed": len(processed_channels),
        "has_more_channels": bool(overview.get("has_more_channels")),
        "next_channel_offset": overview.get("next_channel_offset"),
        "item_limit": item_limit,
        "target_embedding_model": refreshed["target_embedding_model"],
        "rotated_embeddings": rotated_total,
        "deleted_orphan_embeddings": deleted_orphans_total,
        "before": {
            "content_items_total": overview["content_items_total"],
            "missing_embeddings": overview["missing_embeddings"],
            "stale_embeddings": overview["stale_embeddings"],
            "stale_model_embeddings": overview["stale_model_embeddings"],
            "stale_text_embeddings": overview["stale_text_embeddings"],
            "coverage": overview["coverage"],
        },
        "after": {
            "content_items_total": refreshed["content_items_total"],
            "missing_embeddings": refreshed["missing_embeddings"],
            "stale_embeddings": refreshed["stale_embeddings"],
            "stale_model_embeddings": refreshed["stale_model_embeddings"],
            "stale_text_embeddings": refreshed["stale_text_embeddings"],
            "coverage": refreshed["coverage"],
        },
        "channels": processed_channels,
    }


def maintain_embeddings(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str | None = None,
    prune_orphans: bool = True,
    prune_malformed: bool = True,
    optimize_native: bool = True,
    limit: int | None = None,
    offset: int = 0,
    after_id: str | None = None,
) -> dict[str, object]:
    if channel_id is not None:
        _require_channel(session, tenant_id=tenant_id, channel_id=channel_id)
    stmt = select(ContentEmbeddingOrm).where(ContentEmbeddingOrm.tenant_id == tenant_id)
    if channel_id is not None:
        stmt = stmt.where(ContentEmbeddingOrm.channel_id == channel_id)
    stmt = stmt.order_by(ContentEmbeddingOrm.created_at.asc(), ContentEmbeddingOrm.id.asc())
    if after_id:
        anchor = session.get(ContentEmbeddingOrm, after_id)
        if anchor is not None:
            stmt = stmt.where(
                or_(
                    ContentEmbeddingOrm.created_at > anchor.created_at,
                    and_(
                        ContentEmbeddingOrm.created_at == anchor.created_at,
                        ContentEmbeddingOrm.id > anchor.id,
                    ),
                )
            )
    elif limit is not None:
        stmt = stmt.limit(limit).offset(offset)
    if limit is not None and after_id:
        stmt = stmt.limit(limit)
    rows = list(session.scalars(stmt).all())
    existing_content_ids = set(session.scalars(select(ContentItemOrm.id).where(ContentItemOrm.tenant_id == tenant_id)).all())
    existing_candidate_ids = set(session.scalars(select(ContentCandidateOrm.id).where(ContentCandidateOrm.tenant_id == tenant_id)).all())
    deleted_orphans = 0
    deleted_malformed = 0
    for row in rows:
        if prune_malformed and not _is_valid_embedding_payload(row.vector_json):
            session.delete(row)
            deleted_malformed += 1
            continue
        if prune_orphans:
            if row.entity_type == "content_item" and row.entity_id not in existing_content_ids:
                session.delete(row)
                deleted_orphans += 1
                continue
            if row.entity_type == "candidate" and row.entity_id not in existing_candidate_ids:
                session.delete(row)
                deleted_orphans += 1
                continue
    session.flush()
    effective_channel = channel_id or _first_channel_id_for_tenant(session, tenant_id=tenant_id)
    if effective_channel:
        vector_result = get_vector_store(session).maintain(
            tenant_id=tenant_id,
            channel_id=effective_channel,
            optimize_native=optimize_native,
        )
    else:
        vector_result = {
            "backend": "pgvector",
            "native": False,
            "optimized_native_index": False,
            "native_index_available": False,
            "stored_embeddings": 0,
            "content_item_embeddings": 0,
            "candidate_embeddings": 0,
            "model_counts": {},
        }
    return {
        "tenant_id": tenant_id,
        "channel_id": channel_id,
        "offset": offset,
        "after_id": after_id,
        "row_limit": limit,
        "processed_rows": len(rows),
        "has_more": bool(limit is not None and len(rows) >= limit),
        "next_offset": (offset + len(rows)) if limit is not None and len(rows) >= limit else None,
        "next_after_id": rows[-1].id if limit is not None and len(rows) >= limit and rows else None,
        "deleted_orphan_embeddings": deleted_orphans,
        "deleted_malformed_embeddings": deleted_malformed,
        "prune_orphans": prune_orphans,
        "prune_malformed": prune_malformed,
        "optimize_native": optimize_native,
        **vector_result,
    }


def compact_embeddings(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str | None = None,
    candidate_retention_days: int,
    optimize_native: bool = True,
) -> dict[str, object]:
    if channel_id is not None:
        _require_channel(session, tenant_id=tenant_id, channel_id=channel_id)
    cutoff = datetime.now(UTC) - timedelta(days=max(candidate_retention_days, 1))
    candidate_stmt = select(ContentCandidateOrm).where(
        ContentCandidateOrm.tenant_id == tenant_id,
        ContentCandidateOrm.created_at < cutoff,
        ContentCandidateOrm.status.in_(("approved", "rejected", "converted", "superseded")),
    )
    if channel_id is not None:
        candidate_stmt = candidate_stmt.where(ContentCandidateOrm.channel_id == channel_id)
    compactable_candidates = list(session.scalars(candidate_stmt).all())
    candidate_ids = [row.id for row in compactable_candidates]
    deleted_candidate_embeddings = 0
    if candidate_ids:
        delete_stmt = select(ContentEmbeddingOrm).where(
            ContentEmbeddingOrm.tenant_id == tenant_id,
            ContentEmbeddingOrm.entity_type == "candidate",
            ContentEmbeddingOrm.entity_id.in_(candidate_ids),
        )
        if channel_id is not None:
            delete_stmt = delete_stmt.where(ContentEmbeddingOrm.channel_id == channel_id)
        for row in session.scalars(delete_stmt).all():
            session.delete(row)
            deleted_candidate_embeddings += 1
    session.flush()
    effective_channel = channel_id or _first_channel_id_for_tenant(session, tenant_id=tenant_id)
    if effective_channel:
        vector_result = get_vector_store(session).maintain(
            tenant_id=tenant_id,
            channel_id=effective_channel,
            optimize_native=optimize_native,
        )
    else:
        vector_result = {
            "backend": "pgvector",
            "native": False,
            "optimized_native_index": False,
            "native_index_available": False,
            "stored_embeddings": 0,
            "content_item_embeddings": 0,
            "candidate_embeddings": 0,
            "model_counts": {},
        }
    return {
        "tenant_id": tenant_id,
        "channel_id": channel_id,
        "candidate_retention_days": candidate_retention_days,
        "cutoff": cutoff,
        "compactable_candidate_count": len(compactable_candidates),
        "deleted_candidate_embeddings": deleted_candidate_embeddings,
        "optimize_native": optimize_native,
        **vector_result,
    }


def _require_channel(session: Session, *, tenant_id: str, channel_id: str) -> ChannelOrm:
    channel = session.get(ChannelOrm, channel_id)
    if channel is None or channel.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_CHANNEL_NOT_FOUND",
            message="channel not found",
            details={"channel_id": channel_id},
        )
    return channel


def _resolve_embedding_provider(session: Session, *, tenant_id: str) -> tuple[OpenAICompatibleProvider, str]:
    provider = ensure_openai_compatible_provider(find_default_provider(session, tenant_id=tenant_id))
    embedding_model = provider.embedding_model_name or provider.model_name
    return provider, embedding_model


def _channels_for_tenant(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str | None,
    limit: int,
    offset: int = 0,
) -> list[ChannelOrm]:
    stmt = select(ChannelOrm).where(ChannelOrm.tenant_id == tenant_id)
    if channel_id is not None:
        stmt = stmt.where(ChannelOrm.id == channel_id)
    stmt = stmt.order_by(ChannelOrm.created_at.asc()).limit(limit).offset(offset)
    return list(session.scalars(stmt).all())


def _first_channel_id_for_tenant(session: Session, *, tenant_id: str) -> str | None:
    return session.scalar(select(ChannelOrm.id).where(ChannelOrm.tenant_id == tenant_id).order_by(ChannelOrm.created_at.asc()).limit(1))


def _content_items_for_tenant(session: Session, *, tenant_id: str, limit: int, offset: int = 0) -> list[ContentItemOrm]:
    return list(
        session.scalars(
            select(ContentItemOrm)
            .where(ContentItemOrm.tenant_id == tenant_id)
            .order_by(ContentItemOrm.updated_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )


def _content_embeddings_join(
    session,
    *,
    tenant_id: str,
    limit: int,
    offset: int = 0,
) -> list[tuple[ContentItemOrm, ContentEmbeddingOrm | None]]:
    stmt = (
        select(ContentItemOrm, ContentEmbeddingOrm)
        .outerjoin(
            ContentEmbeddingOrm,
            (ContentEmbeddingOrm.entity_type == "content_item") & (ContentEmbeddingOrm.entity_id == ContentItemOrm.id),
        )
        .where(ContentItemOrm.tenant_id == tenant_id)
        .order_by(ContentItemOrm.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.execute(stmt).all())


def _delete_orphan_embeddings(session: Session, *, tenant_id: str, channel_id: str) -> int:
    orphan_embeddings = list(
        session.scalars(
            select(ContentEmbeddingOrm).where(
                ContentEmbeddingOrm.tenant_id == tenant_id,
                ContentEmbeddingOrm.channel_id == channel_id,
            )
        ).all()
    )
    deleted_orphans = 0
    content_ids = set(session.scalars(select(ContentItemOrm.id).where(ContentItemOrm.tenant_id == tenant_id)).all())
    candidate_ids = set(session.scalars(select(ContentCandidateOrm.id).where(ContentCandidateOrm.tenant_id == tenant_id)).all())
    for row in orphan_embeddings:
        if row.entity_type == "content_item" and row.entity_id not in content_ids:
            session.delete(row)
            deleted_orphans += 1
        elif row.entity_type == "candidate" and row.entity_id not in candidate_ids:
            session.delete(row)
            deleted_orphans += 1
    return deleted_orphans


def _embedding_operation_result(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    embedding_model: str,
    lifecycle_before: dict[str, int],
    indexed: int,
    offset: int = 0,
    scanned_count: int = 0,
    has_more: bool = False,
) -> dict[str, int | str | bool | float]:
    stats = get_vector_store(session).stats(tenant_id=tenant_id, channel_id=channel_id)
    return {
        "indexed": indexed,
        "offset": offset,
        "scanned_count": scanned_count,
        "has_more": has_more,
        "next_offset": (offset + scanned_count) if has_more else None,
        "embedding_model": embedding_model,
        "vector_backend": stats["backend"],
        "pgvector_native": stats["native"],
        "stored_embeddings": stats["stored_embeddings"],
        "content_items_total": lifecycle_before["content_items_total"],
        "missing_embeddings_before": lifecycle_before["missing_embeddings"],
        "stale_embeddings_before": lifecycle_before["stale_embeddings"],
        "stale_model_embeddings_before": lifecycle_before["stale_model_embeddings"],
        "stale_text_embeddings_before": lifecycle_before["stale_text_embeddings"],
        "coverage_after": _coverage_ratio(stats["stored_embeddings"], lifecycle_before["content_items_total"]),
    }


def _channel_embedding_lifecycle_stats(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    embedding_model: str,
) -> dict[str, int]:
    contents = list(session.scalars(select(ContentItemOrm).where(ContentItemOrm.tenant_id == tenant_id)).all())
    content_ids = {content.id for content in contents}
    embeddings = list(
        session.scalars(
            select(ContentEmbeddingOrm).where(
                ContentEmbeddingOrm.tenant_id == tenant_id,
                ContentEmbeddingOrm.channel_id == channel_id,
                ContentEmbeddingOrm.entity_type == "content_item",
            )
        ).all()
    )
    by_entity = {row.entity_id: row for row in embeddings if row.entity_id in content_ids}
    missing = 0
    stale = 0
    stale_model = 0
    stale_text = 0
    stored_embeddings = 0
    for content in contents:
        text = _content_embedding_text(content)
        if not text:
            continue
        expected_hash = fingerprint_text(text)
        current = by_entity.get(content.id)
        if current is None:
            missing += 1
            continue
        stored_embeddings += 1
        is_stale_model = current.model_name != embedding_model
        is_stale_text = current.text_hash != expected_hash
        if is_stale_model or is_stale_text:
            stale += 1
        if is_stale_model:
            stale_model += 1
        if is_stale_text:
            stale_text += 1
    return {
        "content_items_total": len(contents),
        "stored_embeddings": stored_embeddings,
        "missing_embeddings": missing,
        "stale_embeddings": stale,
        "stale_model_embeddings": stale_model,
        "stale_text_embeddings": stale_text,
    }


def _content_embedding_text(content: ContentItemOrm) -> str:
    return "\n".join(x for x in [content.title or "", content.body_markdown or ""] if x).strip()


def _is_valid_embedding_payload(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except Exception:
        return False
    return isinstance(data, list) and bool(data) and all(isinstance(item, (int, float)) for item in data)


def _coverage_ratio(indexed: int, total: int) -> float:
    return round((indexed / total) if total else 0.0, 4)
