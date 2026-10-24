"""Standing, revocable schedule consent uses the ordinary immutable video approval."""
import json
from types import SimpleNamespace
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from postbridge.models.domain import (AgentTaskOrm, AgentRunOrm, VideoAgentExecutionOrm, VideoBatchItemOrm,
    VideoBatchOrm, VideoRevisionOrm, VideoProjectOrm, VideoPostDraftOrm)
from postbridge.services.video_media import video_error
from postbridge.services.video_bridges import reviewed_bridges
from postbridge.services.video_post_drafts import PrepareVideoPost, prepare_post_draft
from postbridge.services.video_publication import ApproveVideoPublication, publication_view, approve_publication, approval_for_content


class AutoPublicationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    confirmed: Literal[True]
    bridge_ids: list[str] = Field(min_length=1, max_length=10)
    expected_bridges: dict[str, str]
    caption: str = Field(min_length=1, max_length=1024)
    language_bridge_ids: dict[Literal["ru", "en"], list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reviewed(self):
        if len(set(self.bridge_ids)) != len(self.bridge_ids) or set(self.bridge_ids) != set(self.expected_bridges):
            raise ValueError("Review every unique bridge")
        if any(not ids or len(ids) != len(set(ids)) or not set(ids).issubset(self.bridge_ids)
               for ids in self.language_bridge_ids.values()):
            raise ValueError("Language destinations must be selected reviewed bridges")
        if any(len(key) > 64 or len(value) != 64 or any(c not in '0123456789abcdef' for c in value)
               for key, value in self.expected_bridges.items()):
            raise ValueError("Bridge settings must have a reviewed fingerprint")
        return self


def schedule_bridges(db, tenant_id, source_channel_id):
    post = SimpleNamespace(title=None, body_markdown='Video', body_structured_json=None)
    return {"items": [row for row in reviewed_bridges(db, tenant_id, post)
        if row['source_channel_id'] == source_channel_id and row['platform'] != 'tiktok']}


def assert_policy(db, tenant_id, execution, *, lock=False):
    stmt = select(AgentTaskOrm).where(AgentTaskOrm.id == execution.task_id, AgentTaskOrm.tenant_id == tenant_id)
    task = db.scalar(stmt.with_for_update().execution_options(populate_existing=True) if lock else stmt)
    saved = execution.input_snapshot.get('auto_publication')
    current = json.loads(task.task_config_json or '{}').get('auto_publication') if task else None
    if not saved or not task or task.status != 'active' or saved != current or task.created_by != execution.input_snapshot.get('created_by'):
        raise video_error('VIDEO_AUTO_PUBLICATION_REVOKED', 'Automatic publication consent is absent, changed or paused')
    policy = AutoPublicationPolicy.model_validate(saved)
    available = {b['id']: b for b in schedule_bridges(db, tenant_id, task.channel_id)['items']}
    if any(key not in available or available[key]['settings_hash'] != policy.expected_bridges[key] for key in policy.bridge_ids):
        raise video_error('VIDEO_BRIDGE_CHANGED', 'Review automatic publication after changing a bridge')
    return policy


def execution_revisions(db, execution):
    if execution.batch_id:
        root = db.get(VideoBatchOrm, execution.batch_id)
        if not root or root.status != 'succeeded':
            raise video_error('VIDEO_NOT_READY', 'Wait for every video variant to finish')
        batches = db.scalars(select(VideoBatchOrm).where(VideoBatchOrm.tenant_id == execution.tenant_id)).all()
        ids = [b.id for b in batches if b.id == root.id or b.policy_json.get('root_batch_id') == root.id]
        revision_ids = list(db.scalars(select(VideoBatchItemOrm.revision_id).where(VideoBatchItemOrm.batch_id.in_(ids),
            VideoBatchItemOrm.revision_id.is_not(None))))
        revisions = db.scalars(select(VideoRevisionOrm).where(VideoRevisionOrm.id.in_(revision_ids))).all()
    else:
        revisions = db.scalars(select(VideoRevisionOrm).where(VideoRevisionOrm.project_id == execution.project_id,
            VideoRevisionOrm.request_key == execution.input_snapshot['video']['request_key'])).all()
    if not revisions or len(revisions) > 35:
        raise video_error('VIDEO_NOT_READY', 'Scheduled video outputs are unavailable')
    maximum = {}
    for row in revisions:
        maximum[row.project_id] = max(maximum.get(row.project_id, 0), row.version)
    for project_id, version in maximum.items():
        project = db.get(VideoProjectOrm, project_id, populate_existing=True)
        if not project or project.tenant_id != execution.tenant_id or project.current_version != version:
            raise video_error('VIDEO_VERSION_CONFLICT', 'A video was edited after this scheduled production')
    return sorted(revisions, key=lambda r: (r.project_id, r.version))


def execution(db, tenant_id, run_id):
    run = db.get(AgentRunOrm, run_id)
    value = db.get(VideoAgentExecutionOrm, run_id)
    if not run or not value or value.tenant_id != tenant_id or run.status != 'awaiting_review':
        raise video_error('VIDEO_SCHEDULE_NOT_FOUND', 'Completed scheduled video run not found')
    return value


def prepare_auto_posts(db, tenant_id, run_id):
    value = execution(db, tenant_id, run_id)
    policy = assert_policy(db, tenant_id, value)
    revisions = execution_revisions(db, value)
    result = []
    for revision in revisions:
        project = db.get(VideoProjectOrm, revision.project_id)
        post = prepare_post_draft(db, tenant_id, project.id, PrepareVideoPost(expected_version=project.current_version,
            source_version=revision.version, caption=policy.caption))
        result.append({'content_item_id': post['content_item_id'], 'approved': bool(approval_for_content(db, post['content_item_id']))})
    return {'items': result}


def auto_approval(db, tenant_id, run_id, content_id, *, lock=False):
    value = execution(db, tenant_id, run_id)
    policy = assert_policy(db, tenant_id, value, lock=lock)
    revisions = execution_revisions(db, value)
    receipt = db.scalar(select(VideoPostDraftOrm).where(VideoPostDraftOrm.tenant_id == tenant_id,
        VideoPostDraftOrm.content_item_id == content_id, VideoPostDraftOrm.revision_id.in_([r.id for r in revisions])))
    if not receipt:
        raise video_error('VIDEO_POST_NOT_FOUND', 'The post is not an output of this schedule occurrence')
    view = publication_view(db, tenant_id, content_id)
    if view['snapshot']['text'] != policy.caption:
        raise video_error('VIDEO_APPROVAL_CONFLICT', 'A post was edited after automatic publication was configured')
    revision = next(r for r in revisions if r.id == receipt.revision_id)
    language = (revision.production_options or {}).get('language', 'ru')
    bridges = policy.language_bridge_ids.get(language, []) if policy.language_bridge_ids else policy.bridge_ids
    if not bridges:
        raise video_error('VIDEO_AUTO_LANGUAGE_UNMAPPED', 'Choose destinations for this video language')
    return ApproveVideoPublication(expected_snapshot=view['snapshot_hash'], bridge_ids=bridges,
        expected_bridges={key: policy.expected_bridges[key] for key in bridges})


def approve_auto_post(db, tenant_id, run_id, content_id, payload):
    expected = auto_approval(db, tenant_id, run_id, content_id, lock=True)
    if payload.model_dump(mode='json') != expected.model_dump(mode='json'):
        raise video_error('VIDEO_APPROVAL_CONFLICT', 'Automatic approval differs from schedule consent')
    return approve_publication(db, tenant_id, content_id, payload, schedule_run_id=run_id)
