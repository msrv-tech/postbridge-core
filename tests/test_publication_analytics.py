from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from postbridge.db import Base
from postbridge.integrations.analytics import AnalyticsUnavailable, Observation, counts, payload
from postbridge.integrations.bluesky.analytics import read_many as bluesky
from postbridge.integrations.facebook.analytics import read_many as facebook
from postbridge.integrations.mastodon.analytics import read_many as mastodon
from postbridge.models.domain import (
    ChannelCredentialOrm, ChannelOrm, ContentItemOrm, PublicationAnalyticsOrm, PublicationPlanOrm,
    PublicationTargetOrm, TenantOrm,
)
from postbridge.services.publication_analytics import claim_due, collect_audiences, collect_due, report, save_sample


NOW = datetime(2026, 11, 27, 10, tzinfo=UTC)


def seeded():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    with sessions.begin() as session:
        for owner, platform in (("one", "mastodon"), ("two", "bluesky"), ("three", "x")):
            session.add(TenantOrm(id=owner))
            session.flush()
            session.add(ChannelOrm(id=f"{owner}-channel", tenant_id=owner, platform=platform, kind="target",
                                   title=f"{owner} channel", status="connected", external_id="@sample@mastodon.social"))
            session.add(ContentItemOrm(id=f"{owner}-post", tenant_id=owner, source_type="manual",
                                       title=f"{owner} title", status="published"))
            session.flush()
            session.add(PublicationPlanOrm(id=f"{owner}-plan", tenant_id=owner, content_item_id=f"{owner}-post",
                                            strategy="direct", status="published"))
            session.flush()
            session.add(PublicationTargetOrm(id=f"{owner}-target", tenant_id=owner, channel_id=f"{owner}-channel",
                                               publication_plan_id=f"{owner}-plan", platform=platform,
                                               published_at=NOW-timedelta(hours=3), status="published", external_post_id="1234"))
    return sessions


def test_receipts_are_collected_once_per_day_and_report_is_tenant_scoped(monkeypatch):
    sessions = seeded()
    monkeypatch.setenv("POSTBRIDGE_PUBLICATION_ANALYTICS_ENABLED", "1")
    monkeypatch.delenv("POSTBRIDGE_PUBLICATION_ANALYTICS_X_DAILY_LIMIT", raising=False)
    from postbridge.services.publication_analytics import READERS
    monkeypatch.setitem(READERS, "mastodon", (lambda client, creds, ids, channel: {"1234": Observation({"likes": 0})}, 10))
    monkeypatch.setitem(READERS, "bluesky", (lambda client, creds, ids, channel: {"1234": Observation({"likes": 25})}, 10))
    assert collect_due(sessions, now=lambda: NOW) == 2
    assert collect_due(sessions, now=lambda: NOW+timedelta(hours=1)) == 0
    assert collect_due(sessions, now=lambda: NOW+timedelta(days=1)) == 2
    with sessions() as session:
        result = report(session, "one", days=30, offset=0, limit=10, platform="mastodon", metric="likes", now=NOW+timedelta(days=1))
        [item] = result["items"]
        assert len(item["history"]) == 2 and item["latest"]["metrics"] == {"likes": 0}
        assert result["channels"] == [{"id": "one-channel", "title": "one channel", "platform": "mastodon"}]
        assert item["url"] == "https://mastodon.social/@sample/1234"
        assert report(session, "one", days=30, offset=0, limit=10, content_id="two-post", now=NOW)["items"] == []
        assert session.get(PublicationAnalyticsOrm, "three-target") is None


def test_replaced_receipt_cannot_keep_old_counts(monkeypatch):
    sessions = seeded()
    monkeypatch.setenv("POSTBRIDGE_PUBLICATION_ANALYTICS_ENABLED", "1")
    with sessions.begin() as session:
        first = next(run for run in claim_due(session, NOW) if run.tenant_id == "one")
        save_sample(session, first, Observation({"likes": 21}), NOW)
    with sessions.begin() as session:
        session.get(PublicationTargetOrm, "one-target").external_post_id = "5678"
    with sessions.begin() as session:
        second = next(run for run in claim_due(session, NOW+timedelta(minutes=1)) if run.tenant_id == "one")
        assert second.post_id == "5678"
        save_sample(session, first, Observation({"likes": 100}), NOW)
        save_sample(session, second, Observation({"likes": 0}), NOW+timedelta(minutes=1))
    with sessions() as session:
        [item] = report(session, "one", days=30, offset=0, limit=10, now=NOW)["items"]
        assert [entry["metrics"] for entry in item["history"]] == [{"likes": 0}]


def test_revoked_read_access_is_hidden_and_erased(monkeypatch):
    sessions = seeded()
    monkeypatch.setenv("POSTBRIDGE_PUBLICATION_ANALYTICS_ENABLED", "1")
    monkeypatch.setenv("POSTBRIDGE_PUBLICATION_ANALYTICS_X_DAILY_LIMIT", "1")
    with sessions.begin() as session:
        session.add(ChannelCredentialOrm(id="cred", tenant_id="three", channel_id="three-channel",
                                         auth_type="bearer", status="active"))
    with sessions.begin() as session:
        claim = next(run for run in claim_due(session, NOW) if run.platform == "x")
        save_sample(session, claim, Observation({"impressions": 15}), NOW)
    with sessions.begin() as session:
        session.get(ChannelCredentialOrm, "cred").status = "revoked"
    with sessions.begin() as session:
        [item] = report(session, "three", days=30, offset=0, limit=10, now=NOW)["items"]
        assert item["latest"] is None and item["detail"] == "permission_required"
        claim_due(session, NOW+timedelta(minutes=1))
        assert session.get(PublicationAnalyticsOrm, "three-target").history == []


def test_public_adapters_require_the_right_author_and_preserve_zero():
    def app(request):
        if request.url.host == "mastodon.social":
            return httpx.Response(200, json={"id": "1", "account": {"acct": "sample"},
                                               "favourites_count": 0, "replies_count": 4})
        return httpx.Response(200, json={"posts": [
            {"uri": "at://did:plc:owner/app.bsky.feed.post/1", "author": {"handle": "sample.bsky.social"}, "likeCount": 0},
            {"uri": "at://did:plc:other/app.bsky.feed.post/2", "author": {"handle": "other.bsky.social"}, "likeCount": 5},
        ]})

    with httpx.Client(transport=httpx.MockTransport(app)) as client:
        assert mastodon(client, None, ["1"], "@sample@mastodon.social")["1"].metrics == {"likes": 0, "comments": 4}
        assert mastodon(client, None, ["1"], "@other@mastodon.social") == {}
        values = bluesky(client, None, ["at://did:plc:owner/app.bsky.feed.post/1", "at://did:plc:other/app.bsky.feed.post/2"], "sample.bsky.social")
        assert list(values.values()) == [Observation({"likes": 0})]


def test_provider_error_and_missing_metrics_are_distinct():
    assert counts({"a": 0, "b": False, "c": -1}, {"views": "a", "comments": "b", "likes": "c"}) == {"views": 0}
    with pytest.raises(AnalyticsUnavailable) as raised:
        payload(httpx.Response(429, headers={"Retry-After": "123"}))
    assert raised.value.reason == "rate_limited"
    assert raised.value.retry_after == timedelta(seconds=123)


def test_audience_history_stays_per_channel_and_only_collects_daily(monkeypatch):
    from postbridge.services.publication_analytics import AUDIENCE_READERS

    sessions = seeded()
    monkeypatch.setenv("POSTBRIDGE_PUBLICATION_ANALYTICS_ENABLED", "1")
    monkeypatch.setitem(AUDIENCE_READERS, "mastodon", lambda *args: Observation({"followers": 20}))
    monkeypatch.setitem(AUDIENCE_READERS, "bluesky", lambda *args: Observation({"followers": 80}))
    assert collect_audiences(sessions, now=NOW) == 2
    assert collect_audiences(sessions, now=NOW+timedelta(hours=1)) == 0
    assert collect_audiences(sessions, now=NOW+timedelta(days=1)) == 2
    with sessions() as session:
        [sample] = report(session, "one", days=30, offset=0, limit=10, now=NOW+timedelta(days=1))["audience"]
        assert sample["id"] == "one-channel"
        assert [entry["metrics"] for entry in sample["history"]] == [{"followers": 20}, {"followers": 20}]
        assert report(session, "one", days=30, offset=0, limit=10, channel_id="two-channel", now=NOW)["audience"] == []


def test_facebook_photo_receipt_falls_back_to_supported_fields():
    def transport(request):
        fields = request.url.params.get("fields", "")
        if "reactions" in fields:
            return httpx.Response(400, json={"error": {"code": 100}})
        if request.url.path.endswith("/insights"):
            assert request.url.params["metric"] == "post_media_view"
            return httpx.Response(200, json={"data": [{"name": "post_media_view", "values": [{"value": 30}]}]})
        assert "likes" in fields
        return httpx.Response(200, json={"id": "555", "likes": {"summary": {"total_count": 0}}})

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result = facebook(client, SimpleNamespace(graph_api_version="v25.0", page_access_token="test"), ["555"], "123")
    assert result["555"].metrics == {"likes": 0, "views": 30}
