"""Schedule snapshots, edit conflicts and conservative acoustic subtitle mapping."""
import copy
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from postbridge.models.domain import VideoAgentExecutionOrm, VideoMaterialOrm, AgentTaskOrm
from postbridge.services.video_schedules import (enqueue_video_agent_run, template_hash,
    update_video_agent_task, validate_template)
from postbridge.services.video_auto_publication import AutoPublicationPolicy
from postbridge.services.video_subtitles import matched_words
from tests.test_video_studio import state, error
from tests.test_video_autopilot import auto
from tests.test_video_schedules import scheduled, create


def edit(db,tenant,task,**changes):
    body=dict(tenant_id=tenant,task_id=task.id,expected_hash=template_hash(task),goal_text=task.goal_text,
        schedule_cron=task.schedule_cron,timezone=task.timezone,
        task_config=json.loads(task.task_config_json),created_by=task.created_by)
    body.update(changes)
    return update_video_agent_task(db,**body),body


def test_edit_keeps_identity_and_frozen_occurrence_and_replays(scheduled):
    db,tenant,*_=scheduled
    task=create(scheduled);db.commit()
    run=enqueue_video_agent_run(db,tenant_id=tenant,task_id=task.id,occurrence_key='before-edit');db.commit()
    snapshot=copy.deepcopy(db.get(VideoAgentExecutionOrm,run.id).input_snapshot)
    config=json.loads(task.task_config_json);config['video']['brief']='New task'
    _,body=edit(db,tenant,task,goal_text='New task',task_config=config,schedule_cron='0 10 * * *');db.commit()
    assert update_video_agent_task(db,**body).id==task.id
    assert db.get(VideoAgentExecutionOrm,run.id).input_snapshot==snapshot
    with error('VIDEO_VERSION_CONFLICT'):
        update_video_agent_task(db,**{**body,'schedule_cron':'0 11 * * *'})
    assert len(db.scalars(select(AgentTaskOrm)).all())==1


def test_edit_rechecks_owner_and_tenant(scheduled):
    db,tenant,other,*_=scheduled
    task=create(scheduled);db.commit()
    with error('VIDEO_SCHEDULE_ACTOR_REVOKED'):
        edit(db,tenant,task,created_by='other-user')
    with error('VIDEO_SCHEDULE_NOT_FOUND'):
        edit(db,other,task)


def test_folder_resolves_at_each_occurrence_and_freezes_ids(scheduled,auto):
    db,tenant,_,material,_=scheduled
    task=create(scheduled,auto=True);db.commit()
    config=json.loads(task.task_config_json);config.update(material_folder='Campaign',material_limit=1)
    row=db.get(VideoMaterialOrm,material['media_asset_id']);row.folder='Campaign';db.commit()
    edit(db,tenant,task,task_config=config);db.commit()
    run=enqueue_video_agent_run(db,tenant_id=tenant,task_id=task.id,occurrence_key='folder-first');db.commit()
    snap=copy.deepcopy(db.get(VideoAgentExecutionOrm,run.id).input_snapshot)
    assert snap['video']['scenes'][0]['asset_id']==row.asset_id
    run.status='awaiting_review';row.folder='Moved';db.commit()
    later=enqueue_video_agent_run(db,tenant_id=tenant,task_id=task.id,occurrence_key='folder-empty');db.commit()
    assert later.status=='failed' and later.error_code=='VIDEO_FOLDER_EMPTY'
    assert db.get(VideoAgentExecutionOrm,run.id).input_snapshot==snap
    assert enqueue_video_agent_run(db,tenant_id=tenant,task_id=task.id,occurrence_key='folder-first').id==run.id


def test_language_routing_requires_reviewed_destinations():
    policy=dict(confirmed=True,bridge_ids=['one','two'],expected_bridges={'one':'a'*64,'two':'b'*64},caption='Demo')
    assert AutoPublicationPolicy(**policy,language_bridge_ids={'ru':['one'],'en':['two']}).language_bridge_ids['en']==['two']
    for mapping in [{'ru':[]},{'ru':['unknown']},{'ru':['one','one']}]:
        with pytest.raises(ValueError):AutoPublicationPolicy(**policy,language_bridge_ids=mapping)


def test_acoustic_words_preserve_approved_text_and_reject_guessed_intervals():
    words=[dict(word=' Hello',start=.2,end=.5,probability=.9),dict(word=' world',start=.6,end=1.,probability=.9)]
    assert matched_words('Hello, world!',words,1.2)==[
        {'text':'Hello,','start':.2,'end':.5},{'text':'world!','start':.6,'end':1.}]
    assert matched_words('A different sentence',words,1.2) is None
    assert matched_words('Hello world',words[:1],1.2) is None
    assert matched_words('Hello world',[{**words[0],'start':float('nan')},words[1]],1.2) is None
    assert matched_words('Hello world',[{**w,'probability':.01} for w in words],1.2) is None


def test_video_errors_are_actionable_http_responses():
    from postbridge.api.main import _status_code_for_error
    from postbridge.services.video_media import video_error
    for code,status in [('VIDEO_REQUEST_NOT_FOUND',404),('VIDEO_VERSION_CONFLICT',409),
        ('VIDEO_FOLDER_EMPTY',422),('VIDEO_PRICING_UNAVAILABLE',503),('VIDEO_BUDGET_EXHAUSTED',402)]:
        assert _status_code_for_error(video_error(code,'test'))==status
