"""Collect bounded provider observations keyed by actual publication receipts."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
import logging
import os
from uuid import uuid4

import httpx
from sqlalchemy import BigInteger, and_, case, cast, func, or_, select
from sqlalchemy.orm import Session

from postbridge.domain.errors import ConfigurationError
from postbridge.integrations.analytics import AnalyticsUnavailable, Observation
from postbridge.integrations.analytics_links import publication_link
from postbridge.integrations.bluesky.analytics import read_many as bluesky
from postbridge.integrations.bluesky.analytics import read_audience as bluesky_audience
from postbridge.integrations.channel_credentials import load_channel_credential_row
from postbridge.integrations.facebook.analytics import read_many as facebook
from postbridge.integrations.instagram.analytics import read_many as instagram
from postbridge.integrations.linkedin.analytics import read_many as linkedin
from postbridge.integrations.mastodon.analytics import read_many as mastodon
from postbridge.integrations.mastodon.analytics import read_audience as mastodon_audience
from postbridge.integrations.max.analytics import read_many as max_metrics
from postbridge.integrations.max.analytics import read_audience as max_audience
from postbridge.integrations.platform_policy import disabled_platforms
from postbridge.integrations.registry import decode_publish_credentials_for_platform
from postbridge.integrations.telegram.analytics import read_many as telegram
from postbridge.integrations.tiktok.analytics import read_many as tiktok
from postbridge.integrations.vk.analytics import read_many as vk
from postbridge.integrations.x.analytics import read_many as x
from postbridge.integrations.youtube.analytics import read_many as youtube
from postbridge.integrations.youtube.analytics import read_audience as youtube_audience
from postbridge.models.domain import (
    ChannelAudienceOrm, ChannelCredentialOrm, ChannelOrm, ContentItemOrm, PublicationAnalyticsOrm,
    PublicationPlanOrm, PublicationTargetOrm,
)

logger = logging.getLogger(__name__)
READERS = {
    "youtube": (youtube, 50), "max": (max_metrics, 100), "mastodon": (mastodon, 1),
    "bluesky": (bluesky, 25), "facebook": (facebook, 1), "instagram": (instagram, 1),
    "x": (x, 100), "vk": (vk, 25), "telegram": (telegram, 20),
    "linkedin": (linkedin, 1), "tiktok": (tiktok, 20),
}
METRIC_NAMES = frozenset({
    "views", "impressions", "reach", "likes", "reactions", "comments", "reposts",
    "shares", "quotes", "bookmarks", "saves",
})
AUDIENCE_READERS = {"youtube": youtube_audience, "max": max_audience,
                    "mastodon": mastodon_audience, "bluesky": bluesky_audience}
RETENTION = timedelta(days=30)
DUE_AGE = timedelta(hours=1)
LEASE_TIME = timedelta(minutes=5)
PUBLIC = {"mastodon", "bluesky"}


def analytics_enabled():
    default = "0" if os.environ.get("POSTBRIDGE_APP_MODE", "selfhost") == "selfhost" else "1"
    return os.environ.get("POSTBRIDGE_PUBLICATION_ANALYTICS_ENABLED", default).lower() in {"true", "1", "yes"}


def x_daily_limit():
    value = os.environ.get("POSTBRIDGE_PUBLICATION_ANALYTICS_X_DAILY_LIMIT", "")
    return min(int(value), 10000) if value.isascii() and value.isdigit() and len(value) <= 8 else 0


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def midnight(value):
    return datetime.combine(value.date(), time.min, UTC)


def _history(values, now):
    return [sample for sample in values if datetime.fromisoformat(sample["collected_at"]) > now - RETENTION]


def _store_history(state, values):
    state.history = values
    state.history_expires_at = datetime.fromisoformat(values[-1]["collected_at"]) + RETENTION if values else None


@dataclass(frozen=True)
class Claim:
    target_id: str
    tenant_id: str
    platform: str
    channel_id: str
    post_id: str
    token: str
    published_at: datetime


def _collectable_platforms(x_limit):
    platforms = set(READERS) - set(disabled_platforms())
    if not x_limit:
        platforms.discard("x")
    if os.environ.get("POSTBRIDGE_TELEGRAM_ANALYTICS_ENABLED", "0").lower() not in {"true", "1", "yes"}:
        platforms.discard("telegram")
    return platforms


def _has_access(platform=PublicationTargetOrm.platform):
    return and_(ChannelOrm.status == "connected", or_(
        platform.in_(PUBLIC),
        select(ChannelCredentialOrm.id).where(
            ChannelCredentialOrm.channel_id == ChannelOrm.id,
            ChannelCredentialOrm.tenant_id == ChannelOrm.tenant_id,
            ChannelCredentialOrm.status == "active",
        ).exists(),
    ))


def _cleanup(session, now):
    revoked = or_(~_has_access(), PublicationTargetOrm.status != "published",
                  PublicationTargetOrm.external_post_id.is_distinct_from(PublicationAnalyticsOrm.external_post_id))
    rows = session.execute(
        select(PublicationAnalyticsOrm, revoked.label("revoked"))
        .join(PublicationTargetOrm, PublicationTargetOrm.id == PublicationAnalyticsOrm.target_id)
        .join(ChannelOrm, ChannelOrm.id == PublicationTargetOrm.channel_id)
        .where(PublicationAnalyticsOrm.history_expires_at.is_not(None),
               or_(PublicationAnalyticsOrm.history_expires_at <= now, revoked))
        .limit(200)
        .with_for_update(of=PublicationAnalyticsOrm, skip_locked=True)
    ).all()
    for state, is_revoked in rows:
        _store_history(state, [] if is_revoked else _history(state.history, now))


def claim_due(session: Session, now: datetime, *, limit: int = 16) -> list[Claim]:
    if session.bind.dialect.name == "postgresql":
        if not session.scalar(select(func.pg_try_advisory_xact_lock(154622381))):
            return []
    _cleanup(session, now)
    if not analytics_enabled():
        return []
    x_limit = x_daily_limit()
    platforms = _collectable_platforms(x_limit)
    if x_limit:
        charged = session.scalar(select(func.count()).select_from(PublicationAnalyticsOrm)
            .join(PublicationTargetOrm, PublicationTargetOrm.id == PublicationAnalyticsOrm.target_id)
            .where(PublicationTargetOrm.platform == "x", PublicationAnalyticsOrm.attempted_at >= midnight(now)))
        x_limit = max(0, x_limit - charged)
        if not x_limit:
            platforms.discard("x")
    target = PublicationTargetOrm
    state = PublicationAnalyticsOrm
    published_at = func.coalesce(target.published_at, target.created_at)
    base = select(target, state).join(ChannelOrm, and_(ChannelOrm.id == target.channel_id,
                 ChannelOrm.tenant_id == target.tenant_id, ChannelOrm.platform == target.platform)).outerjoin(
                 state, state.target_id == target.id).where(
        target.status == "published", target.external_post_id.is_not(None), target.external_post_id != "",
        ChannelOrm.status == "connected", target.platform.in_(platforms), published_at <= now - DUE_AGE,
        or_(state.target_id.is_(None), state.external_post_id != target.external_post_id, state.next_attempt_at <= now),
    )
    claims = []
    # Separate the paid pool so exhausting its cap cannot starve other platforms.
    recent = now - RETENTION
    backfill = max(1, limit // 4) if limit > 1 else 0
    for pool, maximum in ((base.where(target.platform != "x", published_at > recent), limit - backfill),
                          (base.where(target.platform != "x", published_at <= recent), backfill),
                          (base.where(target.platform == "x", or_(state.attempted_at.is_(None),
                                                                   state.attempted_at < midnight(now))), min(x_limit, limit))):
        if maximum < 1:
            continue
        rows = session.execute(pool.order_by(func.coalesce(state.next_attempt_at, published_at), target.id)
                               .limit(maximum).with_for_update(of=target, skip_locked=True)).all()
        for receipt, run in rows:
            if run is None:
                run = PublicationAnalyticsOrm(target_id=receipt.id, history=[])
                session.add(run)
            if run.external_post_id != receipt.external_post_id:
                _store_history(run, [])
                run.status, run.detail = "pending", None
            run.external_post_id = receipt.external_post_id
            run.attempted_at = now
            run.claim_id = str(uuid4())
            run.next_attempt_at = midnight(now) + timedelta(days=1) if receipt.platform == "x" else now + LEASE_TIME
            claims.append(Claim(receipt.id, receipt.tenant_id, receipt.platform, receipt.channel_id,
                                receipt.external_post_id, run.claim_id, utc(receipt.published_at or receipt.created_at)))
    return claims


def _connection(session, tenant_id, platform, channel_id):
    channel = session.get(ChannelOrm, channel_id)
    if not channel or (channel.tenant_id, channel.platform, channel.status) != (tenant_id, platform, "connected"):
        raise AnalyticsUnavailable("permission_required")
    credential = load_channel_credential_row(session, channel_id, tenant_id)
    if platform not in PUBLIC and (credential is None or credential.status != "active"):
        raise AnalyticsUnavailable("permission_required")
    decoded = decode_publish_credentials_for_platform(platform, credential) if credential and credential.status == "active" else None
    return channel.external_id or "", decoded


def save_sample(session, claim, observation, now, *, retry_after=None):
    run = session.scalar(select(PublicationAnalyticsOrm).where(PublicationAnalyticsOrm.target_id == claim.target_id)
                         .with_for_update())
    receipt = session.get(PublicationTargetOrm, claim.target_id)
    if run is None or run.claim_id != claim.token or not receipt:
        return
    run.claim_id = None
    if receipt.status != "published" or receipt.external_post_id != claim.post_id:
        _store_history(run, [])
        run.next_attempt_at = None
        return
    try:
        _connection(session, claim.tenant_id, claim.platform, claim.channel_id)
    except AnalyticsUnavailable:
        observation = Observation({}, "unavailable", "permission_required")
    values = _history(run.history, now)
    run.status, run.detail = observation.status, observation.detail
    if run.detail in {"permission_required", "post_not_accessible"} and not observation.metrics:
        values = []
    if observation.metrics:
        sample = {"collected_at": now.isoformat(), "metrics": observation.metrics}
        if observation.url:
            sample["url"] = observation.url
        if values and datetime.fromisoformat(values[0]["collected_at"]).date() == now.date():
            values[0] = sample
        else:
            values.insert(0, sample)
    _store_history(run, values)
    if claim.platform == "x":
        run.next_attempt_at = max(midnight(now) + timedelta(days=1), now + (retry_after or timedelta())) if (
            not values or now - claim.published_at < RETENTION) else None
    elif observation.detail in {"rate_limited", "temporarily_unavailable", "invalid_response"}:
        run.next_attempt_at = now + (retry_after or timedelta(hours=1))
    elif observation.detail in {"permission_required", "endpoint_not_enabled"} or now - claim.published_at < RETENTION:
        run.next_attempt_at = midnight(now) + timedelta(days=1)
    else:
        run.next_attempt_at = None


def collect_due(session_factory, *, now=None):
    observed_at = now or (lambda: datetime.now(UTC))
    with session_factory.begin() as session:
        claims = claim_due(session, observed_at())
    groups = defaultdict(list)
    for claim in claims:
        groups[(claim.tenant_id, claim.platform, claim.channel_id)].append(claim)
    with httpx.Client(timeout=httpx.Timeout(7.0, connect=4.0), follow_redirects=False) as client:
        for (_, platform, _), group in groups.items():
            reader, batch_size = READERS[platform]
            for start in range(0, len(group), batch_size):
                batch = group[start:start + batch_size]
                retry_after = None
                try:
                    with session_factory() as session:
                        first = batch[0]
                        label, credentials = _connection(session, first.tenant_id, first.platform, first.channel_id)
                    results = reader(client, credentials, list(dict.fromkeys(job.post_id for job in batch)), label)
                    fallback = Observation({}, "unavailable", "post_not_accessible")
                except AnalyticsUnavailable as exc:
                    results, fallback, retry_after = {}, Observation({}, "unavailable", exc.reason), exc.retry_after
                except ConfigurationError:
                    results, fallback = {}, Observation({}, "unavailable", "permission_required")
                except (ValueError, TypeError, KeyError, AttributeError):
                    results, fallback = {}, Observation({}, "error", "invalid_response")
                except (httpx.RequestError, TimeoutError):
                    results, fallback = {}, Observation({}, "error", "temporarily_unavailable")
                except Exception as exc:
                    logger.warning("publication analytics provider error platform=%s type=%s", platform, type(exc).__name__)
                    results, fallback = {}, Observation({}, "error", "temporarily_unavailable")
                with session_factory.begin() as session:
                    for job in batch:
                        save_sample(session, job, results.get(job.post_id, fallback), observed_at(), retry_after=retry_after)
    return len(claims)


def collect_audiences(session_factory, *, now=None):
    now = now or datetime.now(UTC)
    state = ChannelAudienceOrm
    claims = []
    with session_factory.begin() as session:
        is_accessible = and_(_has_access(ChannelOrm.platform), ChannelOrm.external_id == state.external_id)
        expiring = session.execute(select(state, is_accessible.label("accessible")).join(ChannelOrm)
            .where(state.history_expires_at.is_not(None), or_(state.history_expires_at <= now, ~is_accessible))
            .limit(200).with_for_update(of=state, skip_locked=True)).all()
        for row, accessible in expiring:
            _store_history(row, _history(row.history, now) if accessible else [])
        if not analytics_enabled():
            return 0
        has_receipt = select(PublicationTargetOrm.id).where(
            PublicationTargetOrm.channel_id == ChannelOrm.id,
            PublicationTargetOrm.tenant_id == ChannelOrm.tenant_id,
            PublicationTargetOrm.status == "published",
        ).exists()
        due = session.execute(select(ChannelOrm, state).outerjoin(state).where(
            _has_access(ChannelOrm.platform), ChannelOrm.external_id.is_not(None), ChannelOrm.external_id != "", has_receipt,
            ChannelOrm.platform.in_(_collectable_platforms(0) & set(AUDIENCE_READERS)),
            or_(state.channel_id.is_(None), state.external_id != ChannelOrm.external_id, state.next_attempt_at <= now),
        ).order_by(func.coalesce(state.next_attempt_at, ChannelOrm.created_at), ChannelOrm.id)
          .limit(12).with_for_update(of=ChannelOrm, skip_locked=True)).all()
        for channel, row in due:
            if row is None:
                row = ChannelAudienceOrm(channel_id=channel.id, history=[])
                session.add(row)
            if row.external_id != channel.external_id:
                row.external_id, row.status, row.detail = channel.external_id, "pending", None
                _store_history(row, [])
            row.claim_id = str(uuid4())
            row.next_attempt_at = now + LEASE_TIME
            claims.append((channel.id, channel.tenant_id, channel.platform, channel.external_id, row.claim_id))
    with httpx.Client(timeout=httpx.Timeout(7.0, connect=4.0), follow_redirects=False) as client:
        for channel_id, tenant, platform, identity, claim_id in claims:
            retry = None
            try:
                with session_factory() as session:
                    label, credentials = _connection(session, tenant, platform, channel_id)
                if label != identity:
                    raise AnalyticsUnavailable("post_not_accessible")
                result = AUDIENCE_READERS[platform](client, credentials, label)
            except AnalyticsUnavailable as error:
                result, retry = Observation({}, "unavailable", error.reason), error.retry_after
            except ConfigurationError:
                result = Observation({}, "unavailable", "permission_required")
            except Exception as error:
                logger.warning("channel audience provider error platform=%s type=%s", platform, type(error).__name__)
                result = Observation({}, "error", "temporarily_unavailable")
            with session_factory.begin() as session:
                row = session.scalar(select(state).where(state.channel_id == channel_id).with_for_update())
                channel = session.get(ChannelOrm, channel_id)
                if not row or row.claim_id != claim_id or not channel or channel.external_id != identity:
                    continue
                try:
                    _connection(session, tenant, platform, channel_id)
                except AnalyticsUnavailable:
                    result = Observation({}, "unavailable", "permission_required")
                row.claim_id = None
                row.status, row.detail = result.status, result.detail
                row.next_attempt_at = max(midnight(now) + timedelta(days=1), now + (retry or timedelta()))
                history = _history(row.history, now)
                if result.detail in {"permission_required", "post_not_accessible"}:
                    history = []
                if result.metrics:
                    history.insert(0, {"collected_at": now.isoformat(), "metrics": result.metrics})
                _store_history(row, history)
    return len(claims)


def _audience_report(session, tenant_id, now, days, platform, channel_id):
    state = ChannelAudienceOrm
    query = select(state, ChannelOrm).join(ChannelOrm).where(
        ChannelOrm.tenant_id == tenant_id, _has_access(ChannelOrm.platform), ChannelOrm.external_id == state.external_id,
    )
    if platform:
        query = query.where(ChannelOrm.platform == platform)
    if channel_id:
        query = query.where(ChannelOrm.id == channel_id)
    rows = session.execute(query.order_by(ChannelOrm.title, ChannelOrm.id).limit(50)).all()
    result = []
    for row, channel in rows:
        history = [sample for sample in _history(row.history, now)
                   if datetime.fromisoformat(sample["collected_at"]) >= now - timedelta(days=days)]
        result.append({"id": channel.id, "platform": channel.platform, "channel_title": channel.title,
                       "status": row.status, "detail": row.detail, "latest": history[0] if history else None,
                       "history": history})
    return result


def report(session, tenant_id, *, days, offset, limit, platform=None, channel_id=None, content_id=None,
           content_format=None, metric=None, now=None):
    now = now or datetime.now(UTC)
    target, plan, item, state = PublicationTargetOrm, PublicationPlanOrm, ContentItemOrm, PublicationAnalyticsOrm
    when = func.coalesce(target.published_at, target.created_at)
    is_video = or_(target.platform.in_(("tiktok", "youtube")),
                   *(func.lower(func.coalesce(item.media_url, "")).like(pattern)
                     for pattern in ("%.mp4%", "%.mov%", "%.webm%", "%.m4v%")))
    filters = [target.tenant_id == tenant_id, target.status == "published"]
    if not content_id:
        filters.append(when >= now - timedelta(days=days))
    for given, column in ((platform, target.platform), (channel_id, target.channel_id), (content_id, item.id)):
        if given:
            filters.append(column == given)
    if content_format:
        filters.append(is_video if content_format == "video" else ~is_video)
    query = select(target, item, ChannelOrm, state, is_video.label("is_video"), _has_access().label("has_access")).join(
        plan, and_(plan.id == target.publication_plan_id, plan.tenant_id == tenant_id)).join(
        item, and_(item.id == plan.content_item_id, item.tenant_id == tenant_id)).join(
        ChannelOrm, and_(ChannelOrm.id == target.channel_id, ChannelOrm.tenant_id == tenant_id)).outerjoin(
        state, and_(state.target_id == target.id, state.external_post_id == target.external_post_id)).where(*filters)
    if metric in METRIC_NAMES and platform:
        last_value = cast(state.history[0]["metrics"][metric].as_string(), BigInteger)
        score = case((and_(_has_access(), state.history_expires_at > now), last_value))
        query = query.order_by(score.desc().nulls_last())
    rows = session.execute(query.order_by(when.desc(), target.id).offset(offset).limit(limit + 1)).all()
    channels = session.execute(select(ChannelOrm.id, ChannelOrm.title, ChannelOrm.platform)
        .join(target, and_(target.channel_id == ChannelOrm.id, target.tenant_id == tenant_id))
        .where(ChannelOrm.tenant_id == tenant_id, target.status == "published").distinct()
        .order_by(ChannelOrm.title, ChannelOrm.id)).all()
    output = []
    supported = _collectable_platforms(x_daily_limit())
    enabled = analytics_enabled()
    for receipt, content, channel, run, video, has_access in rows[:limit]:
        samples = _history(run.history, now) if run and has_access else []
        status, detail = "pending", None
        if not enabled or receipt.platform not in supported:
            status, detail = "unavailable", "collection_disabled" if receipt.platform in READERS else "unsupported"
        elif not has_access:
            status, detail = "unavailable", "permission_required"
        elif not receipt.external_post_id:
            status, detail = "unavailable", "missing_post_id"
        elif run:
            status, detail = (run.status, run.detail) if samples or run.status not in {"available", "partial"} else ("unavailable", "expired")
        output.append({"id": receipt.id, "content_id": content.id, "title": content.title,
                       "channel_id": channel.id, "channel_title": channel.title, "platform": receipt.platform,
                       "format": "video" if video else "post",
                       "url": receipt.external_url or (samples[0].get("url") if samples else None)
                       or publication_link(receipt.platform, channel.external_id or "",
                                                                         receipt.external_post_id or ""),
                       "published_at": utc(receipt.published_at or receipt.created_at).isoformat(),
                       "status": status, "detail": detail,
                       "latest": samples[0] if samples else None, "history": samples})
    return {"items": output, "has_more": len(rows) > limit,
            "audience": _audience_report(session, tenant_id, now, days, platform, channel_id) if not content_id else [],
            "channels": [{"id": key, "title": title, "platform": kind} for key, title, kind in channels]}
