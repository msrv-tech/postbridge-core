"""Small TikTok v2 client; never log provider messages or token responses."""
from time import time
import logging
import re

import httpx
from pydantic import BaseModel, ConfigDict, model_validator

from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.integrations.video_delivery import check_response, unknown

API = "https://open.tiktokapis.com/v2"
PRIVACY_LEVELS = {"PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"}
logger = logging.getLogger(__name__)


class AuthorizationRemoved(ConfigurationError):
    """The provider confirmed an invalid or expired refresh grant."""


class TikTokPostOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    privacy_level: str
    allow_comment: bool = False
    allow_duet: bool = False
    allow_stitch: bool = False
    commercial_content: bool = False
    brand_organic: bool = False
    branded_content: bool = False
    music_usage_confirmed: bool = False
    original_content_confirmed: bool = False

    @model_validator(mode="after")
    def check_choices(self):
        if self.privacy_level not in PRIVACY_LEVELS:
            raise ValueError("Choose a TikTok privacy level")
        if not self.music_usage_confirmed or not self.original_content_confirmed:
            raise ValueError("Confirm the TikTok music declaration and your right to share this original video")
        if self.commercial_content != (self.brand_organic or self.branded_content):
            raise ValueError("Choose the type of commercial content")
        if self.branded_content and self.privacy_level == "SELF_ONLY":
            raise ValueError("Branded content cannot be private")
        return self


def result(response):
    try:
        body = response.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    error = error if isinstance(error, dict) else {}
    details = {"status_code": response.status_code}
    code = error.get("code")
    if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,99}", code):
        details["provider_code"] = code
    for log_id in (response.headers.get("x-tt-logid"), error.get("log_id"), error.get("logid")):
        if isinstance(log_id, str) and re.fullmatch(r"\d{14}[A-Fa-f0-9]{10,50}", log_id):
            details["provider_log_id"] = log_id
            break
    try:
        # HTTP failures also carry TikTok's machine code and request ID. Never
        # retain the message, response body, token fields or request URL.
        check_response(response, "tiktok")
        if "provider_code" not in details:
            raise unknown("tiktok", "TikTok returned an unreadable response")
        if code == "ok":
            data = body.get("data")
            if not isinstance(data, dict):
                raise unknown("tiktok", "TikTok returned an unreadable response")
            return data
        if code in {"internal_error", "internal", "server_error"}:
            raise unknown("tiktok")
        raise ExternalApiError(code="EXTERNAL_API_TIKTOK_REJECTED", message="TikTok rejected the request; review the account and publishing settings",
            source="tiktok", retryable=False, details={})
    except ExternalApiError as exc:
        exc.details.update(details)
        logger.warning("TikTok request failed: status=%s provider_code=%s log_id=%s",
            response.status_code, details.get("provider_code", "unavailable"), details.get("provider_log_id", "unavailable"))
        raise


def access_token(client, credentials, save_credentials):
    if credentials.access_token and credentials.expires_at > time() + 60:
        return credentials.access_token
    if not credentials.refresh_token or credentials.refresh_expires_at <= time():
        raise AuthorizationRemoved("Reconnect this TikTok account to renew publishing access")
    response = client.post(f"{API}/oauth/token/", data={"grant_type": "refresh_token", "client_key": credentials.client_key,
        "client_secret": credentials.client_secret, "refresh_token": credentials.refresh_token})
    if response.status_code >= 400:
        if response.status_code in {400, 401}:
            try:
                if response.json().get("error") in {"invalid_grant", "invalid_token"}:
                    raise AuthorizationRemoved("Reconnect this TikTok account to renew publishing access")
            except (ValueError, AttributeError, TypeError):
                pass
        raise ConfigurationError("Reconnect this TikTok account to renew publishing access")
    try:
        tokens = response.json()
        if (tokens.get("open_id") != credentials.open_id or not tokens.get("access_token")
                or not tokens.get("refresh_token") or "video.publish" not in {value.strip() for value in str(tokens.get("scope", "")).split(",")}
                or int(tokens["expires_in"]) <= 0 or int(tokens["refresh_expires_in"]) <= 0):
            raise ValueError("Invalid token response")
        now = int(time())
        updated = type(credentials).model_validate({**credentials.model_dump(), "access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"],
            "expires_at": now + int(tokens["expires_in"]), "refresh_expires_at": now + int(tokens["refresh_expires_in"])})
        save_credentials(updated)
        return updated.access_token
    except (ValueError, KeyError, TypeError):
        raise ConfigurationError("Reconnect this TikTok account to renew publishing access") from None


def creator_info(client, token):
    data = result(client.post(f"{API}/post/publish/creator_info/query/", headers={"Authorization": f"Bearer {token}"}, json={}))
    try:
        if (not isinstance(data["creator_nickname"], str) or not isinstance(data["creator_username"], str)
                or not isinstance(data["privacy_level_options"], list)
                or not data["privacy_level_options"] or set(data["privacy_level_options"]) - PRIVACY_LEVELS
                or type(data["max_video_post_duration_sec"]) is not int or data["max_video_post_duration_sec"] <= 0
                or any(type(data[key]) is not bool for key in ("comment_disabled", "duet_disabled", "stitch_disabled"))):
            raise ValueError("Invalid creator info")
        return {key: data[key] for key in ("creator_nickname", "creator_username", "privacy_level_options",
            "max_video_post_duration_sec", "comment_disabled", "duet_disabled", "stitch_disabled")}
    except (KeyError, TypeError, ValueError):
        raise unknown("tiktok", "TikTok account publishing information is unavailable") from None


def validate_post(options, creator, duration, text):
    settings = TikTokPostOptions.model_validate(options)
    if settings.privacy_level not in creator["privacy_level_options"]:
        raise ConfigurationError("TikTok privacy options changed; review this video again")
    if any(getattr(settings, f"allow_{key}") and creator[f"{key}_disabled"] for key in ("comment", "duet", "stitch")):
        raise ConfigurationError("TikTok interaction permissions changed; review this video again")
    if not 0 < duration <= creator["max_video_post_duration_sec"]:
        raise ConfigurationError("This video exceeds the TikTok account duration limit")
    if len(text.encode("utf-16-le")) // 2 > 2200:
        raise ConfigurationError("TikTok video captions may contain at most 2200 UTF-16 characters")
    return settings
