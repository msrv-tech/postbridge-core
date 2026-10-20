from uuid import uuid4
import json
import pytest
from sqlalchemy import select
from postbridge.models.domain import BridgeOrm, ChannelOrm, ChannelCredentialOrm, ContentItemOrm, PublicationTargetOrm
from postbridge.services.video_publication import ApproveVideoPublication, approve_publication, publication_view
from postbridge.services.video_bridges import normalize_bridge_settings
from postbridge.services.publication_target_executor import PublicationTargetExecutor
from postbridge.integrations.video_delivery import VideoDeliveryReceipt
from postbridge.infrastructure.crypto.credentials import encrypt_credential_secret
from tests.test_video_studio import state, error
from tests.test_video_post_drafts import ready
from tests.test_video_publication import publication


def video_bridge(db, tenant, target, *, platform='youtube'):
    target.platform, target.external_id = platform, '42'
    source = ChannelOrm(id=str(uuid4()), tenant_id=tenant, platform='postbridge', kind='source', title='Editorial', external_id='pb/ws-test', status='connected')
    db.add(source)
    credential = db.scalar(select(ChannelCredentialOrm).where(ChannelCredentialOrm.channel_id == target.id))
    credential.encrypted_secret = encrypt_credential_secret(json.dumps({'channel_id': '42', 'instagram_user_id': '42', 'access_token': 'token'}))
    options = {'format': {'youtube':'short','instagram':'reel','telegram':'video'}[platform], 'visibility':'private', 'made_for_kids':False, 'contains_synthetic_media':True}
    bridge = BridgeOrm(id=uuid4().hex, tenant_id=tenant, saas_user_id='actor', source_channel_id=source.id, target_channel_id=target.id,
        status='active', mode='live_sync', settings_json={'video_delivery':options})
    db.add(bridge); db.commit()
    return bridge


def request_for(db, tenant, post_id, bridge):
    view = publication_view(db, tenant, post_id)
    route = next(item for item in view['bridges'] if item['id'] == bridge.id)
    return ApproveVideoPublication(expected_snapshot=view['snapshot_hash'], bridge_ids=[bridge.id], expected_bridges={bridge.id:route['settings_hash']})


@pytest.mark.parametrize('platform', ['youtube', 'instagram'])
def test_receiving_settings_follow_the_bridge_and_execute_once(publication, platform):
    db, tenant, _, post_id, channels = publication
    bridge = video_bridge(db, tenant, channels[0], platform=platform)
    body = request_for(db, tenant, post_id, bridge)
    view = approve_publication(db, tenant, post_id, body)
    assert view['targets'][0]['bridge_id'] == bridge.id
    assert approve_publication(db, tenant, post_id, body)['approval_id'] == view['approval_id']
    calls = []
    class Publisher:
        def publish_video(self, channel, post, creds, context):
            calls.append(post)
            return VideoDeliveryReceipt('published-id')
    executor = PublicationTargetExecutor(db, Publisher())
    assert executor.run(view['targets'][0]['id']) == 1
    assert executor.run(view['targets'][0]['id']) == 0
    assert len(calls) == 1 and calls[0].publication_format == ('short' if platform=='youtube' else 'reel')
    if platform == 'youtube':
        assert calls[0].video_title == 'Exact approved text' and calls[0].visibility == 'private' and calls[0].made_for_kids is False


@pytest.mark.parametrize('change,code', [('paused','VIDEO_BRIDGE_UNAVAILABLE'),('settings','VIDEO_BRIDGE_CHANGED'),('deleted','VIDEO_BRIDGE_UNAVAILABLE'),('stale-identity','VIDEO_BRIDGE_UNAVAILABLE')])
def test_bridge_changes_after_approval_stop_all_network_calls(publication, change, code):
    db, tenant, _, post_id, channels = publication
    bridge = video_bridge(db, tenant, channels[0])
    view = approve_publication(db, tenant, post_id, request_for(db, tenant, post_id, bridge))
    if change == 'stale-identity':
        from sqlalchemy import update
        db.execute(update(BridgeOrm).where(BridgeOrm.id == bridge.id).values(status='paused').execution_options(synchronize_session=False))
    elif change == 'paused': bridge.status = 'paused'
    elif change == 'deleted': db.delete(bridge)
    else: bridge.settings_json = {'video_delivery': {**bridge.settings_json['video_delivery'], 'visibility':'public'}}
    db.commit()
    class Forbidden:
        def publish_video(self, *args): pytest.fail('Bridge changes must stop sending before provider calls')
    with error(code):
        PublicationTargetExecutor(db, Forbidden()).run(view['targets'][0]['id'])
    assert db.get(PublicationTargetOrm, view['targets'][0]['id']).status == 'failed'


def test_stale_review_foreign_bridge_and_injected_options_cannot_approve(publication):
    db, tenant, other, post_id, channels = publication
    bridge = video_bridge(db, tenant, channels[0])
    body = request_for(db, tenant, post_id, bridge)
    bridge.settings_json = {'video_delivery': {**bridge.settings_json['video_delivery'], 'visibility':'public'}}; db.commit()
    with error('VIDEO_BRIDGE_CHANGED'): approve_publication(db, tenant, post_id, body)
    bridge.tenant_id = other; db.commit()
    with error('VIDEO_BRIDGE_UNAVAILABLE'): approve_publication(db, tenant, post_id, body)
    with pytest.raises(ValueError):
        ApproveVideoPublication(expected_snapshot=body.expected_snapshot, bridge_ids=[bridge.id], expected_bridges=body.expected_bridges,
            options={channels[0].id:{'format':'short','made_for_kids':True}})


def test_receiving_side_validation_and_plain_post_routing(publication):
    from postbridge.api.service_internal import service_list_live_sync_targets_by_source
    db, tenant, _, _, channels = publication
    bridge = video_bridge(db, tenant, channels[0])
    source, target = db.get(ChannelOrm,bridge.source_channel_id), channels[0]
    with error('VIDEO_BRIDGE_SETTINGS_INVALID'): normalize_bridge_settings(None,source,target)
    source.platform='telegram'
    with error('VIDEO_BRIDGE_UNSUPPORTED'): normalize_bridge_settings(bridge.settings_json,source,target)
    source.platform='postbridge'
    assert service_list_live_sync_targets_by_source(source_channel_id=source.id, tenant_id=tenant, session=db)['items'] == []
