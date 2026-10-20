from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
import json
import runpy

import httpx
import pytest
from sqlalchemy import func, select

from postbridge.models.domain import (ChannelOrm, ChannelCredentialOrm, ContentItemOrm, PublicationPlanOrm,
    PublicationTargetOrm, RenderVariantOrm, VideoPublicationApprovalOrm)
from postbridge.domain.errors import ExternalApiError, ValidationError
from postbridge.services.publication_planning import create_plan_and_targets_for_content_item, create_content_with_plan_and_targets
from postbridge.services.publication_target_executor import PublicationTargetExecutor, recover_stuck_publication_targets, schedule_publication_target_retry
from postbridge.services.postbridge_workspace_content import update_postbridge_content_item, delete_postbridge_content_item
from postbridge.services.video_post_drafts import PrepareVideoPost, prepare_post_draft
from postbridge.services.video_publication import (ApproveVideoPublication, approve_publication, publication_view, retry_failed_target)
from tests.test_video_studio import state, error
from tests.test_video_post_drafts import ready


@pytest.fixture
def publication(ready, monkeypatch):
    state, project = ready
    db, tenant, other, _, _ = state
    monkeypatch.setenv("VIDEO_PUBLICATION_ENABLED", "1")
    post = prepare_post_draft(db, tenant, project["id"], PrepareVideoPost(expected_version=1, caption="Exact approved text"))
    channels = []
    from postbridge.infrastructure.crypto.credentials import encrypt_credential_secret
    for index in range(2):
        ch = ChannelOrm(id=str(uuid4()), tenant_id=tenant, platform="telegram", kind="destination",
            title=f"Channel {index}", external_id=f"@demo_channel_{index}", status="connected")
        db.add(ch)
        db.flush()
        db.add(ChannelCredentialOrm(id=str(uuid4()), tenant_id=tenant, channel_id=ch.id,
            auth_type="bot", encrypted_secret=encrypt_credential_secret(json.dumps({"bot_token": "test-token"})), status="active"))
        channels.append(ch)
    db.commit()
    return db, tenant, other, post["content_item_id"], channels


def approval(publication):
    db, tenant, _, post_id, channels = publication
    view = publication_view(db, tenant, post_id)
    body = ApproveVideoPublication(expected_snapshot=view["snapshot_hash"], channel_ids=[ch.id for ch in channels])
    return approve_publication(db, tenant, post_id, body), body


class Publisher:
    def __init__(self, exc=None):
        self.calls = []
        self.exc = exc
    def publish_post(self, channel, post, credentials=None):
        self.calls.append((channel, post))
        if self.exc:
            raise self.exc
        return "123"


def test_approval_is_idempotent_and_shared_executor_sends_exact_snapshot(publication):
    db, tenant, _, post_id, _ = publication
    view, body = approval(publication)
    assert approve_publication(db, tenant, post_id, body)["approval_id"] == view["approval_id"]
    assert db.scalar(select(func.count()).select_from(PublicationPlanOrm)) == 1
    assert db.scalar(select(func.count()).select_from(VideoPublicationApprovalOrm)) == 1
    target_ids = [target["id"] for target in view["targets"]]
    # A generic render rewrite must not change the approved text.
    for render in db.scalars(select(RenderVariantOrm)):
        render.body_text = "Hidden bridge rewrite"
    db.commit()
    publisher = Publisher()
    executor = PublicationTargetExecutor(db, publisher)
    for target_id in target_ids:
        assert executor.run(target_id) == 1
        assert executor.run(target_id) == 0
    assert len(publisher.calls) == 2
    assert all(post.text == "Exact approved text" and post.media_type == "video" for _, post in publisher.calls)
    assert db.get(ContentItemOrm, post_id).status == "published"
    assert all(target["external_url"].endswith("/123") for target in publication_view(db, tenant, post_id)["targets"])
    with error("VIDEO_PUBLICATION_LOCKED"):
        update_postbridge_content_item(db, row=db.get(ContentItemOrm, post_id), content_md="Later change")
    with error("VIDEO_PUBLICATION_LOCKED"):
        delete_postbridge_content_item(db, row=db.get(ContentItemOrm, post_id))


def test_stale_text_foreign_target_and_disabled_flag_cannot_approve(publication, monkeypatch):
    db, tenant, other, post_id, channels = publication
    view = publication_view(db, tenant, post_id)
    update_postbridge_content_item(db, row=db.get(ContentItemOrm, post_id), content_md="Changed")
    db.commit()
    with error("VIDEO_APPROVAL_CONFLICT"):
        approve_publication(db, tenant, post_id, ApproveVideoPublication(expected_snapshot=view["snapshot_hash"], channel_ids=[channels[0].id]))
    body = ApproveVideoPublication(expected_snapshot=publication_view(db, tenant, post_id)["snapshot_hash"], channel_ids=[str(uuid4())])
    with error("VIDEO_DESTINATION_UNAVAILABLE"):
        approve_publication(db, tenant, post_id, body)
    with error("VIDEO_POST_NOT_FOUND"):
        publication_view(db, other, post_id)
    monkeypatch.setenv("VIDEO_PUBLICATION_ENABLED", "0")
    with error("VIDEO_PUBLICATION_DISABLED"):
        approve_publication(db, tenant, post_id, body)
    assert db.scalar(select(func.count()).select_from(PublicationPlanOrm)) == 0


def test_generic_live_sync_and_plan_routes_cannot_bypass_approval(publication):
    from postbridge.services.live_sync_publish_service import ingest_live_sync_publication
    db, tenant, _, post_id, channels = publication
    post = db.get(ContentItemOrm, post_id)
    with error("VIDEO_APPROVAL_REQUIRED"):
        update_postbridge_content_item(db, row=post, status="published")
    with error("VIDEO_APPROVAL_REQUIRED"):
        create_plan_and_targets_for_content_item(db, tenant_id=tenant, content_item=post, channel_ids=[channels[0].id])
    with error("VIDEO_APPROVAL_REQUIRED"):
        create_content_with_plan_and_targets(db, tenant_id=tenant, channel_ids=[channels[0].id], media_url=post.media_url)
    with error("VIDEO_APPROVAL_REQUIRED"):
        ingest_live_sync_publication(db, tenant_id=tenant, target_core_channel_id=channels[0].id,
            source_channel="pb/demo", target_channel="demo", target_platform="telegram", post={"source_post_id": post_id}, correlation_id="test")


def test_worker_rechecks_destination_and_content_after_approval(publication):
    db, tenant, _, post_id, channels = publication
    view, _ = approval(publication)
    channels[0].external_id = "@other_destination"
    db.commit()
    target = next(item for item in view["targets"] if item["channel_id"] == channels[0].id)
    publisher = Publisher()
    with error("VIDEO_DESTINATION_UNAVAILABLE"):
        PublicationTargetExecutor(db, publisher).run(target["id"])
    assert publisher.calls == []
    # Even a direct database rewrite that bypasses normal editing is stopped.
    db.get(ContentItemOrm, post_id).body_markdown = "Unauthorized replacement"
    db.commit()
    other_target = next(item for item in view["targets"] if item["channel_id"] != channels[0].id)
    with error("VIDEO_APPROVAL_CONFLICT"):
        PublicationTargetExecutor(db, publisher).run(other_target["id"])
    assert publisher.calls == []


def test_unknown_delivery_and_stuck_claim_are_never_automatically_retried(publication):
    db, tenant, _, post_id, _ = publication
    view, _ = approval(publication)
    exc = ExternalApiError(code="EXTERNAL_API_TELEGRAM_PUBLISH_ERROR", message="Timeout", source="telegram", retryable=True)
    publisher = Publisher(exc)
    target_id = view["targets"][0]["id"]
    with pytest.raises(ExternalApiError):
        PublicationTargetExecutor(db, publisher).run(target_id)
    assert db.get(PublicationTargetOrm, target_id).status == "unknown"
    assert not schedule_publication_target_retry(db, target_id, exc, 3)
    with error("VIDEO_RETRY_UNSAFE"):
        retry_failed_target(db, tenant, post_id, target_id)
    second = db.get(PublicationTargetOrm, view["targets"][1]["id"])
    second.status, second.updated_at = "publishing", datetime.now(UTC) - timedelta(hours=1)
    db.commit()
    assert recover_stuck_publication_targets(db, timeout_seconds=30) == []
    assert second.status == "unknown"
    assert PublicationTargetExecutor(db, Publisher()).run(target_id) == 0


def test_retry_only_the_rejected_destination_after_partial_success(publication):
    db, tenant, _, post_id, _ = publication
    view, _ = approval(publication)
    first, second = [item["id"] for item in view["targets"]]
    PublicationTargetExecutor(db, Publisher()).run(first)
    exc = ExternalApiError(code="EXTERNAL_API_TELEGRAM_PUBLISH_ERROR", message="Forbidden", source="telegram", retryable=False)
    with pytest.raises(ExternalApiError):
        PublicationTargetExecutor(db, Publisher(exc)).run(second)
    retry_failed_target(db, tenant, post_id, second)
    publisher = Publisher()
    assert PublicationTargetExecutor(db, publisher).run(first) == 0
    assert PublicationTargetExecutor(db, publisher).run(second) == 1
    assert len(publisher.calls) == 1


def test_telegram_video_contract_uses_one_send_video_request(monkeypatch):
    from postbridge.api.schemas import TelegramCredentials
    from postbridge.domain.models import PostPayload
    from postbridge.integrations.telegram.publisher import TelegramPublisher
    calls = []
    client_class = httpx.Client
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client_class(transport=httpx.MockTransport(handler)))
    result = TelegramPublisher().publish_post("@demo", PostPayload("test", "Exact caption", "https://media.example.test/out.mp4", media_type="video"), TelegramCredentials(bot_token="test-token"))
    assert result == "7" and len(calls) == 1
    assert calls[0].url.path.endswith("/sendVideo")
    assert json.loads(calls[0].content) == {"chat_id": "@demo", "video": "https://media.example.test/out.mp4", "caption": "Exact caption", "supports_streaming": True}


def test_approval_migration_handles_existing_baseline(publication):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    db, *_ = publication
    migration = runpy.run_path(str(Path(__file__).resolve().parents[1] / "alembic/versions/20261019_video_approvals.py"))
    with db.get_bind().begin() as connection:
        VideoPublicationApprovalOrm.__table__.drop(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
            migration["upgrade"]()


def test_durable_dispatch_and_cancel_preserve_already_started_delivery(publication, monkeypatch):
    from postbridge.services.video_publication import cancel_pending
    from postbridge.workers import video_publication_tasks
    from sqlalchemy.orm import sessionmaker
    db, tenant, _, post_id, _ = publication
    view, _ = approval(publication)
    monkeypatch.setattr(video_publication_tasks, "SESSION_LOCAL", sessionmaker(db.get_bind()))
    def unavailable(*args, **kwargs):
        raise RuntimeError("Broker unavailable")
    monkeypatch.setattr(video_publication_tasks.celery_app, "send_task", unavailable)
    with pytest.raises(RuntimeError):
        video_publication_tasks.dispatch_video_publications.run()
    queued = []
    monkeypatch.setattr(video_publication_tasks.celery_app, "send_task", lambda name, **kwargs: queued.append((name, kwargs)))
    assert video_publication_tasks.dispatch_video_publications.run() == 2
    assert all(name == "postbridge.publication.process_target" for name, _ in queued)
    first = db.get(PublicationTargetOrm, view["targets"][0]["id"])
    first.status = "publishing"
    db.commit()
    cancelled = cancel_pending(db, tenant, post_id)
    assert sorted(target["status"] for target in cancelled["targets"]) == ["cancelled", "publishing"]
    assert video_publication_tasks.dispatch_video_publications.run() == 0


@pytest.mark.parametrize("case,code", [("caption", "VIDEO_CAPTION_TOO_LONG"), ("file", "VIDEO_DELIVERY_FILE_TOO_LARGE")])
def test_telegram_limits_are_checked_before_any_delivery(publication, case, code):
    from postbridge.models.domain import MediaAssetOrm
    db, tenant, _, post_id, channels = publication
    if case == "caption":
        update_postbridge_content_item(db, row=db.get(ContentItemOrm, post_id), content_md="😀" * 600)
    else:
        view = publication_view(db, tenant, post_id)
        db.get(MediaAssetOrm, view["snapshot"]["output_asset_id"]).byte_size = 21 * 1024 * 1024
    db.commit()
    view = publication_view(db, tenant, post_id)
    with error(code):
        approve_publication(db, tenant, post_id, ApproveVideoPublication(expected_snapshot=view["snapshot_hash"], channel_ids=[channels[0].id]))
    assert db.scalar(select(func.count()).select_from(PublicationPlanOrm)) == 0


def test_video_api_reviews_approves_and_cancels_the_canonical_plan(publication):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from postbridge.api.video_studio import router
    from postbridge.db import get_db_session
    db, tenant, _, post_id, channels = publication
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_session] = lambda: db
    client = TestClient(app)
    headers = {"Authorization": "Bearer svc-test-secret", "X-Tenant-Id": tenant}
    path = f"/internal/service/videos/posts/{post_id}/publication"
    view = client.get(path, headers=headers)
    assert view.status_code == 200, view.text
    approved = client.post(path + "/approve", headers=headers,
        json={"expected_snapshot": view.json()["snapshot_hash"], "channel_ids": [channels[0].id]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_id"]
    cancelled = client.post(path + "/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["targets"][0]["status"] == "cancelled"
    moved = client.post(path + "/reschedule", headers=headers, json={"expected_schedule_version": 1})
    assert moved.status_code == 200, moved.text
    assert moved.json()["delivery"]["version"] == 2 and moved.json()["targets"][0]["status"] == "pending"


@pytest.mark.parametrize('platform,format', [('youtube', 'short'), ('instagram', 'reel')])
def test_provider_options_are_immutable_and_processing_resumes_after_start_window(publication, monkeypatch, platform, format):
    from postbridge.infrastructure.crypto.credentials import encrypt_credential_secret, decrypt_credential_secret
    from postbridge.models.domain import VideoDeliveryStateOrm
    from postbridge.services.video_publication import reconcile_target
    from postbridge.services.video_delivery_state import DeliveryPending, VideoDeliveryReceipt
    db, tenant, _, post_id, channels = publication
    channel = channels[0]
    channel.platform, channel.external_id = platform, '42'
    credential = db.scalar(select(ChannelCredentialOrm).where(ChannelCredentialOrm.channel_id == channel.id))
    credential.encrypted_secret = encrypt_credential_secret(json.dumps({'access_token': 'token', 'channel_id': '42', 'instagram_user_id': '42'}))
    db.commit()
    view = publication_view(db, tenant, post_id)
    options = {channel.id: {'format': format, 'title': 'Approved title', 'visibility': 'private', 'made_for_kids': False}}
    body = ApproveVideoPublication(expected_snapshot=view['snapshot_hash'], channel_ids=[channel.id], options=options)
    view = approve_publication(db, tenant, post_id, body)
    changed = body.model_copy(update={'options': {}})
    with error('VIDEO_APPROVAL_CONFLICT'):
        approve_publication(db, tenant, post_id, changed)
    target_id = view['targets'][0]['id']
    calls = []
    class Provider:
        def publish_video(self, slug, post, creds, delivery):
            calls.append(post)
            if not delivery.state:
                delivery.save(phase='uploaded', external_id='provider-id', upload_url='secret-session')
                raise DeliveryPending()
            return VideoDeliveryReceipt('provider-id', 'https://example.test/result')
    executor = PublicationTargetExecutor(db, Provider())
    assert executor.run(target_id) == 0
    assert db.get(PublicationTargetOrm, target_id).status == 'processing'
    encrypted = db.get(VideoDeliveryStateOrm, target_id).encrypted_state
    assert 'secret-session' not in encrypted and 'secret-session' in decrypt_credential_secret(encrypted)
    approval_row = db.scalar(select(VideoPublicationApprovalOrm))
    approval_row.deadline_at = datetime.now(UTC) - timedelta(hours=1)
    db.commit()
    assert executor.run(target_id) == 1
    assert calls[-1].publication_format == format and calls[-1].video_title == 'Approved title'
    assert db.get(PublicationTargetOrm, target_id).external_post_id == 'provider-id'
    # A repeated reconcile cannot reset a completed target or resend it.
    assert reconcile_target(db, tenant, post_id, target_id)['targets'][0]['status'] == 'published'
    assert executor.run(target_id) == 0 and len(calls) == 2


def test_youtube_requires_explicit_audience_and_correct_credentials(publication):
    from postbridge.infrastructure.crypto.credentials import encrypt_credential_secret
    db, tenant, _, post_id, channels = publication
    channel = channels[0]; channel.platform, channel.external_id = 'youtube', '42'
    row = db.scalar(select(ChannelCredentialOrm).where(ChannelCredentialOrm.channel_id == channel.id))
    row.encrypted_secret = encrypt_credential_secret(json.dumps({'access_token': 'token', 'channel_id': 'other'})); db.commit()
    view = publication_view(db, tenant, post_id)
    args = dict(expected_snapshot=view['snapshot_hash'], channel_ids=[channel.id])
    with error('VIDEO_DESTINATION_OPTIONS_INVALID'):
        approve_publication(db, tenant, post_id, ApproveVideoPublication(**args, options={channel.id: {'format': 'short', 'title': 'Title'}}))
    with error('VIDEO_DESTINATION_UNAVAILABLE'):
        approve_publication(db, tenant, post_id, ApproveVideoPublication(**args, options={channel.id: {'format': 'short', 'title': 'Title', 'made_for_kids': False}}))


@pytest.mark.parametrize("via_bridge", [False, True])
def test_real_managed_mp4_upload_recovers_a_lost_response_without_a_second_insert(publication, monkeypatch, via_bridge):
    from postbridge.infrastructure.crypto.credentials import encrypt_credential_secret
    from postbridge.integrations.youtube.publisher import YouTubePublisher
    from postbridge.services.video_publication import reconcile_target
    db, tenant, _, post_id, channels = publication
    channel = channels[0]; channel.platform, channel.external_id = 'youtube', '42'
    credential = db.scalar(select(ChannelCredentialOrm).where(ChannelCredentialOrm.channel_id == channel.id))
    credential.encrypted_secret = encrypt_credential_secret(json.dumps({'access_token': 'token', 'channel_id': '42'})); db.commit()
    view = publication_view(db, tenant, post_id)
    if via_bridge:
        from tests.test_video_bridges import video_bridge, request_for
        bridge = video_bridge(db, tenant, channel)
        body = request_for(db, tenant, post_id, bridge)
    else:
        body = ApproveVideoPublication(expected_snapshot=view['snapshot_hash'], channel_ids=[channel.id],
            options={channel.id: {'format': 'short', 'title': 'Approved title', 'made_for_kids': False}})
    view = approve_publication(db, tenant, post_id, body)
    target_id = view['targets'][0]['id']
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == 'POST':
            return httpx.Response(200, headers={'Location': 'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=private-checkpoint'})
        if request.method == 'PUT' and request.headers.get('content-range', '').startswith('bytes */'):
            assert request.content == b''
            return httpx.Response(201, json={'id': 'video_123'})
        if request.method == 'PUT':
            assert b'ftyp' in request.content[:32] and len(request.content) == view['snapshot']['byte_size']
            raise httpx.ReadTimeout('Upload reply lost', request=request)
        return httpx.Response(200, json={'items': [{'status': {'uploadStatus': 'processed', 'privacyStatus': 'private'}}]})
    original = httpx.Client
    class Client(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, 'Client', Client)
    executor = PublicationTargetExecutor(db, YouTubePublisher())
    with pytest.raises(ExternalApiError):
        executor.run(target_id)
    assert db.get(PublicationTargetOrm, target_id).status == 'unknown'
    assert executor.run(target_id) == 0  # No automatic resend of an unknown target.
    assert reconcile_target(db, tenant, post_id, target_id)['targets'][0]['status'] == 'processing'
    assert executor.run(target_id) == 1 and executor.run(target_id) == 0
    assert db.get(ContentItemOrm, post_id).status == 'published'
    assert sum(r.method == 'POST' for r in requests) == 1
    assert sum(r.method == 'PUT' for r in requests) == 2  # Original bytes, then empty probe.
