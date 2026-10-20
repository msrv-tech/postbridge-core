"""TikTok provider boundaries, tenant isolation and immutable consent."""
from contextlib import contextmanager
import json
from time import time
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from postbridge.api.schemas import TikTokCredentials
from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.domain.models import PostPayload
from postbridge.infrastructure.crypto.credentials import encrypt_credential_secret, decode_channel_credential_raw
from postbridge.integrations.tiktok.api import TikTokPostOptions, validate_post, result
from postbridge.integrations.tiktok.publisher import TikTokPublisher
from postbridge.integrations.video_delivery import DeliveryPending, VideoDeliveryReceipt
from postbridge.models.domain import ChannelCredentialOrm, BridgeOrm, ChannelOrm
from postbridge.services.tiktok import get_creator_info
from postbridge.services.video_publication import ApproveVideoPublication, approve_publication, publication_view
from postbridge.services.publication_target_executor import PublicationTargetExecutor
from tests.test_video_studio import state, error
from tests.test_video_post_drafts import ready
from tests.test_video_publication import publication
from tests.test_video_delivery_providers import delivery, transport

CREATOR = {"creator_nickname": "Test creator", "creator_username": "creator", "privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
    "max_video_post_duration_sec": 180, "comment_disabled": False, "duet_disabled": True, "stitch_disabled": False}
CHOICES = {"privacy_level": "SELF_ONLY", "music_usage_confirmed": True, "original_content_confirmed": True}


def credential(**updates):
    return TikTokCredentials(access_token="access", refresh_token="refresh", open_id="creator-id", client_key="client", client_secret="secret",
        expires_at=int(time()) + 3600, refresh_expires_at=int(time()) + 86400).model_copy(update=updates)


def ok(data):
    return httpx.Response(200, json={"data": data, "error": {"code": "ok"}})


@pytest.mark.parametrize("changes", [{"privacy_level": ""}, {"music_usage_confirmed": False}, {"original_content_confirmed": False},
    {"commercial_content": True}, {"brand_organic": True}, {"commercial_content": True, "branded_content": True}])
def test_post_choices_require_manual_visibility_and_explicit_declarations(changes):
    with pytest.raises(ValueError):
        TikTokPostOptions.model_validate({**CHOICES, **changes})


def test_creator_limits_and_non_bmp_caption_lengths_are_enforced():
    for choices, duration, text in [({**CHOICES, "allow_duet": True}, 10, "text"), (CHOICES, 181, "text"),
            (CHOICES, 10, "😀" * 1101), ({**CHOICES, "privacy_level": "FOLLOWER_OF_CREATOR"}, 10, "text")]:
        with pytest.raises(ConfigurationError):
            validate_post(choices, CREATOR, duration, text)


def mock_delivery_client(monkeypatch, delivery, handler):
    delivery.session, delivery.channel_id, delivery.tenant_id = None, "channel", "tenant"
    client = httpx.Client(transport=httpx.MockTransport(handler))
    @contextmanager
    def authorized(*args):
        yield client, "access", credential()
    monkeypatch.setattr("postbridge.services.tiktok.authorized_client", authorized)
    monkeypatch.setattr("postbridge.services.video_media.inspect_media", lambda path: {"duration_seconds": 10})
    monkeypatch.setenv("TIKTOK_MEDIA_URL_PREFIX", "https://media.example.test/")
    return client


def post():
    return PostPayload(source_post_id="post", text="Exact creator caption", media_url="https://media.example.test/clip.mp4",
        publication_format="tiktok", contains_synthetic_media=True, video_duration_seconds=10, tiktok_options=CHOICES)


def test_direct_post_uses_verified_server_url_and_polls_without_duplicate_init(monkeypatch, delivery):
    calls = []
    statuses = iter(["PROCESSING_DOWNLOAD", "PUBLISH_COMPLETE"])
    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer access"
        if request.url.path.endswith("creator_info/query/"):
            return ok(CREATOR)
        if request.url.path.endswith("video/init/"):
            assert delivery.state["phase"] == "starting"
            body = json.loads(request.content)
            assert body["source_info"] == {"source": "PULL_FROM_URL", "video_url": post().media_url}
            assert body["post_info"] == {"title": post().text, "privacy_level": "SELF_ONLY", "disable_comment": True,
                "disable_duet": True, "disable_stitch": True, "brand_content_toggle": False, "brand_organic_toggle": False, "is_aigc": True}
            return ok({"publish_id": "private-publish-id"})
        assert json.loads(request.content) == {"publish_id": "private-publish-id"}
        return ok({"status": next(statuses), "publicaly_available_post_id": []})
    with mock_delivery_client(monkeypatch, delivery, handler):
        with pytest.raises(DeliveryPending):
            TikTokPublisher().publish_video("creator-id", post(), credential(), delivery)
        receipt = TikTokPublisher().publish_video("creator-id", post(), credential(), delivery)
    assert receipt.external_id == "private-publish-id" and receipt.url is None
    assert sum(request.url.path.endswith("video/init/") for request in calls) == 1


@pytest.mark.parametrize("outcome", ["timeout", "server_error", "bad_body"])
def test_uncertain_init_never_restarts_upload(monkeypatch, delivery, outcome):
    starts = []
    def handler(request):
        if request.url.path.endswith("creator_info/query/"):
            return ok(CREATOR)
        starts.append(request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("lost response", request=request)
        return httpx.Response(503) if outcome == "server_error" else ok({})
    with mock_delivery_client(monkeypatch, delivery, handler):
        for _ in range(2):
            with pytest.raises(ExternalApiError) as caught:
                TikTokPublisher().publish_video("creator-id", post(), credential(), delivery)
            assert caught.value.code == "EXTERNAL_API_TIKTOK_DELIVERY_UNKNOWN"
    assert len(starts) == 1 and delivery.state["phase"] == "starting"


def test_definite_rejection_is_distinct_from_http_200_error_and_processing_failure(monkeypatch, delivery):
    response = httpx.Response(200, request=httpx.Request("POST", "https://open.tiktokapis.com/v2/test"),
        json={"error": {"code": "spam_risk_too_many_posts", "message": "not logged"}})
    with pytest.raises(ExternalApiError) as caught:
        result(response)
    assert caught.value.code == "EXTERNAL_API_TIKTOK_REJECTED"
    delivery.save(phase="processing", publish_id="job")
    with mock_delivery_client(monkeypatch, delivery, lambda request: ok({"status": "FAILED"})):
        with pytest.raises(ExternalApiError) as caught:
            TikTokPublisher().publish_video("creator-id", post(), credential(), delivery)
    assert caught.value.code == "EXTERNAL_API_TIKTOK_PROCESSING_FAILED"


def setup_channel(publication, **credential_updates):
    db, tenant, other, post_id, channels = publication
    channel = channels[0]
    channel.platform, channel.external_id = "tiktok", "creator-id"
    row = db.scalar(select(ChannelCredentialOrm).where(ChannelCredentialOrm.channel_id == channel.id))
    row.encrypted_secret = encrypt_credential_secret(credential(**credential_updates).model_dump_json())
    db.commit()
    return db, tenant, other, post_id, channel, row


def test_rotated_tokens_are_persisted_encrypted_and_creator_response_is_safe(publication, monkeypatch):
    db, tenant, other, _, channel, row = setup_channel(publication, expires_at=0)
    def handler(request):
        if request.url.path.endswith("oauth/token/"):
            return httpx.Response(200, json={"open_id": "creator-id", "scope": "user.info.basic,video.publish", "access_token": "fresh-access",
                "refresh_token": "rotated-refresh", "expires_in": 86400, "refresh_expires_in": 31536000})
        assert request.headers["authorization"] == "Bearer fresh-access"
        assert "rotated-refresh" in decode_channel_credential_raw(db.get(ChannelCredentialOrm, row.id))
        return ok(CREATOR)
    transport(monkeypatch, handler)
    response = get_creator_info(db, tenant, channel.id)
    assert response["privacy_level_options"] == ["SELF_ONLY"] and response["app_audited"] is False
    assert "fresh-access" not in str(response) and "rotated-refresh" not in row.encrypted_secret
    with error("VIDEO_DESTINATION_UNAVAILABLE"):
        get_creator_info(db, other, channel.id)


def test_bridge_approval_preserves_per_post_choices_and_executes_exactly_once(publication, monkeypatch):
    db, tenant, _, post_id, channel, _ = setup_channel(publication)
    source = ChannelOrm(id=str(uuid4()), tenant_id=tenant, platform="postbridge", kind="source", title="Editorial",
        external_id="pb/ws-test", status="connected")
    db.add(source)
    bridge = BridgeOrm(id=uuid4().hex, tenant_id=tenant, saas_user_id="actor", source_channel_id=source.id, target_channel_id=channel.id,
        status="active", mode="live_sync", settings_json={"video_delivery": {"format": "tiktok", "contains_synthetic_media": True}})
    db.add(bridge); db.commit()
    transport(monkeypatch, lambda request: ok(CREATOR))
    view = publication_view(db, tenant, post_id)
    payload = ApproveVideoPublication(expected_snapshot=view["snapshot_hash"], bridge_ids=[bridge.id],
        expected_bridges={bridge.id: view["bridges"][0]["settings_hash"]}, tiktok_options={channel.id: CHOICES})
    approved = approve_publication(db, tenant, post_id, payload)
    assert approve_publication(db, tenant, post_id, payload)["approval_id"] == approved["approval_id"]
    changed = payload.model_copy(update={"tiktok_options": {uuid4(): TikTokPostOptions.model_validate(CHOICES)}})
    with error("VIDEO_APPROVAL_CONFLICT"):
        approve_publication(db, tenant, post_id, changed)
    calls = []
    class Publisher:
        def publish_video(self, slug, value, credentials, context):
            calls.append(value)
            return VideoDeliveryReceipt("private-job")
    executor = PublicationTargetExecutor(db, Publisher())
    assert executor.run(approved["targets"][0]["id"]) == 1
    assert executor.run(approved["targets"][0]["id"]) == 0
    assert calls[0].tiktok_options["music_usage_confirmed"] is True
    assert calls[0].text == "Exact approved text"


@pytest.mark.parametrize("provider_status", [200, 503])
def test_disconnect_revokes_before_erasing_credentials_and_receipts(publication, monkeypatch, provider_status):
    from postbridge.services.tiktok import disconnect
    db, tenant, _, _, channel, row = setup_channel(publication)
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.path == "/v2/oauth/revoke/" and b"token=access" in request.content
        return httpx.Response(provider_status, content=b"")
    transport(monkeypatch, handler)
    channel_id = channel.id
    if provider_status == 200:
        assert disconnect(db, tenant, channel_id) == {"status": "disconnected"}
        assert db.get(ChannelOrm, channel_id) is None and db.get(ChannelCredentialOrm, row.id) is None
    else:
        with pytest.raises(ExternalApiError) as caught:
            disconnect(db, tenant, channel_id)
        assert caught.value.code == "TIKTOK_REVOCATION_UNAVAILABLE"
        assert db.get(ChannelOrm, channel_id) is not None and db.get(ChannelCredentialOrm, row.id) is not None
    assert len(calls) == 1
