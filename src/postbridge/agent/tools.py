from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from postbridge.agent.retrieval import collect_topic_sources, fetch_seed_sources
from postbridge.agent.vector_store import get_vector_store
from postbridge.config import get_settings
from postbridge.domain.errors import ValidationError
from postbridge.integrations.registry import RULE_POST_TEXT_LIMITS, get_platform_capabilities
from postbridge.models.domain import (
    ChannelOrm,
    ChannelStyleProfileOrm,
    ContentCandidateOrm,
    ContentEmbeddingOrm,
    ContentItemOrm,
    ContentSourceFingerprintOrm,
    LlmProviderConfigOrm,
    PublicationPlanOrm,
    PublicationTargetOrm,
)


def get_channel_context(session: Session, *, tenant_id: str, channel_id: str) -> dict[str, Any]:
    channel = session.get(ChannelOrm, channel_id)
    if channel is None or channel.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_CHANNEL_NOT_FOUND",
            message="channel not found",
            details={"channel_id": channel_id},
        )
    return {
        "channel_id": channel.id,
        "platform": channel.platform,
        "title": channel.title,
        "status": channel.status,
        "config_json": channel.config_json,
        "capabilities_json": channel.capabilities_json,
    }


def get_channel_style_profile(session: Session, *, tenant_id: str, channel_id: str) -> dict[str, Any]:
    row = session.scalar(
        select(ChannelStyleProfileOrm).where(
            ChannelStyleProfileOrm.tenant_id == tenant_id,
            ChannelStyleProfileOrm.channel_id == channel_id,
        )
    )
    if row is None:
        return {"source": "derived", "profile": {}, "version": 0}
    try:
        profile = json.loads(row.profile_json)
    except json.JSONDecodeError:
        profile = {}
    return {"source": row.source, "profile": profile, "version": row.version}


def list_recent_publications(
    session: Session, *, tenant_id: str, channel_id: str, limit: int = 10
) -> list[dict[str, Any]]:
    stmt = (
        select(ContentItemOrm, PublicationTargetOrm)
        .join(PublicationPlanOrm, PublicationPlanOrm.content_item_id == ContentItemOrm.id)
        .join(PublicationTargetOrm, PublicationTargetOrm.publication_plan_id == PublicationPlanOrm.id)
        .where(
            ContentItemOrm.tenant_id == tenant_id,
            PublicationTargetOrm.channel_id == channel_id,
            PublicationTargetOrm.status == "published",
        )
        .order_by(desc(PublicationTargetOrm.published_at), desc(ContentItemOrm.updated_at))
        .limit(limit)
    )
    rows = session.execute(stmt).all()
    out: list[dict[str, Any]] = []
    for content, target in rows:
        out.append(
            {
                "content_item_id": content.id,
                "title": content.title,
                "body_markdown": content.body_markdown,
                "published_at": target.published_at.isoformat() if target.published_at else None,
                "external_url": target.external_url,
            }
        )
    return out


def search_similar_publications(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    title: str | None = None,
    body_markdown: str | None = None,
    source_url: str | None = None,
    source_bundle: dict[str, Any] | list[dict[str, Any]] | None = None,
    embedding_vector: list[float] | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """Find exact and near-duplicate publication signals for a candidate."""
    channel = session.get(ChannelOrm, channel_id)
    if channel is None or channel.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_CHANNEL_NOT_FOUND",
            message="channel not found",
            details={"channel_id": channel_id},
        )
    limit = max(int(top_k or 5), 1)
    candidate_text = " ".join(part for part in [title, body_markdown] if isinstance(part, str) and part.strip())
    candidate_fp = fingerprint_text(candidate_text)
    source_urls = _candidate_source_urls(source_url=source_url, source_bundle=source_bundle)
    matches: list[dict[str, Any]] = []
    seen: set[str] = set()

    for url in source_urls:
        source_hash = canonical_source_hash(source_url=url, title=None, body_markdown=None)
        stmt = select(ContentSourceFingerprintOrm).where(
            ContentSourceFingerprintOrm.tenant_id == tenant_id,
            ContentSourceFingerprintOrm.channel_id == channel_id,
            ContentSourceFingerprintOrm.source_url_hash == source_hash,
        )
        for row in session.scalars(stmt).all():
            _append_similarity_match(
                matches,
                seen,
                {
                    "match_type": "source_url",
                    "score": 1.0,
                    "source_url": row.canonical_url or url,
                    "content_item_id": row.published_content_item_id,
                    "candidate_id": row.candidate_id,
                    "reason": "exact source URL match",
                },
            )

    recent_stmt = (
        select(ContentItemOrm)
        .join(PublicationPlanOrm, PublicationPlanOrm.content_item_id == ContentItemOrm.id)
        .join(PublicationTargetOrm, PublicationTargetOrm.publication_plan_id == PublicationPlanOrm.id)
        .where(
            ContentItemOrm.tenant_id == tenant_id,
            PublicationTargetOrm.channel_id == channel_id,
            PublicationTargetOrm.status == "published",
        )
        .order_by(desc(PublicationTargetOrm.published_at), desc(ContentItemOrm.updated_at))
        .limit(max(limit * 4, 20))
    )
    for row in session.scalars(recent_stmt).all():
        title_score = jaccard_similarity(fingerprint_text(title or ""), fingerprint_text(row.title or ""))
        body_score = jaccard_similarity(candidate_fp, fingerprint_text(" ".join([row.title or "", row.body_markdown or ""])))
        score = max(title_score, body_score)
        if title and row.title and title.strip().lower() == row.title.strip().lower():
            score = 1.0
            match_type = "headline_exact"
            reason = "exact headline match"
        elif title_score >= 0.75:
            match_type = "headline_near"
            reason = "near headline match"
        elif body_score >= 0.45:
            match_type = "semantic_fingerprint"
            reason = "near text fingerprint match"
        else:
            continue
        _append_similarity_match(
            matches,
            seen,
            {
                "match_type": match_type,
                "score": round(score, 4),
                "content_item_id": row.id,
                "title": row.title,
                "published_at": row.updated_at.isoformat() if row.updated_at else None,
                "reason": reason,
            },
        )

    if embedding_vector:
        for item in find_similar_embeddings(
            session,
            tenant_id=tenant_id,
            channel_id=channel_id,
            vector=embedding_vector,
            entity_type="content_item",
            top_k=limit,
        ):
            score = float(item.get("score") or 0.0)
            if score < 0.82:
                continue
            _append_similarity_match(
                matches,
                seen,
                {
                    "match_type": "embedding",
                    "score": round(score, 4),
                    "content_item_id": item.get("entity_id"),
                    "embedding_model": item.get("model_name"),
                    "reason": "semantic embedding match",
                },
            )

    matches.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    matches = matches[:limit]
    high_confidence = [item for item in matches if float(item.get("score") or 0.0) >= 0.82]
    return {
        "matches": matches,
        "match_count": len(matches),
        "high_confidence_duplicate": bool(high_confidence),
        "summary": (
            f"{len(high_confidence)} high-confidence duplicate signal(s)"
            if high_confidence
            else ("related prior publication found" if matches else "no similar publications found")
        ),
    }


def extract_news_facts(
    source: dict[str, Any] | list[dict[str, Any]],
    *,
    topic: str | None = None,
) -> dict[str, Any]:
    """Normalize source snippets into a small evidence-first fact package."""
    items = source if isinstance(source, list) else [source]
    facts: list[dict[str, Any]] = []
    entities: set[str] = set()
    locations: set[str] = set()
    event_dates: list[str] = []
    topic_tokens = token_set(topic or "")
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        excerpt = str(item.get("text_excerpt") or item.get("search_snippet") or "").strip()
        text = _collapse_fact_text(" ".join(part for part in [title, excerpt] if part))
        event_date = _first_string(item.get("published_at"), item.get("updated_at"), item.get("event_date"))
        if event_date:
            event_dates.append(event_date)
        item_entities = _extract_named_entities(text)
        item_locations = _extract_location_candidates(text, topic_tokens=topic_tokens)
        entities.update(item_entities)
        locations.update(item_locations)
        facts.append(
            {
                "headline": title or None,
                "event_date": event_date,
                "entities": item_entities[:8],
                "location": item_locations[0] if item_locations else None,
                "source_url": item.get("url"),
                "why_relevant": _fact_relevance_reason(text, topic_tokens=topic_tokens),
                "source_type": item.get("source_type") or classify_source_type(item),
            }
        )
    return {
        "facts": facts,
        "headline": facts[0]["headline"] if facts else None,
        "event_date": event_dates[0] if event_dates else None,
        "entities": sorted(entities)[:12],
        "location": sorted(locations)[0] if locations else None,
        "source_urls": [
            item.get("source_url")
            for item in facts
            if isinstance(item.get("source_url"), str) and item.get("source_url")
        ],
        "fact_count": len(facts),
    }


def validate_platform_constraints(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    channel = session.get(ChannelOrm, channel_id)
    if channel is None or channel.tenant_id != tenant_id:
        raise ValidationError(
            code="VALIDATION_CHANNEL_NOT_FOUND",
            message="channel not found",
            details={"channel_id": channel_id},
        )
    platform = channel.platform
    capabilities = _load_json_dict(channel.capabilities_json)
    body = str(candidate.get("body_markdown") or "").strip()
    title = str(candidate.get("headline") or candidate.get("title") or "").strip()
    media_urls = _candidate_media_urls(candidate)
    text_limit = _platform_text_limit(platform=platform, capabilities=capabilities)
    rendered_text = "\n\n".join(part for part in [title, body] if part).strip()
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not body:
        errors.append({"code": "empty_body_markdown", "message": "body_markdown is required"})
    if len(title) > 512:
        errors.append({"code": "headline_too_long", "message": "headline exceeds 512 characters", "limit": 512})
    if text_limit is not None and len(rendered_text) > text_limit:
        errors.append(
            {
                "code": "text_too_long",
                "message": "rendered text exceeds platform limit",
                "limit": text_limit,
                "actual": len(rendered_text),
            }
        )
    invalid_media = [url for url in media_urls if not _is_http_url(url)]
    if invalid_media:
        errors.append({"code": "invalid_media_url", "message": "media URLs must be http(s) URLs", "urls": invalid_media})
    if candidate.get("media_url") and media_urls and str(candidate.get("media_url")).strip() not in media_urls:
        warnings.append({"code": "media_url_not_in_media_urls", "message": "media_url is not present in media_urls"})
    if re.search(r"(?is)<script\b|</script>", body):
        errors.append({"code": "unsafe_markup", "message": "script tags are not allowed"})
    if re.search(r"(?is)<[^>]+>", body):
        warnings.append({"code": "html_markup", "message": "HTML markup may not be supported by the target platform"})

    return {
        "ok": not errors,
        "platform": platform,
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "headline_chars": len(title),
            "body_chars": len(body),
            "rendered_chars": len(rendered_text),
            "media_url_count": len(media_urls),
        },
        "limits": {
            "text_chars": text_limit,
            "headline_chars": 512,
        },
        "capabilities": {
            "supports_target": bool(get_platform_capabilities(platform).supports_target)
            if get_platform_capabilities(platform)
            else None,
            "channel_overrides": sorted(capabilities),
        },
    }


def _append_similarity_match(
    matches: list[dict[str, Any]],
    seen: set[str],
    item: dict[str, Any],
) -> None:
    marker = "|".join(
        str(item.get(key) or "")
        for key in ("match_type", "content_item_id", "candidate_id", "source_url")
    )
    if marker in seen:
        return
    seen.add(marker)
    matches.append(item)


def _candidate_source_urls(
    *,
    source_url: str | None,
    source_bundle: dict[str, Any] | list[dict[str, Any]] | None,
) -> list[str]:
    out: list[str] = []
    if isinstance(source_url, str) and source_url.strip():
        out.append(source_url.strip())
    for item in source_detail_items(source_bundle):
        url = item.get("url")
        if isinstance(url, str) and url.strip():
            out.append(url.strip())
    seen: set[str] = set()
    deduped: list[str] = []
    for url in out:
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(url)
    return deduped


def _candidate_media_urls(candidate: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    media_url = candidate.get("media_url")
    if isinstance(media_url, str) and media_url.strip():
        urls.append(media_url.strip())
    raw_urls = candidate.get("media_urls")
    if isinstance(raw_urls, list):
        urls.extend(item.strip() for item in raw_urls if isinstance(item, str) and item.strip())
    cover = candidate.get("cover_image_url")
    if isinstance(cover, str) and cover.strip():
        urls.append(cover.strip())
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def _is_http_url(raw: str) -> bool:
    parsed = urlparse(raw)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _load_json_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _platform_text_limit(*, platform: str, capabilities: dict[str, Any]) -> int | None:
    for key in ("rule_post_text_limit", "max_text_length", "text_limit", "max_post_chars"):
        raw = capabilities.get(key)
        if isinstance(raw, int) and raw > 0:
            return raw
    return RULE_POST_TEXT_LIMITS.get(platform)


def _collapse_fact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _first_string(*items: Any) -> str | None:
    for item in items:
        if isinstance(item, str) and item.strip():
            return item.strip()
    return None


def _extract_named_entities(text: str) -> list[str]:
    candidates = re.findall(r"\b[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9-]*(?:\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9-]*){0,3}", text)
    stop = {"The", "This", "That", "Today", "Yesterday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}
    out: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        value = raw.strip(" .,;:!?")
        if len(value) < 3 or value in stop or value.lower() in seen:
            continue
        seen.add(value.lower())
        out.append(value)
        if len(out) >= 12:
            break
    return out


def _extract_location_candidates(text: str, *, topic_tokens: set[str]) -> list[str]:
    entities = _extract_named_entities(text)
    locality_markers = {"city", "region", "district", "область", "город", "район", "край", "republic"}
    out: list[str] = []
    for entity in entities:
        lowered = entity.lower()
        if lowered in topic_tokens or any(marker in text.lower() for marker in locality_markers):
            out.append(entity)
    return out[:5]


def _fact_relevance_reason(text: str, *, topic_tokens: set[str]) -> str:
    if not topic_tokens:
        return "source provides current context"
    overlap = token_set(text) & topic_tokens
    if overlap:
        return "matches topic terms: " + ", ".join(sorted(overlap)[:5])
    return "source provides adjacent context"


def find_default_provider(session: Session, *, tenant_id: str) -> LlmProviderConfigOrm | None:
    row = session.scalar(
        select(LlmProviderConfigOrm)
        .where(LlmProviderConfigOrm.tenant_id == tenant_id, LlmProviderConfigOrm.is_default.is_(True))
        .limit(1)
    )
    if row is not None:
        return row
    return session.scalar(
        select(LlmProviderConfigOrm)
        .where(LlmProviderConfigOrm.tenant_id == tenant_id)
        .order_by(LlmProviderConfigOrm.created_at.asc())
        .limit(1)
    )


def collect_seed_sources(seed_urls: list[str] | None) -> list[dict[str, Any]]:
    return fetch_seed_sources(seed_urls or [])


def dedupe_mixed_list(items: list[Any] | None) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for item in items or []:
        if isinstance(item, (dict, list)):
            marker = json.dumps(item, ensure_ascii=True, sort_keys=True)
        else:
            marker = repr(item)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(item)
    return out


def normalize_risk_flags(items: list[Any] | None) -> list[str]:
    flags: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        if isinstance(item, str):
            normalized = item.strip()
        elif isinstance(item, dict):
            normalized = str(item.get("flag") or item.get("code") or item.get("name") or "").strip()
        else:
            normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        flags.append(normalized)
    return flags


def collect_topic_evidence(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    topic: str | None,
    seed_urls: list[str] | None = None,
    workspace_policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not topic and not seed_urls:
        return []
    policy = workspace_policy if isinstance(workspace_policy, dict) else {}
    sources = collect_topic_sources(
        topic or "",
        seed_urls=seed_urls or [],
        preferred_domains={
            str(item).strip().lower()
            for item in (policy.get("preferred_domains") or [])
            if isinstance(item, str) and str(item).strip()
        },
        blocked_domains={
            str(item).strip().lower()
            for item in (policy.get("blocked_domains") or [])
            if isinstance(item, str) and str(item).strip()
        },
        blocked_url_patterns=[
            str(item).strip()
            for item in (policy.get("blocked_url_patterns") or [])
            if isinstance(item, str) and str(item).strip()
        ],
    )
    if not sources:
        return []
    topic_signals = infer_topic_signals(topic)
    trust_map = _historical_domain_trust_map(session, tenant_id=tenant_id, channel_id=channel_id)
    source_type_trust_map = _historical_source_type_trust_map(session, tenant_id=tenant_id, channel_id=channel_id)
    ranked: list[dict[str, Any]] = []
    for item in sources:
        domain = _source_domain(item)
        trust = trust_map.get(domain or "", {"trust_score": 0.5, "trust_label": "unknown", "history_count": 0})
        retrieval_score = float(item.get("retrieval_score") or 0.0)
        source_type = classify_source_type(item)
        source_type_weight = source_type_editorial_weight(source_type)
        source_type_trust = source_type_trust_map.get(
            source_type,
            {"trust_score": 0.5, "trust_label": "unknown", "history_count": 0},
        )
        local_relevance = source_local_relevance_score(item, topic_signals)
        news_relevance = source_news_relevance_score(item, topic_signals)
        combined = round(
            retrieval_score * 0.55
            + float(trust["trust_score"]) * 0.2
            + source_type_weight * 0.1
            + float(source_type_trust["trust_score"]) * 0.05
            + local_relevance * 0.05
            + news_relevance * 0.05,
            4,
        )
        ranked_item = dict(item)
        ranked_item["source_type"] = source_type
        ranked_item["source_type_weight"] = round(source_type_weight, 4)
        ranked_item["source_type_trust_score"] = float(source_type_trust["trust_score"])
        ranked_item["source_type_trust_label"] = str(source_type_trust["trust_label"])
        ranked_item["source_type_history_count"] = int(source_type_trust["history_count"])
        ranked_item["local_relevance_score"] = round(local_relevance, 4)
        ranked_item["news_relevance_score"] = round(news_relevance, 4)
        ranked_item["topic_intent"] = topic_signals["intent"]
        ranked_item["retrieval_trust_score"] = float(trust["trust_score"])
        ranked_item["retrieval_trust_label"] = str(trust["trust_label"])
        ranked_item["retrieval_history_count"] = int(trust["history_count"])
        ranked_item["retrieval_combined_score"] = combined
        ranked.append(ranked_item)
    ranked.sort(key=lambda row: float(row.get("retrieval_combined_score") or 0.0), reverse=True)
    for idx, item in enumerate(ranked, start=1):
        item["retrieval_rank"] = idx
    return ranked


def shortlist_topic_evidence(
    sources: list[dict[str, Any]] | None,
    *,
    topic: str | None = None,
    max_sources: int = 6,
    max_per_domain: int = 2,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = get_settings()
    items = [dict(item) for item in (sources or []) if isinstance(item, dict)]
    if not items:
        return [], {
            "input_sources": 0,
            "selected_sources": 0,
            "dropped_sources": 0,
            "unique_domains": 0,
            "max_per_domain": max_per_domain,
            "topic_intent": "general",
            "freshness_filtered": 0,
            "source_type_filtered": 0,
            "domain_filtered": 0,
        }
    topic_signals = infer_topic_signals(topic)
    ranked = sorted(
        items,
        key=lambda item: (
            float(item.get("retrieval_combined_score") or item.get("retrieval_score") or 0.0),
            float(item.get("retrieval_trust_score") or 0.0),
            float(item.get("source_type_weight") or 0.0),
        ),
        reverse=True,
    )
    fresh_candidates = [
        item
        for item in ranked
        if _source_age_hours(item) is not None and float(_source_age_hours(item) or 0.0) <= float(settings.agent_search_max_source_age_hours)
    ]
    freshness_pool = fresh_candidates if len(fresh_candidates) >= min(max_sources, 3) else ranked
    preferred_source_types = preferred_source_types_for_topic(topic_signals)
    preferred_pool = [
        item
        for item in freshness_pool
        if not preferred_source_types
        or str(item.get("source_type") or classify_source_type(item)) in preferred_source_types
    ]
    source_type_filtered = len(freshness_pool) - len(preferred_pool)
    selection_pool = preferred_pool if len(preferred_pool) >= min(max_sources, 3) else freshness_pool
    domain_counts: dict[str, int] = {}
    selected: list[dict[str, Any]] = []
    freshness_filtered = len(ranked) - len(freshness_pool)
    domain_filtered = 0
    for item in selection_pool:
        domain = _source_domain(item) or "__unknown__"
        if domain_counts.get(domain, 0) >= max_per_domain:
            domain_filtered += 1
            continue
        selected.append(item)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        if len(selected) >= max_sources:
            break
    if len(selected) < min(max_sources, len(selection_pool)):
        for item in selection_pool:
            if item in selected:
                continue
            selected.append(item)
            if len(selected) >= max_sources:
                break
    if len(selected) < min(max_sources, len(ranked)):
        for item in ranked:
            if item in selected:
                continue
            domain = _source_domain(item) or "__unknown__"
            if domain_counts.get(domain, 0) >= max_per_domain:
                continue
            selected.append(item)
            domain_counts[domain] = domain_counts.get(domain, 0) + 1
            if len(selected) >= max_sources:
                break
    return selected, {
        "input_sources": len(ranked),
        "selected_sources": len(selected),
        "dropped_sources": max(len(ranked) - len(selected), 0),
        "unique_domains": len({_source_domain(item) for item in ranked if _source_domain(item)}),
        "max_per_domain": max_per_domain,
        "topic_intent": topic_signals["intent"],
        "freshness_filtered": freshness_filtered,
        "source_type_filtered": max(source_type_filtered, 0),
        "domain_filtered": domain_filtered,
    }


def shortlist_topic_angles(
    sources: list[dict[str, Any]] | None,
    *,
    topic: str | None = None,
    max_angles: int = 4,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items = [dict(item) for item in (sources or []) if isinstance(item, dict)]
    if not items:
        return [], {"input_sources": 0, "selected_angles": 0, "dropped_angles": 0, "topic_intent": "general"}
    topic_signals = infer_topic_signals(topic)
    seen_headlines: set[str] = set()
    angles: list[dict[str, Any]] = []
    for item in items:
        title = str(item.get("title") or "").strip()
        excerpt = str(item.get("text_excerpt") or item.get("search_snippet") or "").strip()
        normalized_headline = (
            fingerprint_text(title)
            or fingerprint_text(excerpt[:160])
            or re.sub(r"\s+", " ", title.lower()).strip()
            or re.sub(r"\s+", " ", excerpt[:160].lower()).strip()
        )
        if not normalized_headline or normalized_headline in seen_headlines:
            continue
        seen_headlines.add(normalized_headline)
        source_type = str(item.get("source_type") or classify_source_type(item))
        domains = [domain for domain in [_source_domain(item)] if domain]
        angle_score = round(
            float(item.get("retrieval_combined_score") or item.get("retrieval_score") or 0.0) * 0.7
            + float(item.get("news_relevance_score") or 0.0) * 0.2
            + float(item.get("local_relevance_score") or 0.0) * 0.1,
            4,
        )
        angles.append(
            {
                "angle": title or excerpt[:120] or "Untitled angle",
                "why_this_angle": _build_angle_reason(item, topic_signals=topic_signals),
                "headline_hint": title or None,
                "source_urls": [item["url"]] if isinstance(item.get("url"), str) else [],
                "source_domains": domains,
                "source_types": [source_type],
                "score": angle_score,
            }
        )
        if len(angles) >= max_angles:
            break
    return angles, {
        "input_sources": len(items),
        "selected_angles": len(angles),
        "dropped_angles": max(len(items) - len(angles), 0),
        "topic_intent": topic_signals["intent"],
    }


def summarize_dedup(
    recent_publications: list[dict[str, Any]], *, topic: str | None, headline: str | None
) -> tuple[str, bool]:
    candidate_text = " ".join(x for x in [headline, topic] if x).strip()
    if not candidate_text:
        return "no duplicate signal", False
    title_norm = candidate_text.lower()
    for publication in recent_publications:
        recent_title = (publication.get("title") or "").strip().lower()
        if recent_title and recent_title == title_norm:
            return "exact title match with recent publication", True
    candidate_fp = fingerprint_text(candidate_text)
    best_score = 0.0
    best_overlap = 0
    best_title = ""
    for publication in recent_publications:
        recent_fp = fingerprint_text(
            " ".join(
                x
                for x in [
                    publication.get("title") or "",
                    publication.get("body_markdown") or "",
                ]
                if x
            )
        )
        score = jaccard_similarity(candidate_fp, recent_fp)
        overlap = len(set(candidate_fp.split()) & set(recent_fp.split()))
        if score > best_score:
            best_score = score
            best_overlap = overlap
            best_title = publication.get("title") or ""
    if best_score >= 0.75 or (best_score >= 0.45 and best_overlap >= 4):
        return f"near-duplicate signal with recent publication '{best_title}' (score={best_score:.2f})", True
    if best_score >= 0.45:
        return f"related to recent publication '{best_title}' (score={best_score:.2f})", False
    return "no exact duplicates among recent publications", False


def fingerprint_text(text: str) -> str:
    tokens = token_set(text)
    return " ".join(sorted(tokens))


def token_set(text: str) -> set[str]:
    normalized = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ]+", " ", (text or "").lower())
    tokens = [tok for tok in normalized.split() if len(tok) >= 3]
    return set(tokens)


def jaccard_similarity(a_fp: str, b_fp: str) -> float:
    a = set(a_fp.split()) if a_fp else set()
    b = set(b_fp.split()) if b_fp else set()
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def upsert_content_fingerprint(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    source_url: str | None,
    title: str | None,
    body_markdown: str | None,
    published_content_item_id: str | None,
    candidate_id: str | None,
) -> None:
    url_hash = canonical_source_hash(source_url=source_url, title=title, body_markdown=body_markdown)
    if not url_hash:
        return
    title_hash = fingerprint_text(title or "")
    semantic_fingerprint = fingerprint_text(" ".join(x for x in [title, body_markdown] if x))
    row = session.scalar(
        select(ContentSourceFingerprintOrm).where(
            ContentSourceFingerprintOrm.tenant_id == tenant_id,
            ContentSourceFingerprintOrm.channel_id == channel_id,
            ContentSourceFingerprintOrm.source_url_hash == url_hash,
        )
    )
    if row is None:
        session.add(
            ContentSourceFingerprintOrm(
                id=str(uuid4()),
                tenant_id=tenant_id,
                channel_id=channel_id,
                source_url_hash=url_hash,
                canonical_url=source_url,
                source_title_hash=title_hash or None,
                semantic_fingerprint=semantic_fingerprint or None,
                published_content_item_id=published_content_item_id,
                candidate_id=candidate_id,
            )
        )
    else:
        row.canonical_url = source_url or row.canonical_url
        row.source_title_hash = title_hash or row.source_title_hash
        row.semantic_fingerprint = semantic_fingerprint or row.semantic_fingerprint
        row.published_content_item_id = published_content_item_id or row.published_content_item_id
        row.candidate_id = candidate_id or row.candidate_id


def canonical_source_hash(*, source_url: str | None, title: str | None, body_markdown: str | None) -> str:
    if isinstance(source_url, str) and source_url.strip():
        normalized_url = source_url.strip().lower()
        return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()
    return fingerprint_text(title or body_markdown or "")


def build_review_payload(candidate: ContentCandidateOrm, *, autonomy_mode: str = "draft_approval") -> dict[str, Any]:
    def _load(raw: str | None, default: Any) -> Any:
        if not raw:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default

    draft_payload = _load(candidate.draft_json, {})
    source_quality_summary = (
        draft_payload.get("source_quality_summary")
        if isinstance(draft_payload, dict)
        else {}
    )
    scores = _load(candidate.scores_json, {})
    risk_flags = _load(candidate.risk_flags_json, [])
    review_hints = build_review_hints(
        source_quality_summary=source_quality_summary if isinstance(source_quality_summary, dict) else {},
        scores=scores if isinstance(scores, dict) else {},
        risk_flags=risk_flags if isinstance(risk_flags, list) else [],
    )
    workflow_preset = infer_workflow_preset(
        source_quality_summary=source_quality_summary if isinstance(source_quality_summary, dict) else {},
        scores=scores if isinstance(scores, dict) else {},
        risk_flags=risk_flags if isinstance(risk_flags, list) else [],
        review_hints=review_hints,
    )
    suggestion = suggest_review_decision(
        source_quality_summary=source_quality_summary if isinstance(source_quality_summary, dict) else {},
        scores=scores if isinstance(scores, dict) else {},
        risk_flags=risk_flags if isinstance(risk_flags, list) else [],
        review_hints=review_hints,
    )
    return {
        "candidate_id": candidate.id,
        "channel_id": candidate.channel_id,
        "autonomy_mode": autonomy_mode,
        "topic": candidate.topic,
        "headline": candidate.headline,
        "body_markdown": candidate.body_markdown,
        "summary": candidate.summary,
        "why_now": candidate.why_now,
        "style_fit_summary": candidate.style_fit_summary,
        "dedup_summary": candidate.dedup_summary,
        "source_bundle": _load(candidate.source_bundle_json, {}),
        "scores": scores,
        "risk_flags": risk_flags,
        "source_quality_summary": source_quality_summary,
        "review_hints": review_hints,
        "workflow_preset": workflow_preset,
        "suggested_decision": suggestion["decision"],
        "suggested_review_action": suggestion["review_action"],
        "proposed_next_action": "review_candidate",
        "created_at": candidate.created_at.isoformat(),
    }


def upsert_content_embedding(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    entity_type: str,
    entity_id: str,
    model_name: str,
    vector: list[float],
    text_hash: str | None,
) -> None:
    get_vector_store(session).upsert_embedding(
        tenant_id=tenant_id,
        channel_id=channel_id,
        entity_type=entity_type,
        entity_id=entity_id,
        model_name=model_name,
        vector=vector,
        text_hash=text_hash,
    )


def find_similar_embeddings(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    vector: list[float],
    entity_type: str | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    return get_vector_store(session).find_similar(
        tenant_id=tenant_id,
        channel_id=channel_id,
        vector=vector,
        entity_type=entity_type,
        top_k=top_k,
    )


def analyze_source_quality(source_bundle: dict[str, Any] | list[dict[str, Any]] | None) -> dict[str, Any]:
    items = source_detail_items(source_bundle)
    domains = [domain for item in items if (domain := _source_domain(item))]
    unique_domains = sorted(set(domains))
    source_types = [str(item.get("source_type") or classify_source_type(item)) for item in items]
    corroboration_score = round(min(len(unique_domains) / 3.0, 1.0), 4) if unique_domains else 0.0
    disagreement_score, conflict_score = source_disagreement_details(items)
    conflict_explanations = source_conflict_explanations(items)
    source_type_trust_score = round(
        sum(float(item.get("source_type_trust_score") or 0.5) for item in items) / len(items),
        4,
    ) if items else 0.0
    repeated_domain_pressure = (
        round(max(len(domains) - len(unique_domains), 0) / max(len(domains), 1), 4) if domains else 0.0
    )
    freshness_score = _source_freshness_score(items)
    risk_flags: list[str] = []
    if unique_domains and len(unique_domains) == 1:
        risk_flags.append("single_source")
    if disagreement_score >= 0.55:
        risk_flags.append("source_disagreement")
    if conflict_score >= 0.65:
        risk_flags.append("source_conflict")
    if repeated_domain_pressure >= 0.34:
        risk_flags.append("source_concentration")
    if source_type_trust_score <= 0.3 and items:
        risk_flags.append("low_trust_source_type_mix")
    if freshness_score == 0.0 and items:
        risk_flags.append("stale_or_unknown_source_freshness")
    return {
        "source_count": len(items),
        "unique_domain_count": len(unique_domains),
        "unique_domains": unique_domains,
        "source_types": sorted(set(source_types)),
        "source_type_trust_score": source_type_trust_score,
        "dominant_domain": Counter(domains).most_common(1)[0][0] if domains else None,
        "corroboration_score": corroboration_score,
        "disagreement_score": disagreement_score,
        "conflict_score": conflict_score,
        "repeated_domain_pressure": repeated_domain_pressure,
        "freshness_score": freshness_score,
        "conflict_explanations": conflict_explanations,
        "risk_flags": risk_flags,
    }


def source_detail_items(source_bundle: dict[str, Any] | list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if isinstance(source_bundle, list):
        return [item for item in source_bundle if isinstance(item, dict)]
    if not isinstance(source_bundle, dict):
        return []
    items: list[dict[str, Any]] = []
    for key in ("primary_sources_details", "seed_sources"):
        value = source_bundle.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None]] = set()
    for item in items:
        marker = (item.get("url"), item.get("title"))
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(item)
    return deduped


def source_disagreement_score(items: list[dict[str, Any]]) -> float:
    return source_disagreement_details(items)[0]


def source_disagreement_details(items: list[dict[str, Any]]) -> tuple[float, float]:
    if len(items) < 2:
        return 0.0, 0.0
    normalized = [
        {
            "title_tokens": token_set(item.get("title") or ""),
            "excerpt_tokens": token_set((item.get("text_excerpt") or "")[:600]),
            "all_tokens": token_set(" ".join(x for x in [item.get("title") or "", item.get("text_excerpt") or ""] if x)),
            "published_at": _parse_dt(item.get("published_at") or item.get("updated_at")),
            "numeric_tokens": _numeric_tokens(" ".join(x for x in [item.get("title") or "", item.get("text_excerpt") or ""] if x)),
            "text": " ".join(x for x in [str(item.get("title") or ""), str(item.get("text_excerpt") or "")] if x).lower(),
            "entity_tokens": _entity_tokens(" ".join(x for x in [str(item.get("title") or ""), str(item.get("text_excerpt") or "")] if x)),
            "event_tokens": _event_tokens(" ".join(x for x in [str(item.get("title") or ""), str(item.get("text_excerpt") or "")] if x)),
        }
        for item in items
    ]
    disagreement_scores: list[float] = []
    conflict_scores: list[float] = []
    for idx, left in enumerate(normalized):
        for right in normalized[idx + 1 :]:
            title_overlap = _token_overlap(left["title_tokens"], right["title_tokens"])
            excerpt_overlap = _token_overlap(left["excerpt_tokens"], right["excerpt_tokens"])
            time_score = _timestamp_distance_score(left["published_at"], right["published_at"])
            semantic_overlap = title_overlap * 0.55 + excerpt_overlap * 0.45
            numeric_divergence = _numeric_divergence(left["numeric_tokens"], right["numeric_tokens"])
            contradiction_signal = _contradiction_signal(
                left["text"],
                right["text"],
                left["all_tokens"],
                right["all_tokens"],
                numeric_divergence=numeric_divergence,
            )
            entity_event_consistency = _entity_event_consistency_score(
                left["entity_tokens"],
                right["entity_tokens"],
                left["event_tokens"],
                right["event_tokens"],
            )
            disagreement_scores.append(
                round((1.0 - title_overlap) * 0.4 + (1.0 - excerpt_overlap) * 0.4 + time_score * 0.2, 4)
            )
            conflict_scores.append(
                round(
                    min(
                        semantic_overlap * 0.3
                        + numeric_divergence * 0.2
                        + contradiction_signal * 0.26
                        + entity_event_consistency * 0.14
                        + (0.18 if entity_event_consistency >= 0.8 and contradiction_signal >= 0.45 else 0.0)
                        + (0.2 if contradiction_signal >= 0.65 and semantic_overlap >= 0.35 else 0.0)
                        + time_score * 0.1,
                        1.0,
                    ),
                    4,
                )
            )
    if not disagreement_scores:
        return 0.0, 0.0
    return (
        round(sum(disagreement_scores) / len(disagreement_scores), 4),
        round(sum(conflict_scores) / len(conflict_scores), 4),
    )


def source_conflict_explanations(
    items: list[dict[str, Any]],
    *,
    max_examples: int = 3,
) -> list[dict[str, Any]]:
    if len(items) < 2:
        return []
    explanations: list[dict[str, Any]] = []
    normalized = [
        {
            "item": item,
            "title_tokens": token_set(item.get("title") or ""),
            "excerpt_tokens": token_set((item.get("text_excerpt") or "")[:600]),
            "all_tokens": token_set(" ".join(x for x in [item.get("title") or "", item.get("text_excerpt") or ""] if x)),
            "published_at": _parse_dt(item.get("published_at") or item.get("updated_at")),
            "numeric_tokens": _numeric_tokens(" ".join(x for x in [item.get("title") or "", item.get("text_excerpt") or ""] if x)),
            "text": " ".join(x for x in [str(item.get("title") or ""), str(item.get("text_excerpt") or "")] if x).lower(),
            "entity_tokens": _entity_tokens(" ".join(x for x in [str(item.get("title") or ""), str(item.get("text_excerpt") or "")] if x)),
            "event_tokens": _event_tokens(" ".join(x for x in [str(item.get("title") or ""), str(item.get("text_excerpt") or "")] if x)),
        }
        for item in items
    ]
    for idx, left in enumerate(normalized):
        for right in normalized[idx + 1 :]:
            title_overlap = _token_overlap(left["title_tokens"], right["title_tokens"])
            excerpt_overlap = _token_overlap(left["excerpt_tokens"], right["excerpt_tokens"])
            time_score = _timestamp_distance_score(left["published_at"], right["published_at"])
            semantic_overlap = title_overlap * 0.55 + excerpt_overlap * 0.45
            numeric_divergence = _numeric_divergence(left["numeric_tokens"], right["numeric_tokens"])
            contradiction_signal = _contradiction_signal(
                left["text"],
                right["text"],
                left["all_tokens"],
                right["all_tokens"],
                numeric_divergence=numeric_divergence,
            )
            entity_event_consistency = _entity_event_consistency_score(
                left["entity_tokens"],
                right["entity_tokens"],
                left["event_tokens"],
                right["event_tokens"],
            )
            conflict_score = round(
                min(
                    semantic_overlap * 0.3
                    + numeric_divergence * 0.2
                    + contradiction_signal * 0.26
                    + entity_event_consistency * 0.14
                    + (0.18 if entity_event_consistency >= 0.8 and contradiction_signal >= 0.45 else 0.0)
                    + (0.2 if contradiction_signal >= 0.65 and semantic_overlap >= 0.35 else 0.0)
                    + time_score * 0.1,
                    1.0,
                ),
                4,
            )
            if conflict_score < 0.35:
                continue
            reasons: list[str] = []
            if contradiction_signal >= 0.65:
                reasons.append("opposing factual claims")
            elif contradiction_signal >= 0.45:
                reasons.append("claim polarity mismatch")
            if entity_event_consistency >= 0.8:
                reasons.append("same entity but inconsistent event narrative")
            elif entity_event_consistency >= 0.45:
                reasons.append("shared entity with divergent event framing")
            if numeric_divergence >= 0.8:
                reasons.append("conflicting numbers")
            elif numeric_divergence >= 0.35:
                reasons.append("numbers do not fully agree")
            if time_score >= 0.45:
                reasons.append("timestamps are far apart")
            explanations.append(
                {
                    "conflict_score": conflict_score,
                    "reason": ", ".join(reasons) or "source narratives do not align",
                    "left": {
                        "url": left["item"].get("url"),
                        "title": left["item"].get("title"),
                    },
                    "right": {
                        "url": right["item"].get("url"),
                        "title": right["item"].get("title"),
                    },
                }
            )
    explanations.sort(key=lambda item: float(item.get("conflict_score") or 0.0), reverse=True)
    return explanations[:max_examples]


def _source_domain(item: dict[str, Any]) -> str | None:
    url = item.get("url")
    if not isinstance(url, str) or "://" not in url:
        return None
    from urllib.parse import urlparse

    return (urlparse(url).hostname or "").lower() or None


def _historical_domain_trust_map(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    limit: int = 200,
) -> dict[str, dict[str, float | int | str]]:
    rows = list(
        session.scalars(
            select(ContentCandidateOrm)
            .where(
                ContentCandidateOrm.tenant_id == tenant_id,
                ContentCandidateOrm.channel_id == channel_id,
            )
            .order_by(ContentCandidateOrm.created_at.desc())
            .limit(limit)
        ).all()
    )
    stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"candidate_count": 0, "converted_count": 0, "rejected_count": 0, "duplicate_count": 0, "risky_count": 0}
    )
    for candidate in rows:
        source_bundle = _safe_json(candidate.source_bundle_json)
        details = source_detail_items(source_bundle)
        domains = {_source_domain(item) for item in details if _source_domain(item)}
        if not domains:
            primary_sources = source_bundle.get("primary_sources") if isinstance(source_bundle, dict) else None
            if isinstance(primary_sources, list):
                for item in primary_sources:
                    if isinstance(item, str) and "://" in item:
                        from urllib.parse import urlparse

                        domain = (urlparse(item).hostname or "").lower() or None
                        if domain:
                            domains.add(domain)
        risk_flags = _safe_json(candidate.risk_flags_json)
        for domain in domains:
            bucket = stats[domain]
            bucket["candidate_count"] += 1
            if candidate.status == "converted":
                bucket["converted_count"] += 1
            elif candidate.status == "rejected":
                bucket["rejected_count"] += 1
            if isinstance(risk_flags, list):
                if "possible_duplicate" in risk_flags or "embedding_duplicate" in risk_flags:
                    bucket["duplicate_count"] += 1
                if risk_flags:
                    bucket["risky_count"] += 1
    out: dict[str, dict[str, float | int | str]] = {}
    for domain, bucket in stats.items():
        count = bucket["candidate_count"]
        conversion_rate = bucket["converted_count"] / count if count else 0.0
        rejection_rate = bucket["rejected_count"] / count if count else 0.0
        duplicate_rate = bucket["duplicate_count"] / count if count else 0.0
        risky_rate = bucket["risky_count"] / count if count else 0.0
        trust_score = max(min(0.55 + conversion_rate * 0.35 - rejection_rate * 0.2 - duplicate_rate * 0.15 - risky_rate * 0.1, 1.0), 0.0)
        if count < 3:
            trust_label = "insufficient_data"
        elif trust_score >= 0.7:
            trust_label = "trusted"
        elif trust_score <= 0.35:
            trust_label = "risky"
        else:
            trust_label = "mixed"
        out[domain] = {
            "trust_score": round(trust_score, 4),
            "trust_label": trust_label,
            "history_count": count,
        }
    return out


def _historical_source_type_trust_map(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    limit: int = 200,
) -> dict[str, dict[str, float | int | str]]:
    rows = list(
        session.scalars(
            select(ContentCandidateOrm)
            .where(
                ContentCandidateOrm.tenant_id == tenant_id,
                ContentCandidateOrm.channel_id == channel_id,
            )
            .order_by(ContentCandidateOrm.created_at.desc())
            .limit(limit)
        ).all()
    )
    stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"candidate_count": 0, "converted_count": 0, "rejected_count": 0, "duplicate_count": 0, "risky_count": 0}
    )
    for candidate in rows:
        source_bundle = _safe_json(candidate.source_bundle_json)
        details = source_detail_items(source_bundle)
        source_types = {classify_source_type(item) for item in details}
        if not source_types:
            source_types = {"unknown"}
        risk_flags = _safe_json(candidate.risk_flags_json)
        for source_type in source_types:
            bucket = stats[source_type]
            bucket["candidate_count"] += 1
            if candidate.status == "converted":
                bucket["converted_count"] += 1
            elif candidate.status == "rejected":
                bucket["rejected_count"] += 1
            if isinstance(risk_flags, list):
                if "possible_duplicate" in risk_flags or "embedding_duplicate" in risk_flags:
                    bucket["duplicate_count"] += 1
                if risk_flags:
                    bucket["risky_count"] += 1
    out: dict[str, dict[str, float | int | str]] = {}
    for source_type, bucket in stats.items():
        count = bucket["candidate_count"]
        conversion_rate = bucket["converted_count"] / count if count else 0.0
        rejection_rate = bucket["rejected_count"] / count if count else 0.0
        duplicate_rate = bucket["duplicate_count"] / count if count else 0.0
        risky_rate = bucket["risky_count"] / count if count else 0.0
        trust_score = max(
            min(0.5 + conversion_rate * 0.35 - rejection_rate * 0.2 - duplicate_rate * 0.1 - risky_rate * 0.1, 1.0),
            0.0,
        )
        if count < 3:
            trust_label = "insufficient_data"
        elif trust_score >= 0.7:
            trust_label = "trusted"
        elif trust_score <= 0.35:
            trust_label = "risky"
        else:
            trust_label = "mixed"
        out[source_type] = {
            "trust_score": round(trust_score, 4),
            "trust_label": trust_label,
            "history_count": count,
        }
    return out


def infer_topic_signals(topic: str | None) -> dict[str, Any]:
    text = (topic or "").strip().lower()
    tokens = token_set(text)
    intent = "general"
    if any(token in text for token in ("news", "новост", "headline", "update", "updates", "daily", "свеж", "fresh")):
        intent = "news"
    locality_tokens = sorted(
        {
            token
            for token in tokens
            if len(token) >= 4 and token not in {"news", "daily", "fresh", "today", "новости", "свежие"}
        }
    )
    return {"intent": intent, "tokens": tokens, "locality_tokens": locality_tokens}


def preferred_source_types_for_topic(topic_signals: dict[str, Any]) -> set[str]:
    if topic_signals.get("intent") == "news":
        return {"news_article", "local_news", "government", "press_release"}
    return set()


def classify_source_type(item: dict[str, Any]) -> str:
    url = str(item.get("url") or "").lower()
    domain = _source_domain(item) or ""
    title = str(item.get("title") or "").lower()
    excerpt = str(item.get("text_excerpt") or item.get("search_snippet") or "").lower()
    text = " ".join(part for part in [domain, url, title, excerpt] if part)
    if any(marker in text for marker in ("docs.", "/docs", "documentation", "manual", "/help", "/kb/")):
        return "documentation"
    if any(marker in text for marker in ("t.me/", "telegram", "twitter.com", "x.com", "facebook.com", "instagram.com", "vk.com")):
        return "social"
    if any(marker in text for marker in ("reddit.com", "forum", "/community", "discuss", "comment")):
        return "community"
    if any(marker in text for marker in ("shop", "store", "product", "pricing", "catalog", "marketplace")):
        return "commercial"
    if domain.endswith(".gov") or ".gov." in domain or any(marker in text for marker in ("government", "ministry", "municipal", "city hall", "official portal", "администрац")):
        return "government"
    if any(marker in text for marker in ("press release", "press-center", "press centre", "пресс-релиз", "пресс-центр")):
        return "press_release"
    if any(marker in text for marker in ("blog", "/blog", "opinion", "column", "essay")):
        return "blog"
    if any(marker in text for marker in ("news", "новости", "report", "reported", "breaking", "headline")):
        if any(marker in text for marker in ("novosibirsk", "новосибир", "city", "region", "municipal")):
            return "local_news"
        return "news_article"
    if any(marker in text for marker in ("aggregator", "roundup", "digest")):
        return "aggregator"
    return "unknown"


def source_type_editorial_weight(source_type: str) -> float:
    weights = {
        "local_news": 1.0,
        "news_article": 0.9,
        "government": 0.88,
        "press_release": 0.78,
        "blog": 0.52,
        "community": 0.4,
        "social": 0.35,
        "aggregator": 0.33,
        "documentation": 0.2,
        "commercial": 0.15,
        "unknown": 0.45,
    }
    return weights.get(source_type, 0.45)


def source_local_relevance_score(item: dict[str, Any], topic_signals: dict[str, Any]) -> float:
    locality_tokens = set(topic_signals.get("locality_tokens") or [])
    if not locality_tokens:
        return 0.5
    text = " ".join(
        str(part or "").lower()
        for part in [item.get("title"), item.get("text_excerpt"), item.get("search_snippet"), item.get("url")]
    )
    overlap = len({token for token in locality_tokens if token in text})
    return round(min(overlap / max(len(locality_tokens), 1), 1.0), 4)


def source_news_relevance_score(item: dict[str, Any], topic_signals: dict[str, Any]) -> float:
    if topic_signals.get("intent") != "news":
        return 0.5
    source_type = str(item.get("source_type") or classify_source_type(item))
    if source_type in {"local_news", "news_article"}:
        return 1.0
    if source_type in {"government", "press_release"}:
        return 0.75
    if source_type in {"blog", "aggregator"}:
        return 0.4
    if source_type in {"documentation", "commercial"}:
        return 0.1
    return 0.3


def _build_angle_reason(item: dict[str, Any], *, topic_signals: dict[str, Any]) -> str:
    parts: list[str] = []
    source_type = str(item.get("source_type") or classify_source_type(item))
    if topic_signals.get("intent") == "news":
        parts.append("fresh news lead")
    if source_type != "unknown":
        parts.append(f"source_type={source_type}")
    trust_label = item.get("retrieval_trust_label")
    if trust_label:
        parts.append(f"trust={trust_label}")
    local_relevance = float(item.get("local_relevance_score") or 0.0)
    if local_relevance >= 0.5:
        parts.append("strong local relevance")
    return ", ".join(parts) or "high-ranked source"


def _safe_json(raw: str | None) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _source_freshness_score(items: list[dict[str, Any]]) -> float:
    if not items:
        return 0.0
    now = datetime.now(UTC)
    scores: list[float] = []
    for item in items:
        timestamp = _parse_dt(item.get("published_at") or item.get("updated_at"))
        if timestamp is None:
            continue
        age_hours = max((now - timestamp).total_seconds() / 3600.0, 0.0)
        if age_hours <= 6:
            scores.append(1.0)
        elif age_hours <= 24:
            scores.append(0.8)
        elif age_hours <= 72:
            scores.append(0.5)
        else:
            scores.append(0.15)
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 4)


def _source_age_hours(item: dict[str, Any]) -> float | None:
    timestamp = _parse_dt(item.get("published_at") or item.get("updated_at"))
    if timestamp is None:
        return None
    return max((datetime.now(UTC) - timestamp).total_seconds() / 3600.0, 0.0)


def _parse_dt(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    value = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _timestamp_distance_score(left: datetime | None, right: datetime | None) -> float:
    if left is None or right is None:
        return 0.35
    hours = abs((left - right).total_seconds()) / 3600.0
    if hours <= 6:
        return 0.0
    if hours <= 24:
        return 0.2
    if hours <= 72:
        return 0.45
    return 0.8


def _token_overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _numeric_tokens(text: str) -> set[str]:
    return set(re.findall(r"\b\d+(?:[.,]\d+)?\b", text or ""))


def _numeric_divergence(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    overlap = len(left & right) / len(left | right)
    if overlap >= 0.75:
        return 0.0
    if overlap >= 0.35:
        return 0.35
    return 0.8


def score_candidate_against_angles(
    candidate: dict[str, Any] | None,
    angles: list[dict[str, Any]] | None,
) -> tuple[float, str | None]:
    if not isinstance(candidate, dict) or not angles:
        return 0.0, None
    candidate_text = " ".join(
        x for x in [candidate.get("headline") or "", candidate.get("topic") or "", candidate.get("summary") or ""] if x
    )
    candidate_tokens = token_set(candidate_text)
    if not candidate_tokens:
        return 0.0, None
    best_score = 0.0
    best_angle = None
    for angle in angles:
        if not isinstance(angle, dict):
            continue
        angle_text = " ".join(
            x for x in [angle.get("angle") or "", angle.get("headline_hint") or "", angle.get("why_this_angle") or ""] if x
        )
        angle_tokens = token_set(angle_text)
        score = _token_overlap(candidate_tokens, angle_tokens)
        if score > best_score:
            best_score = score
            best_angle = str(angle.get("angle") or angle.get("headline_hint") or "") or None
    return round(best_score, 4), best_angle


def extract_candidate_angle(candidate: dict[str, Any] | None) -> str | None:
    if not isinstance(candidate, dict):
        return None
    source_bundle = candidate.get("source_bundle")
    if isinstance(source_bundle, dict):
        matched = source_bundle.get("matched_angle")
        if isinstance(matched, str) and matched.strip():
            return matched.strip()
    matched = candidate.get("matched_angle")
    if isinstance(matched, str) and matched.strip():
        return matched.strip()
    for field in (candidate.get("headline"), candidate.get("topic")):
        if isinstance(field, str) and field.strip():
            return field.strip()
    return None


def canonical_angle_family(angle: str | None) -> str | None:
    if not isinstance(angle, str) or not angle.strip():
        return None
    tokens = [
        token
        for token in sorted(token_set(angle))
        if token not in {
            "news", "fresh", "daily", "update", "today", "headline", "новости", "свежие",
            "new", "another", "other", "next", "first", "second", "новый", "новая", "другой", "следующий",
        }
    ]
    if not tokens:
        fallback = re.sub(r"\s+", " ", angle.lower()).strip()
        return fallback[:120] or None
    return " ".join(tokens[:6])


def theme_labels_from_texts(texts: list[str | None], *, max_labels: int = 3) -> list[str]:
    stopwords = {
        "news", "fresh", "daily", "today", "update", "updates", "headline", "channel", "post",
        "новости", "свежие", "сегодня", "обновление", "канал", "пост",
    }
    counts: Counter[str] = Counter()
    for text in texts:
        for entity in _entity_tokens(text or ""):
            if len(entity) >= 4:
                counts[entity] += 2
        for token in token_set(text or ""):
            if len(token) < 5 or token in stopwords:
                continue
            counts[token] += 1
    return [label for label, _ in counts.most_common(max_labels)]


def build_review_hints(
    *,
    source_quality_summary: dict[str, Any] | None,
    scores: dict[str, Any] | None,
    risk_flags: list[str] | None,
) -> list[dict[str, Any]]:
    summary = source_quality_summary if isinstance(source_quality_summary, dict) else {}
    score_map = scores if isinstance(scores, dict) else {}
    flags = set(normalize_risk_flags(risk_flags))
    hints: list[dict[str, Any]] = []
    conflict_examples = summary.get("conflict_explanations")
    if isinstance(conflict_examples, list) and conflict_examples:
        hints.append(
            {
                "action": "verify_conflicting_sources",
                "priority": "high",
                "reason": "sources disagree on the same event",
                "details": conflict_examples[:2],
            }
        )
    if "possible_duplicate" in flags or "embedding_duplicate" in flags:
        hints.append(
            {
                "action": "compare_with_recent_publications",
                "priority": "high",
                "reason": "candidate may duplicate recent channel content",
            }
        )
    if "single_source" in flags or float(score_map.get("source_corroboration") or 0.0) < 0.35:
        hints.append(
            {
                "action": "seek_additional_source",
                "priority": "medium",
                "reason": "candidate relies on limited corroboration",
            }
        )
    if "repeated_angle" in flags or float(score_map.get("angle_pressure") or 0.0) > 0.34:
        hints.append(
            {
                "action": "consider_new_angle",
                "priority": "medium",
                "reason": "channel recently covered the same angle family",
            }
        )
    if not hints:
        hints.append(
            {
                "action": "review_for_editorial_fit",
                "priority": "low",
                "reason": "standard editorial review",
            }
        )
    return hints


def infer_workflow_preset(
    *,
    source_quality_summary: dict[str, Any] | None,
    scores: dict[str, Any] | None,
    risk_flags: list[str] | None,
    review_hints: list[dict[str, Any]] | None = None,
) -> str:
    summary = source_quality_summary if isinstance(source_quality_summary, dict) else {}
    score_map = scores if isinstance(scores, dict) else {}
    flags = set(normalize_risk_flags(risk_flags))
    hints = review_hints if isinstance(review_hints, list) else []
    hint_actions = {str(item.get("action") or "") for item in hints if isinstance(item, dict)}
    if "verify_conflicting_sources" in hint_actions or summary.get("conflict_explanations"):
        return "fact_check"
    if "compare_with_recent_publications" in hint_actions or "possible_duplicate" in flags or "embedding_duplicate" in flags:
        return "anti_duplicate"
    if "consider_new_angle" in hint_actions or float(score_map.get("angle_pressure") or 0.0) > 0.34:
        return "angle_refresh"
    if "seek_additional_source" in hint_actions or float(score_map.get("source_corroboration") or 0.0) < 0.35:
        return "evidence_boost"
    return "standard_review"


def suggest_review_decision(
    *,
    source_quality_summary: dict[str, Any] | None,
    scores: dict[str, Any] | None,
    risk_flags: list[str] | None,
    review_hints: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    summary = source_quality_summary if isinstance(source_quality_summary, dict) else {}
    score_map = scores if isinstance(scores, dict) else {}
    flags = set(normalize_risk_flags(risk_flags))
    hints = review_hints if isinstance(review_hints, list) else []
    hint_actions = {str(item.get("action") or "") for item in hints if isinstance(item, dict)}
    if "compare_with_recent_publications" in hint_actions or "possible_duplicate" in flags or "embedding_duplicate" in flags:
        return {"decision": "rejected", "review_action": "reject_duplicate"}
    if summary.get("conflict_explanations") and float(score_map.get("source_conflict") or 0.0) >= 0.65:
        return {"decision": "rejected", "review_action": "reject_conflict"}
    if "seek_additional_source" in hint_actions and float(score_map.get("source_quality") or 0.0) < 0.3:
        return {"decision": "rejected", "review_action": "reject_low_quality"}
    if "verify_conflicting_sources" in hint_actions:
        return {"decision": "approved", "review_action": "approve_after_fact_check"}
    if "consider_new_angle" in hint_actions:
        return {"decision": "approved", "review_action": "approve_after_new_angle"}
    return {"decision": "approved", "review_action": "approve_as_is"}


def review_action_from_hints(
    *,
    decision: str,
    review_hints: list[dict[str, Any]] | None,
) -> str:
    hints = review_hints if isinstance(review_hints, list) else []
    actions = {str(item.get("action") or "") for item in hints if isinstance(item, dict)}
    if decision == "approved":
        if "verify_conflicting_sources" in actions:
            return "approve_after_fact_check"
        if "consider_new_angle" in actions:
            return "approve_after_new_angle"
        return "approve_as_is"
    if "compare_with_recent_publications" in actions:
        return "reject_duplicate"
    if "verify_conflicting_sources" in actions:
        return "reject_conflict"
    if "seek_additional_source" in actions:
        return "reject_low_quality"
    return "reject_off_strategy"


def historical_angle_pressure(
    session: Session,
    *,
    tenant_id: str,
    channel_id: str,
    matched_angle: str | None,
    exclude_candidate_id: str | None = None,
    limit: int = 40,
) -> dict[str, Any]:
    if not isinstance(matched_angle, str) or not matched_angle.strip():
        return {"angle": None, "angle_hash": None, "angle_family": None, "recent_match_count": 0, "pressure": 0.0}
    angle_hash = fingerprint_text(matched_angle) or matched_angle.strip().lower()
    angle_family = canonical_angle_family(matched_angle)
    stmt = (
        select(ContentCandidateOrm)
        .where(
            ContentCandidateOrm.tenant_id == tenant_id,
            ContentCandidateOrm.channel_id == channel_id,
            ContentCandidateOrm.status.in_(("approved", "converted")),
        )
        .order_by(ContentCandidateOrm.created_at.desc())
        .limit(limit)
    )
    if exclude_candidate_id:
        stmt = stmt.where(ContentCandidateOrm.id != exclude_candidate_id)
    rows = list(session.scalars(stmt).all())
    history_total = len(rows)
    recent_match_count = 0
    family_match_count = 0
    for row in rows:
        source_bundle = _safe_json(row.source_bundle_json)
        candidate_angle = None
        if isinstance(source_bundle, dict):
            raw = source_bundle.get("matched_angle")
            if isinstance(raw, str) and raw.strip():
                candidate_angle = raw.strip()
        if not candidate_angle:
            draft = _safe_json(row.draft_json)
            if isinstance(draft, dict):
                raw = draft.get("matched_angle") or draft.get("headline") or draft.get("topic")
                if isinstance(raw, str) and raw.strip():
                    candidate_angle = raw.strip()
        candidate_hash = fingerprint_text(candidate_angle or "") or (candidate_angle or "").strip().lower()
        candidate_family = canonical_angle_family(candidate_angle)
        if candidate_hash and candidate_hash == angle_hash:
            recent_match_count += 1
        if angle_family and candidate_family == angle_family:
            family_match_count += 1
    pressure = round((recent_match_count * 0.45 + family_match_count * 0.55) / max(history_total, 1), 4) if history_total else 0.0
    return {
        "angle": matched_angle.strip(),
        "angle_hash": angle_hash,
        "angle_family": angle_family,
        "recent_match_count": recent_match_count,
        "family_match_count": family_match_count,
        "history_total": history_total,
        "pressure": pressure,
    }


def _contradiction_signal(
    left_text: str,
    right_text: str,
    left_tokens: set[str],
    right_tokens: set[str],
    *,
    numeric_divergence: float,
) -> float:
    shared_context = _token_overlap(left_tokens, right_tokens)
    if shared_context < 0.15:
        return 0.0
    polarity_pairs = [
        ({"increase", "grew", "growth", "rose", "up", "boost", "expanded", "open", "approved", "launch", "won", "побед", "рост", "откры"}, {"decrease", "fell", "drop", "down", "cut", "closed", "blocked", "delay", "cancel", "loss", "decline", "снижен", "закры", "отмен", "проиг"}),
        ({"confirmed", "official", "valid", "true", "стало известно", "подтверд"}, {"denied", "fake", "false", "rumor", "опроверг", "ложн"}),
    ]
    score = 0.0
    for positive, negative in polarity_pairs:
        left_positive = any(token in left_text for token in positive)
        right_positive = any(token in right_text for token in positive)
        left_negative = any(token in left_text for token in negative)
        right_negative = any(token in right_text for token in negative)
        if (left_positive and right_negative) or (left_negative and right_positive):
            score = max(score, 0.65)
    if any(token in left_text for token in ("not", "no ", "without ", "нет ", "не ")) != any(
        token in right_text for token in ("not", "no ", "without ", "нет ", "не ")
    ):
        score = max(score, 0.45)
    if numeric_divergence >= 0.8 and shared_context >= 0.35:
        score = max(score, 0.75)
    return min(score, 1.0)


def _entity_tokens(text: str) -> set[str]:
    candidates = re.findall(r"\b[A-ZА-ЯЁ][a-zа-яё]{2,}\b", text or "")
    lowered = {token.lower() for token in candidates if len(token) >= 3}
    lowered.update(token for token in token_set(text) if token in {"novosibirsk", "новосибирск", "обь", "metro", "bridge"})
    return lowered


def _event_tokens(text: str) -> set[str]:
    vocabulary = {
        "open", "opened", "opening", "launch", "launched", "close", "closed", "cancel", "cancelled", "approve", "approved",
        "confirm", "confirmed", "deny", "denied", "increase", "decrease", "expand", "expanded", "build", "built", "delay", "delayed",
        "откры", "закры", "отмен", "одобр", "подтверд", "опроверг", "рост", "снижен", "запуск", "строит",
    }
    return {token for token in token_set(text) if any(token.startswith(prefix) for prefix in vocabulary)}


def _entity_event_consistency_score(
    left_entities: set[str],
    right_entities: set[str],
    left_events: set[str],
    right_events: set[str],
) -> float:
    entity_overlap = _token_overlap(left_entities, right_entities)
    event_overlap = _token_overlap(left_events, right_events)
    if entity_overlap >= 0.35 and left_events and right_events and event_overlap == 0.0:
        return 1.0
    if entity_overlap >= 0.35 and event_overlap <= 0.1:
        return 0.8
    if entity_overlap >= 0.2 and event_overlap <= 0.2:
        return 0.45
    return 0.0
