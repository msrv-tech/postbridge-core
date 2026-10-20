"""Tenant-bound creator information and encrypted TikTok token rotation."""
from contextlib import contextmanager
import json

import httpx
from sqlalchemy import select

from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.infrastructure.crypto.credentials import encrypt_credential_secret
from postbridge.integrations.tiktok.api import access_token, creator_info, AuthorizationRemoved
from postbridge.integrations.tiktok.credentials import decode_channel_credentials
from postbridge.models.domain import ChannelCredentialOrm, ChannelOrm
from postbridge.services.video_media import video_error


@contextmanager
def authorized_client(session, channel_id, tenant_id):
    row = session.scalar(select(ChannelCredentialOrm).where(ChannelCredentialOrm.channel_id == channel_id,
        ChannelCredentialOrm.tenant_id == tenant_id, ChannelCredentialOrm.status == "active").with_for_update())
    credentials = decode_channel_credentials(row) if row else None
    channel = session.get(ChannelOrm, channel_id)
    if (credentials is None or not channel or channel.tenant_id != tenant_id or channel.platform != "tiktok"
            or channel.status != "connected" or channel.external_id != credentials.open_id):
        raise ConfigurationError("Reconnect this TikTok account")
    def save(updated):
        row.encrypted_secret = encrypt_credential_secret(updated.model_dump_json())
        session.commit()  # A refresh token can rotate; persist it before the next API request.
    with httpx.Client(timeout=httpx.Timeout(120, connect=15), follow_redirects=False) as client:
        token = access_token(client, credentials, save)
        yield client, token, credentials


def get_creator_info(session, tenant_id, channel_id):
    channel = session.get(ChannelOrm, channel_id)
    if not channel or channel.tenant_id != tenant_id or channel.platform != "tiktok" or channel.status != "connected":
        raise video_error("VIDEO_DESTINATION_UNAVAILABLE", "Choose a connected TikTok account in this workspace")
    try:
        with authorized_client(session, channel.id, tenant_id) as (client, token, _):
            result = creator_info(client, token)
    except httpx.RequestError:
        from postbridge.integrations.video_delivery import unknown
        raise unknown("tiktok", "TikTok account information is temporarily unavailable") from None
    from postbridge.config import get_settings
    result["app_audited"] = get_settings().tiktok_direct_post_audited
    if not result["app_audited"]:
        result["privacy_level_options"] = [value for value in result["privacy_level_options"] if value == "SELF_ONLY"]
    session.commit()
    return result


def disconnect(session, tenant_id, channel_id):
    channel = session.get(ChannelOrm, channel_id)
    if not channel or channel.tenant_id != tenant_id or channel.platform != "tiktok":
        raise video_error("VIDEO_DESTINATION_UNAVAILABLE", "TikTok account not found in this workspace")
    try:
        with authorized_client(session, channel.id, tenant_id) as (client, token, credentials):
            response = client.post("https://open.tiktokapis.com/v2/oauth/revoke/", data={
                "client_key": credentials.client_key, "client_secret": credentials.client_secret, "token": token})
            if response.status_code in {400, 401} and response.json().get("error") == "invalid_token":
                pass  # Access has already been removed by TikTok.
            elif response.status_code != 200 or response.content.strip() not in {b"", b"{}"}:
                raise ValueError("Revocation failed")
    except AuthorizationRemoved:
        pass  # There is no remaining refresh grant to revoke.
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise ExternalApiError(code="TIKTOK_REVOCATION_UNAVAILABLE", message="Unable to revoke TikTok access. Retry disconnecting the account.",
            source="tiktok", retryable=True, details={}) from None
    from postbridge.services.youtube_data import purge_channel_data
    purge_channel_data(session, channel)
    session.commit()
    return {"status": "disconnected"}
