"""Durable, encrypted provider checkpoints shared by the delivery executor."""
from contextlib import contextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from postbridge.infrastructure.crypto.credentials import decrypt_credential_secret, encrypt_credential_secret
from postbridge.infrastructure.media_storage import copy_media_object_to_file
from postbridge.models.domain import PublicationTargetOrm, VideoDeliveryStateOrm


from postbridge.integrations.video_delivery import DeliveryPending, VideoDeliveryReceipt, check_response, unknown


class VideoDeliveryContext:
    def __init__(self, session, target_id, snapshot):
        self.session, self.target_id, self.snapshot = session, target_id, snapshot
        target = session.get(PublicationTargetOrm, target_id)
        self.channel_id, self.tenant_id = target.channel_id, target.tenant_id
        row = session.get(VideoDeliveryStateOrm, target_id)
        self.state = json.loads(decrypt_credential_secret(row.encrypted_state)) if row else {}

    def save(self, **updates):
        self.state.update(updates)
        row = self.session.get(VideoDeliveryStateOrm, self.target_id)
        if row is None:
            row = VideoDeliveryStateOrm(target_id=self.target_id)
            self.session.add(row)
        row.encrypted_state = encrypt_credential_secret(json.dumps(self.state, sort_keys=True))
        row.updated_at = datetime.now(UTC)
        target = self.session.get(PublicationTargetOrm, self.target_id)
        if self.state.get("external_id"):
            target.external_post_id = self.state["external_id"]
        if self.state.get("external_url"):
            target.external_url = self.state["external_url"]
        self.session.commit()  # Persist before crossing each irreversible boundary.

    @contextmanager
    def media_file(self):
        with TemporaryDirectory(prefix="postbridge-delivery-") as directory:
            path = Path(directory) / "video.mp4"
            copy_media_object_to_file(self.snapshot["object_key"], path)
            if path.stat().st_size != self.snapshot["byte_size"]:
                raise unknown("youtube", "The approved media file changed")
            yield path
