"""Resumable uploads of approved managed MP4s to YouTube Data API v3."""
import re
from urllib.parse import urlparse
import httpx

from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.integrations.video_delivery import DeliveryPending, VideoDeliveryReceipt, check_response, unknown

API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"


def session_url(value):
    parsed = urlparse(value or "")
    if parsed.scheme != "https" or parsed.hostname not in {"www.googleapis.com", "youtube.googleapis.com"} or parsed.port not in {None, 443} or parsed.username or parsed.password or not parsed.path.startswith("/upload/youtube/"):
        raise unknown("youtube", "YouTube returned an invalid upload session")
    return value


class YouTubePublisher:
    def publish_post(self, target_channel, payload, credentials=None):
        raise ConfigurationError("YouTube uploads require the approved video delivery workflow")

    def publish_video(self, target_channel, payload, credentials, delivery):
        if not credentials or credentials.channel_id != target_channel.removeprefix("youtube/"):
            raise ConfigurationError("Connect publishing credentials for this YouTube channel")
        if payload.publication_format != "short" or not payload.video_title or payload.made_for_kids is None:
            raise ConfigurationError("Choose the YouTube title, audience and visibility before approval")
        try:
            with httpx.Client(timeout=httpx.Timeout(120, connect=15), follow_redirects=False) as client:
                token = self._token(client, credentials)
                headers = {"Authorization": f"Bearer {token}"}
                state = delivery.state
                if not state or state.get("phase") == "rejected":
                    delivery.save(phase="starting")
                    create = client.post(UPLOAD, params={"uploadType": "resumable", "part": "snippet,status", "notifySubscribers": "false"},
                        headers={**headers, "X-Upload-Content-Length": str(delivery.snapshot["byte_size"]), "X-Upload-Content-Type": "video/mp4"},
                        json={"snippet": {"title": payload.video_title, "description": payload.text}, "status": {
                            "privacyStatus": payload.visibility or "private", "selfDeclaredMadeForKids": payload.made_for_kids,
                            "containsSyntheticMedia": bool(payload.contains_synthetic_media)}})
                    if 400 <= create.status_code < 500 and create.status_code not in {408, 409, 425}:
                        delivery.save(phase="rejected")
                    check_response(create, "youtube")
                    url = session_url(create.headers.get("location"))
                    delivery.save(phase="upload_ready", upload_url=url)
                state = delivery.state
                if state.get("phase") == "starting":
                    raise unknown("youtube", "The upload session response was lost; no automatic new upload is allowed")
                if state.get("phase") in {"upload_ready", "uploading"}:
                    url = session_url(state["upload_url"])
                    total = delivery.snapshot["byte_size"]
                    offset = 0
                    complete = None
                    if state["phase"] == "uploading":
                        probe = client.put(url, headers={**headers, "Content-Length": "0", "Content-Range": f"bytes */{total}"}, content=b"")
                        if probe.status_code in {200, 201}:
                            complete = probe
                        elif probe.status_code == 308:
                            found = re.fullmatch(r"bytes=0-(\d+)", probe.headers.get("range", ""))
                            offset = int(found[1]) + 1 if found else 0
                        else:
                            raise unknown("youtube", "The previous upload must be reconciled; a new upload was not started")
                    if complete is None:
                        if not 0 <= offset < total:
                            raise unknown("youtube")
                        delivery.save(phase="uploading")
                        with delivery.media_file() as path, path.open("rb") as stream:
                            stream.seek(offset)
                            complete = client.put(url, headers={**headers, "Content-Type": "video/mp4", "Content-Length": str(total-offset),
                                "Content-Range": f"bytes {offset}-{total-1}/{total}"}, content=stream)
                        # Any uncertain upload is probed on its saved session, never reinserted.
                        if complete.status_code not in {200, 201}:
                            raise unknown("youtube")
                    video_id = complete.json().get("id")
                    if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,128}", video_id):
                        raise unknown("youtube")
                    delivery.save(phase="uploaded", external_id=video_id, external_url=f"https://www.youtube.com/watch?v={video_id}")
                state = delivery.state
                if not state.get("external_id"):
                    raise unknown("youtube")
                result = client.get(f"{API}/videos", headers=headers,
                    params={"id": state["external_id"], "part": "status,processingDetails"})
                check_response(result, "youtube")
                items = result.json().get("items") or []
                if not items:
                    raise DeliveryPending()
                status = items[0].get("status", {})
                processing = items[0].get("processingDetails", {}).get("processingStatus")
                if processing in {"failed", "terminated"} or status.get("uploadStatus") in {"failed", "rejected", "deleted"}:
                    raise ExternalApiError(code="EXTERNAL_API_YOUTUBE_PROCESSING_FAILED", message="YouTube could not process the uploaded video",
                        source="youtube", retryable=False, details={})
                if processing != "succeeded" and status.get("uploadStatus") != "processed":
                    raise DeliveryPending()
                actual = status.get("privacyStatus")
                delivery.save(actual_visibility=actual)
                if actual != (payload.visibility or "private"):
                    raise ExternalApiError(code="EXTERNAL_API_YOUTUBE_VISIBILITY_MISMATCH", message="YouTube applied a different visibility; check the uploaded video before continuing",
                        source="youtube", retryable=False, details={})
                delivery.save(phase="published")
                return VideoDeliveryReceipt(state["external_id"], state["external_url"])
        except httpx.RequestError:
            raise unknown("youtube") from None

    def _token(self, client, credentials):
        if credentials.refresh_token and credentials.client_id and credentials.client_secret:
            response = client.post("https://oauth2.googleapis.com/token", data={"grant_type": "refresh_token",
                "refresh_token": credentials.refresh_token, "client_id": credentials.client_id, "client_secret": credentials.client_secret})
            if response.status_code >= 400:
                raise ConfigurationError("Reconnect this YouTube channel to renew publishing access")
            token = response.json().get("access_token")
        else:
            token = credentials.access_token
        if not isinstance(token, str) or not token:
            raise ConfigurationError("Reconnect this YouTube channel")
        return token
