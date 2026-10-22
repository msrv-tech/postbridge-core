"""Durable bounded batches sharing the existing revision, job and billing contracts."""
from datetime import UTC, datetime
import hashlib
import json
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select

from postbridge.models.domain import (TenantOrm, VideoBatchOrm, VideoBatchItemOrm,
    VideoProductionJobOrm, VideoRevisionOrm)
from postbridge.services.video_media import video_error
from postbridge.services.video_studio import VideoCreate, _project, _check_assets, capabilities, job_usage, require_enabled


class VoiceChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Literal["ru", "en"]
    voice: Literal["Kore", "Charon", "Puck"] = "Kore"
    voice_style: Literal["neutral", "warm", "energetic"] = "neutral"


class BatchBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9:_-]+$")
    max_units_total: int | None = Field(default=None, ge=1, le=100_000_000)
    created_by: str | None = Field(default=None, max_length=128)


class VoiceBatchCreate(BatchBase):
    expected_version: int = Field(ge=1)
    source_version: int = Field(ge=1)
    variants: list[VoiceChoice] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def unique_variants(self):
        keys = [(v.language, v.voice, v.voice_style) for v in self.variants]
        if len(keys) != len(set(keys)):
            raise ValueError("Choose each language and voice only once")
        return self


class ProjectBatchCreate(BatchBase):
    video: VideoCreate
    count: int = Field(default=1, ge=1, le=5)
    variants: list[VoiceChoice] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def validate_template(self):
        if self.video.billing_reservation_id or self.video.billing_budget_units:
            raise ValueError("Batch templates cannot supply billing credentials")
        if self.count > 1 and self.video.mode != "autopilot":
            raise ValueError("Multiple original videos require autopilot")
        if self.video.mode == "manual" and not self.video.scenes:
            raise ValueError("Manual batches require video scenes")
        keys = [(v.language, v.voice, v.voice_style) for v in self.variants]
        if len(keys) != len(set(keys)):
            raise ValueError("Choose each language and voice only once")
        return self


def _batch(session, tenant_id, batch_id, *, lock=False):
    stmt = select(VideoBatchOrm).where(VideoBatchOrm.id == batch_id, VideoBatchOrm.tenant_id == tenant_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(stmt)
    if row is None:
        raise video_error("VIDEO_BATCH_NOT_FOUND", "Video batch not found")
    return row


def batch_payload(session, row):
    from postbridge.infrastructure.media_storage import managed_media_url
    from postbridge.models.domain import MediaAssetOrm
    items = []
    for item in session.scalars(select(VideoBatchItemOrm).where(VideoBatchItemOrm.batch_id == row.id)
                                .order_by(VideoBatchItemOrm.position)):
        revision = session.get(VideoRevisionOrm, item.revision_id) if item.revision_id else None
        job = session.scalar(select(VideoProductionJobOrm).where(VideoProductionJobOrm.revision_id == item.revision_id)) if revision else None
        asset = session.get(MediaAssetOrm, job.output_asset_id) if job and job.output_asset_id else None
        items.append({"id": item.id, "position": item.position, "status": item.status,
            "error_code": item.error_code, "payload": item.payload_json, "project_id": item.project_id,
            "revision_id": item.revision_id, "version": revision.version if revision else None,
            "job_id": job.id if job else None,
            "usage": job_usage(job),
            "output_url": managed_media_url(asset.id, asset.object_key) if asset else None,
            "result": item.result_json})
    return {"id": row.id, "kind": row.kind, "status": row.status, "created_by": row.created_by,
        "source_project_id": row.source_project_id, "source_version": row.source_version,
        "expected_project_version": row.expected_project_version, "policy": row.policy_json,
        "error_code": row.error_code, "created_at": row.created_at, "completed_at": row.completed_at, "items": items}


def get_batch(session, tenant_id, batch_id):
    return batch_payload(session, _batch(session, tenant_id, batch_id))


def list_batches(session, tenant_id, *, project_id=None, limit=20):
    stmt = select(VideoBatchOrm).where(VideoBatchOrm.tenant_id == tenant_id)
    if project_id:
        _project(session, tenant_id, project_id)
        stmt = stmt.where(VideoBatchOrm.source_project_id == project_id)
    rows = session.scalars(stmt.order_by(VideoBatchOrm.created_at.desc()).limit(limit)).all()
    return {"items": [batch_payload(session, row) for row in rows]}


def _create(session, tenant_id, payload, kind, *, project_id=None, policy=None, commit=True):
    require_enabled()
    if session.scalar(select(TenantOrm).where(TenantOrm.id == tenant_id).with_for_update()) is None:
        raise video_error("VIDEO_TENANT_NOT_FOUND", "Workspace not found")
    canonical = {"kind": kind, "project_id": project_id, **payload.model_dump()}
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
    key = str(uuid5(NAMESPACE_URL, f"video-batch:{tenant_id}:{payload.request_key}"))
    existing = session.get(VideoBatchOrm, key)
    if existing:
        if existing.request_hash != digest:
            raise video_error("VIDEO_REQUEST_CONFLICT", "This batch request has different settings")
        return batch_payload(session, existing)
    if not (policy or {}).get("root_batch_id"):
        active = session.scalar(select(func.count()).select_from(VideoBatchOrm).where(
            VideoBatchOrm.tenant_id == tenant_id, VideoBatchOrm.status.in_(["queued", "running"])))
        if active >= 3:
            raise video_error("VIDEO_QUEUE_FULL", "Wait for an existing video batch or cancel it")
    if kind == "voices" or payload.variants:
        if not capabilities()["speech_enabled"]:
            raise video_error("VIDEO_SPEECH_UNAVAILABLE", "Google speech generation is not configured")
    if kind == "projects":
        _check_assets(session, tenant_id, [scene.model_dump() for scene in payload.video.scenes])
        if (payload.video.generated_visuals == "video" or any(s.regenerate_video or not s.asset_id and s.generation_kind == "video" for s in payload.video.scenes)) and not capabilities()["video_generation_enabled"]:
            raise video_error("VIDEO_GENERATION_UNAVAILABLE", "AI video generation is not configured")
        if (payload.video.mode == "autopilot" or any(not s.asset_id or s.regenerate_image or s.regenerate_video for s in payload.video.scenes)) and not capabilities()["autopilot_enabled"]:
            raise video_error("VIDEO_AUTOPILOT_UNAVAILABLE", "Video autopilot is not configured")
        if payload.video.narration_enabled and not capabilities()["speech_enabled"]:
            raise video_error("VIDEO_SPEECH_UNAVAILABLE", "Google speech generation is not configured")
        if payload.video.mode == "autopilot" and any(s.speech_asset_id for s in payload.video.scenes):
            raise video_error("VIDEO_SPEECH_ASSET_INVALID", "Autopilot cannot reuse narrated scenes")
    project = _project(session, tenant_id, project_id, lock=True) if project_id else None
    if project:
        if project.current_version != payload.expected_version:
            raise video_error("VIDEO_VERSION_CONFLICT", "The project changed; refresh before creating variants")
        revision = session.scalar(select(VideoRevisionOrm).where(VideoRevisionOrm.project_id == project_id,
            VideoRevisionOrm.version == payload.source_version))
        job = session.scalar(select(VideoProductionJobOrm).where(VideoProductionJobOrm.revision_id == revision.id)) if revision else None
        if not job or job.status != "succeeded":
            raise video_error("VIDEO_NOT_READY", "Choose a completed video version")
        if project.current_version + len(payload.variants) > 100:
            raise video_error("VIDEO_REVISION_LIMIT", "This project would exceed its revision limit")
    row = VideoBatchOrm(id=key, tenant_id=tenant_id, request_key=payload.request_key, request_hash=digest,
        kind=kind, status="queued", created_by=payload.created_by, source_project_id=project_id,
        source_version=payload.source_version if project else None,
        expected_project_version=payload.expected_version if project else None,
        policy_json={"max_units_total": payload.max_units_total, "variants": [v.model_dump() for v in payload.variants], **(policy or {})})
    session.add(row)
    session.flush()
    values = [v.model_dump() for v in payload.variants] if kind == "voices" else [
        payload.video.model_dump(exclude={"billing_reservation_id", "billing_budget_units"}) for _ in range(payload.count)]
    for index, value in enumerate(values):
        value = dict(value)
        if kind == "projects" and payload.count > 1:
            value["brief"] = (value["brief"] +
                f"\nVariation {index + 1} of {payload.count}: choose a distinct hook and angle; preserve all product facts.")
            if len(value["brief"]) > 4000:
                raise video_error("VIDEO_BATCH_BRIEF_TOO_LONG", "Shorten the brief to leave space for variation instructions")
        item_id = str(uuid5(NAMESPACE_URL, f"video-batch:{key}:item:{index}"))
        session.add(VideoBatchItemOrm(id=item_id, batch_id=key, position=index,
            payload_json={**value, "request_key": f"batch:{item_id}"}, result_json={}, status="queued"))
    session.commit() if commit else session.flush()
    return batch_payload(session, row)


def create_voice_batch(session, tenant_id, project_id, payload, **kwargs):
    return _create(session, tenant_id, payload, "voices", project_id=project_id, **kwargs)


def create_project_batch(session, tenant_id, payload, **kwargs):
    return _create(session, tenant_id, payload, "projects", **kwargs)


def cancel_batch(session, tenant_id, batch_id, *, commit=True):
    row = _batch(session, tenant_id, batch_id, lock=True)
    if row.status in {"succeeded", "failed", "cancelled"}:
        return batch_payload(session, row)
    row.status, row.completed_at = "cancelled", datetime.now(UTC)
    for item in session.scalars(select(VideoBatchItemOrm).where(VideoBatchItemOrm.batch_id == batch_id)):
        if item.status in {"succeeded", "failed"}:
            continue
        item.status = "cancelled"
        if item.revision_id:
            job = session.scalar(select(VideoProductionJobOrm).where(VideoProductionJobOrm.revision_id == item.revision_id).with_for_update())
            if job and job.status in {"queued", "running"}:
                job.status, job.stage, job.completed_at = "cancelled", "cancelled", datetime.now(UTC)
        if item.result_json.get("voice_batch_id"):
            cancel_batch(session, tenant_id, item.result_json["voice_batch_id"], commit=False)
    session.commit() if commit else session.flush()
    return batch_payload(session, row)
