from datetime import UTC, datetime, timedelta
import json

import httpx
import pytest
from sqlalchemy import select

from postbridge.infrastructure.crypto.credentials import encrypt_credential_secret
from postbridge.models.domain import (ChannelCredentialOrm, ChannelOrm, ContentItemOrm,
    PublicationTargetOrm, VideoDeliveryStateOrm, VideoPublicationApprovalOrm)
from postbridge.services.youtube_data import maintain_connection
from tests.test_video_studio import state
from tests.test_video_post_drafts import ready
from tests.test_video_publication import publication, approval


@pytest.fixture
def connected(publication):
    db, tenant, other, post_id, channels = publication
    view, _ = approval(publication)
    db.connection().exec_driver_sql('PRAGMA foreign_keys=ON')
    channel = channels[0]
    channel.platform, channel.external_id, channel.title = 'youtube', 'UC' + 'a' * 22, 'Old API title'
    credential = db.scalar(select(ChannelCredentialOrm).where(ChannelCredentialOrm.channel_id == channel.id))
    credential.encrypted_secret = encrypt_credential_secret(json.dumps({
        'refresh_token': 'private-refresh', 'client_id': 'client', 'client_secret': 'private-secret'}))
    grant = db.get(VideoPublicationApprovalOrm, view['approval_id'])
    destinations = grant.snapshot['destinations']
    grant.snapshot = {**grant.snapshot, 'destinations': {**destinations, channel.id: {
        **destinations[channel.id], 'platform': 'youtube', 'slug': channel.external_id,
        'title': channel.title, 'options': {'title': 'User approved video title'}}}}
    db.commit()
    return db, channel, post_id, grant, channels[1]


def google(monkeypatch, *, revoked=False, unavailable=False):
    original = httpx.Client
    def handler(request):
        if unavailable:
            return httpx.Response(503)
        if request.method == 'POST':
            assert request.url == 'https://oauth2.googleapis.com/token'
            if revoked:
                return httpx.Response(400, json={'error': 'invalid_grant'})
            return httpx.Response(200, json={'access_token': 'access'})
        assert request.headers['authorization'] == 'Bearer access'
        return httpx.Response(200, json={'items': [{'id': 'UC' + 'a' * 22, 'snippet': {'title': 'New API title'}}]})
    class Client(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, 'Client', Client)


def test_refresh_updates_display_metadata_without_changing_approved_content(connected, monkeypatch):
    db, channel, post_id, grant, other_channel = connected
    other = grant.snapshot['destinations'][other_channel.id].copy()
    google(monkeypatch)
    result = maintain_connection(db, channel)
    db.commit()
    assert result == {'status': 'active', 'title': 'New API title'}
    assert channel.title == grant.snapshot['destinations'][channel.id]['title'] == 'New API title'
    assert grant.snapshot['destinations'][channel.id]['options']['title'] == 'User approved video title'
    assert grant.snapshot['destinations'][other_channel.id] == other
    assert 'private-' not in str(result) + channel.config_json


def test_revocation_erases_api_data_tokens_and_receipts_but_preserves_source(connected, monkeypatch):
    db, channel, post_id, grant, other_channel = connected
    channel_id = channel.id
    google(monkeypatch, revoked=True)
    assert maintain_connection(db, channel) == {'status': 'disconnected'}
    db.commit()
    assert db.get(ChannelOrm, channel_id) is None
    assert db.scalar(select(ChannelCredentialOrm).where(ChannelCredentialOrm.channel_id == channel_id)) is None
    assert db.scalar(select(PublicationTargetOrm).where(PublicationTargetOrm.channel_id == channel_id)) is None
    assert channel_id not in grant.snapshot['destinations']
    assert db.get(ContentItemOrm, post_id).body_markdown == 'Exact approved text'
    assert db.get(ChannelOrm, other_channel.id) is not None


@pytest.mark.parametrize('days_old,expected', [(1, 'deferred'), (30, 'disconnected')])
def test_provider_outage_retries_recent_data_and_purges_expired_data(connected, monkeypatch, days_old, expected):
    db, channel, _, _, _ = connected
    channel.config_json = json.dumps({'youtube_data_checked_at': (datetime.now(UTC) - timedelta(days=days_old)).isoformat()})
    google(monkeypatch, unavailable=True)
    assert maintain_connection(db, channel)['status'] == expected
    db.commit()


def test_expired_upload_session_cannot_be_used_for_a_new_upload(connected, monkeypatch):
    db, channel, _, _, _ = connected
    target = db.scalar(select(PublicationTargetOrm).where(PublicationTargetOrm.channel_id == channel.id))
    target.created_at = datetime.now(UTC) - timedelta(days=30)
    target.updated_at = target.created_at
    target.status, target.external_post_id, target.external_url = 'unknown', 'api-video-id', 'https://youtube.com/watch?v=api-video-id'
    db.add(VideoDeliveryStateOrm(target_id=target.id, encrypted_state='sensitive-provider-session', updated_at=target.created_at))
    db.commit()
    google(monkeypatch, unavailable=True)
    assert maintain_connection(db, channel)['status'] == 'deferred'
    db.commit()
    assert target.status == 'failed' and target.error_code == 'YOUTUBE_DATA_EXPIRED'
    assert target.external_post_id is None and target.external_url is None
    assert db.get(VideoDeliveryStateOrm, target.id) is None


def test_old_approval_without_provider_data_keeps_its_future_schedule(connected, monkeypatch):
    db, channel, _, _, _ = connected
    target = db.scalar(select(PublicationTargetOrm).where(PublicationTargetOrm.channel_id == channel.id))
    target.created_at = datetime.now(UTC) - timedelta(days=30)
    target.scheduled_at = datetime.now(UTC) + timedelta(days=5)
    db.commit()
    google(monkeypatch)
    assert maintain_connection(db, channel)['status'] == 'active'
    db.commit()
    assert target.status == 'pending'
