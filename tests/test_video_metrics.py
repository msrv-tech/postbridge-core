import httpx
from sqlalchemy import select

from postbridge.models.domain import PublicationTargetOrm
from postbridge.services.video_metrics import VideoMetricsRequest, read_video_metrics
from postbridge.services.video_publication import approve_publication
from tests.test_video_bridges import video_bridge, request_for
from tests.test_video_publication import publication
from tests.test_video_studio import state, error
from tests.test_video_post_drafts import ready


def test_youtube_counters_use_channel_identity_and_absent_counts_stay_unknown(publication, monkeypatch):
    from postbridge.integrations.youtube.publisher import YouTubePublisher
    db, tenant, other, post_id, channels = publication
    bridge = video_bridge(db, tenant, channels[0])
    view = approve_publication(db, tenant, post_id, request_for(db, tenant, post_id, bridge))
    target = db.get(PublicationTargetOrm, view['targets'][0]['id'])
    target.status, target.external_post_id = 'published', 'test-video'
    db.commit()
    payload = VideoMetricsRequest(content_ids=[post_id])
    with error('VIDEO_POST_NOT_FOUND'):
        read_video_metrics(db, other, payload)
    original_client = httpx.Client
    identity = ['42']
    def handle(request):
        assert request.url.host == 'www.googleapis.com'
        return httpx.Response(200, json={'items': [{'id': 'test-video', 'snippet': {'channelId': identity[0]},
            'statistics': {'viewCount': '123', 'commentCount': '-5'}}]})
    monkeypatch.setattr(YouTubePublisher, '_token', lambda *_: 'test-token')
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(handle)))
    result = read_video_metrics(db, tenant, payload)['items'][0]
    assert result['metrics']['views']['value'] == 123
    assert result['metrics']['likes']['value'] is None
    assert result['metrics']['comments']['value'] is None
    identity[0] = 'different-channel'
    result = read_video_metrics(db, tenant, payload)['items'][0]
    assert result['status'] == 'unavailable' and result['metrics'] == {}
