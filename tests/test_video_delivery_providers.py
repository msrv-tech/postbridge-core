"""Provider contracts and lost-response recovery without external publishing."""
from contextlib import contextmanager
import json
from urllib.parse import parse_qs
from types import SimpleNamespace

import httpx
import pytest

from postbridge.api.schemas import InstagramCredentials, YouTubeCredentials
from postbridge.domain.errors import ExternalApiError
from postbridge.domain.models import PostPayload
from postbridge.integrations.instagram.publisher import InstagramPublisher
from postbridge.integrations.youtube.publisher import YouTubePublisher, session_url
from postbridge.services.video_delivery_state import DeliveryPending


class Delivery:
    def __init__(self, path):
        self.path, self.state, self.history = path, {}, []
        self.snapshot = {"byte_size": path.stat().st_size}
    def save(self, **updates):
        self.state.update(updates)
        self.history.append(dict(self.state))
    @contextmanager
    def media_file(self):
        yield self.path


@pytest.fixture
def delivery(tmp_path):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'0123456789')
    return Delivery(path)


def transport(monkeypatch, handler):
    original = httpx.Client
    class Client(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, 'Client', Client)


def payload(format='short'):
    return PostPayload(source_post_id='post', text='Exact caption', media_url='https://media.example.test/clip.mp4',
        media_type='video', publication_format=format, video_title='Exact title', visibility='private', made_for_kids=False, contains_synthetic_media=True)


def test_youtube_processing_polls_the_same_uploaded_video(monkeypatch, delivery, caplog):
    caplog.set_level("INFO", logger="httpx")
    calls, statuses = [], iter(['uploaded', 'processed'])
    def handler(request):
        calls.append(request)
        if request.url.path == '/token':
            return httpx.Response(200, json={'access_token': 'fresh'})
        assert request.headers['authorization'] == 'Bearer fresh'
        if request.method == 'POST':
            body = json.loads(request.content)
            assert body == {'snippet': {'title': 'Exact title', 'description': 'Exact caption'}, 'status': {'privacyStatus': 'private', 'selfDeclaredMadeForKids': False, 'containsSyntheticMedia': True}}
            assert delivery.state['phase'] == 'starting'
            return httpx.Response(200, headers={'Location': 'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=secret'})
        if request.method == 'PUT':
            assert delivery.state['phase'] == 'uploading'
            assert request.content == b'0123456789'
            return httpx.Response(201, json={'id': 'video_123'})
        return httpx.Response(200, json={'items': [{'status': {'uploadStatus': next(statuses), 'privacyStatus': 'private'}}]})
    transport(monkeypatch, handler)
    credentials = YouTubeCredentials(channel_id='channel', refresh_token='refresh', client_id='client', client_secret='secret')
    with pytest.raises(DeliveryPending):
        YouTubePublisher().publish_video('channel', payload(), credentials, delivery)
    result = YouTubePublisher().publish_video('channel', payload(), credentials, delivery)
    assert result.external_id == 'video_123'
    assert sum(r.method == 'POST' and r.url.path != '/token' for r in calls) == 1
    assert sum(r.method == 'PUT' for r in calls) == 1
    assert 'upload_id=secret' not in caplog.text
    assert 'redacted' in caplog.text


@pytest.mark.parametrize('received', [0, 4, 10])
def test_youtube_lost_upload_response_uses_session_probe(monkeypatch, delivery, received):
    uploads, probes, inserts = [], [], []
    delivery.save(phase='uploading', upload_url='https://www.googleapis.com/upload/youtube/v3/videos?upload_id=secret')
    def handler(request):
        if request.method == 'POST':
            inserts.append(request)
            pytest.fail('A replacement upload would duplicate the video')
        if request.method == 'PUT' and request.headers.get('content-range') == 'bytes */10':
            probes.append(request)
            if received == 10:
                return httpx.Response(201, json={'id': 'video_123'})
            return httpx.Response(308, headers={'Range': f'bytes=0-{received-1}'} if received else {})
        if request.method == 'PUT':
            uploads.append(request.content)
            assert request.headers['content-range'] == f'bytes {received}-9/10'
            return httpx.Response(201, json={'id': 'video_123'})
        return httpx.Response(200, json={'items': [{'status': {'uploadStatus': 'processed', 'privacyStatus': 'private'}}]})
    transport(monkeypatch, handler)
    assert YouTubePublisher().publish_video('channel', payload(), YouTubeCredentials(access_token='token', channel_id='channel'), delivery).external_id == 'video_123'
    assert len(probes) == 1 and not inserts
    assert uploads == ([] if received == 10 else [b'0123456789'[received:]])


@pytest.mark.parametrize('url', ['http://www.googleapis.com/upload/youtube/x', 'https://evil.test/upload/youtube/x', 'https://www.googleapis.com.evil.test/upload/youtube/x', 'https://token@www.googleapis.com/upload/youtube/x', 'https://www.googleapis.com:444/upload/youtube/x'])
def test_youtube_rejects_untrusted_upload_locations(url):
    with pytest.raises(ExternalApiError):
        session_url(url)


def test_youtube_visibility_mismatch_keeps_authoritative_video_id(monkeypatch, delivery):
    delivery.save(phase='uploaded', external_id='video_123', external_url='https://www.youtube.com/watch?v=video_123')
    transport(monkeypatch, lambda request: httpx.Response(200, json={'items': [{'status': {'uploadStatus': 'processed', 'privacyStatus': 'private'}}]}))
    public = payload(); public.visibility = 'public'
    with pytest.raises(ExternalApiError) as result:
        YouTubePublisher().publish_video('channel', public, YouTubeCredentials(access_token='token', channel_id='channel'), delivery)
    assert result.value.code == 'EXTERNAL_API_YOUTUBE_VISIBILITY_MISMATCH'
    assert delivery.state['external_id'] == 'video_123'
    assert delivery.state['actual_visibility'] == 'private'


def test_instagram_reel_processing_and_missing_permalink_never_republish(monkeypatch, delivery):
    calls, statuses = [], iter(['IN_PROGRESS', 'FINISHED'])
    def handler(request):
        calls.append(request)
        assert request.headers['authorization'] == 'Bearer token'
        assert 'access_token' not in str(request.url)
        if request.url.path.endswith('/media'):
            fields = parse_qs(request.content.decode())
            assert fields['media_type'] == ['REELS'] and fields['caption'] == ['Exact caption']
            assert fields['share_to_feed'] == ['true']
            return httpx.Response(200, json={'id': '123'})
        if request.url.path.endswith('/media_publish'):
            assert delivery.state['phase'] == 'publishing'
            return httpx.Response(200, json={'id': '456'})
        if request.url.path.endswith('/456'):
            return httpx.Response(500)
        return httpx.Response(200, json={'status_code': next(statuses)})
    transport(monkeypatch, handler)
    publisher = InstagramPublisher()
    credentials = InstagramCredentials(access_token='token', instagram_user_id='42')
    with pytest.raises(DeliveryPending):
        publisher.publish_video('42', payload('reel'), credentials, delivery)
    assert publisher.publish_video('42', payload('reel'), credentials, delivery).external_id == '456'
    assert publisher.publish_video('42', payload('reel'), credentials, delivery).external_id == '456'
    assert sum(r.method == 'POST' for r in calls) == 2


@pytest.mark.parametrize('status', [408, 409, 425, 500])
def test_instagram_ambiguous_publish_response_never_resends(monkeypatch, delivery, status):
    delivery.save(phase='processing', container_id='123')
    writes = []
    def handler(request):
        if request.method == 'POST':
            writes.append(request)
            return httpx.Response(status)
        return httpx.Response(200, json={'status_code': 'FINISHED'})
    transport(monkeypatch, handler)
    args = ('42', payload('reel'), InstagramCredentials(access_token='token', instagram_user_id='42'), delivery)
    for _ in range(2):
        with pytest.raises(ExternalApiError):
            InstagramPublisher().publish_video(*args)
    assert len(writes) == 1
    assert delivery.state['phase'] == 'publishing'
