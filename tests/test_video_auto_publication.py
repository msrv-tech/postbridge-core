import json
from uuid import uuid4

import pytest
from sqlalchemy import select, func

from postbridge.agent.storage import create_agent_task, pause_agent_task
from postbridge.models.domain import (AgentRunOrm, VideoAgentExecutionOrm, VideoRevisionOrm, VideoPostDraftOrm,
    VideoPublicationApprovalOrm, PublicationPlanOrm, ContentItemOrm)
from postbridge.services.video_auto_publication import (schedule_bridges, prepare_auto_posts, auto_approval, approve_auto_post)
from postbridge.services.video_schedules import enqueue_video_agent_run
from postbridge.services.video_publication import approved_payload
from postbridge.services.postbridge_workspace_content import update_postbridge_content_item
from tests.test_video_bridges import video_bridge
from tests.test_video_publication import publication
from tests.test_video_studio import state, error
from tests.test_video_post_drafts import ready


@pytest.fixture
def automatic(publication):
    db, tenant, other, post_id, channels = publication
    bridge = video_bridge(db, tenant, channels[0])
    available = schedule_bridges(db, tenant, bridge.source_channel_id)['items'][0]
    receipt = db.scalar(select(VideoPostDraftOrm).where(VideoPostDraftOrm.content_item_id == post_id))
    revision = db.get(VideoRevisionOrm, receipt.revision_id)
    key = str(uuid4())
    policy = {'confirmed': True, 'bridge_ids': [bridge.id], 'expected_bridges': {bridge.id: available['settings_hash']}, 'caption': 'Exact approved text'}
    task = create_agent_task(db, tenant_id=tenant, channel_id=bridge.source_channel_id, mode='video_autopilot', goal_text='Product',
        editorial_instructions=None, schedule_cron='0 9 * * 1', timezone='UTC', max_candidates_per_run=1, autonomy_mode='draft_approval',
        provider_config_id=None, model_name=None, content_item_id=None, created_by='actor', task_config={
            'request_key': key, 'auto_publication': policy, 'video': {'brief': 'Product', 'request_key': key,
                'mode': 'manual', 'scenes': [{'asset_id': revision.scenes[0]['asset_id'], 'duration_seconds': 1}]}})
    run = enqueue_video_agent_run(db, tenant_id=tenant, task_id=task.id, occurrence_key=str(uuid4()))
    run.status = 'awaiting_review'
    execution = db.get(VideoAgentExecutionOrm, run.id)
    execution.project_id = revision.project_id
    revision.request_key = execution.input_snapshot['video']['request_key']
    db.commit()
    return db, tenant, other, post_id, bridge, run, task


def test_explicit_policy_uses_existing_approval_once_and_pause_stops_delivery(automatic):
    from postbridge.models.domain import PublicationTargetOrm
    db, tenant, _, post_id, _, run, task = automatic
    assert prepare_auto_posts(db, tenant, run.id)['items'] == [{'content_item_id': post_id, 'approved': False}]
    body = auto_approval(db, tenant, run.id, post_id)
    view = approve_auto_post(db, tenant, run.id, post_id, body)
    assert approve_auto_post(db, tenant, run.id, post_id, body)['approval_id'] == view['approval_id']
    assert db.scalar(select(func.count()).select_from(PublicationPlanOrm)) == 1
    assert db.get(VideoPublicationApprovalOrm, view['approval_id']).auto_agent_run_id == run.id
    pause_agent_task(db, tenant_id=tenant, task_id=task.id); db.commit()
    with error('VIDEO_AUTO_PUBLICATION_REVOKED'):
        auto_approval(db, tenant, run.id, post_id)
    target = db.get(PublicationTargetOrm, view['targets'][0]['id'])
    with error('VIDEO_AUTO_PUBLICATION_REVOKED'):
        approved_payload(db, target, db.get(ContentItemOrm, post_id), None)


def test_changed_bridge_changed_caption_and_foreign_run_never_approve(automatic):
    db, tenant, other, post_id, bridge, run, _ = automatic
    with error('VIDEO_SCHEDULE_NOT_FOUND'):
        prepare_auto_posts(db, other, run.id)
    update_postbridge_content_item(db, row=db.get(ContentItemOrm, post_id), content_md='Edited by user'); db.commit()
    with error('VIDEO_APPROVAL_CONFLICT'):
        auto_approval(db, tenant, run.id, post_id)
    bridge.settings_json = {**bridge.settings_json, 'added': True}; db.commit()
    with error('VIDEO_BRIDGE_CHANGED'):
        auto_approval(db, tenant, run.id, post_id)
    assert db.scalar(select(func.count()).select_from(VideoPublicationApprovalOrm)) == 0


def test_language_variant_uses_only_its_reviewed_bridges(automatic):
    from postbridge.models.domain import ChannelOrm
    db,tenant,_,post_id,first,run,task=automatic
    target=db.scalar(select(ChannelOrm).where(ChannelOrm.tenant_id==tenant,ChannelOrm.kind=='destination',ChannelOrm.id!=first.target_channel_id))
    second=video_bridge(db,tenant,target)
    second.source_channel_id=first.source_channel_id;db.commit()
    fingerprints={b['id']:b['settings_hash'] for b in schedule_bridges(db,tenant,first.source_channel_id)['items']}
    policy={'confirmed':True,'bridge_ids':[first.id,second.id],'expected_bridges':fingerprints,
        'caption':'Exact approved text','language_bridge_ids':{'ru':[first.id],'en':[second.id]}}
    config=json.loads(task.task_config_json);config['auto_publication']=policy;task.task_config_json=json.dumps(config)
    execution=db.get(VideoAgentExecutionOrm,run.id);execution.input_snapshot={**execution.input_snapshot,'auto_publication':policy}
    receipt=db.scalar(select(VideoPostDraftOrm).where(VideoPostDraftOrm.content_item_id==post_id))
    revision=db.get(VideoRevisionOrm,receipt.revision_id)
    revision.production_options={**revision.production_options,'language':'en'};db.commit()
    approval=auto_approval(db,tenant,run.id,post_id)
    assert approval.bridge_ids==[second.id] and approval.expected_bridges=={second.id:fingerprints[second.id]}
    revision.production_options={**revision.production_options,'language':'ru'};db.commit()
    assert auto_approval(db,tenant,run.id,post_id).bridge_ids==[first.id]


def test_auto_approval_keeps_frozen_time_and_refuses_late_ready_video(automatic):
    from datetime import UTC, datetime, timedelta
    db, tenant, _, post_id, _, run, _ = automatic
    execution = db.get(VideoAgentExecutionOrm, run.id)
    due = (datetime.now(UTC) + timedelta(hours=2)).replace(second=0, microsecond=0)
    execution.input_snapshot = {**execution.input_snapshot, "publish_at": due.isoformat(), "timezone": "Asia/Novosibirsk"}
    db.commit()
    body = auto_approval(db, tenant, run.id, post_id)
    assert body.publish_at == due and body.timezone == "Asia/Novosibirsk"
    execution.input_snapshot = {**execution.input_snapshot, "publish_at": (due - timedelta(days=1)).isoformat()}
    db.commit()
    with error("VIDEO_DELIVERY_MISSED"):
        auto_approval(db, tenant, run.id, post_id)
