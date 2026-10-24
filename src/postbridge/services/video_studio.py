"""Versioned video projects with opt-in, reserved-budget autopilot production."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from postbridge.config import get_settings
from postbridge.infrastructure.media_storage import managed_media_url, upload_media_file
from postbridge.models.domain import MediaAssetOrm, TenantOrm, VideoProjectOrm, VideoRevisionOrm, VideoProductionJobOrm, VideoSpeechAssetOrm
from postbridge.services.video_media import HEIGHT, WIDTH, MAX_DURATION, MAX_SCENES, MAX_UPLOAD_BYTES, inspect_media, video_error


class VideoScene(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    asset_id: str | None = Field(default=None, min_length=36, max_length=36)
    duration_seconds: float = Field(default=5, ge=1, le=15)
    caption: str = Field(default="", max_length=180)
    narration_text: str | None = Field(default=None, max_length=400)
    speech_asset_id: str | None = Field(default=None, min_length=36, max_length=36)
    scene_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9:_-]+$")
    purpose: Literal["hook", "demo", "benefit", "cta"] | None = None
    visual_description: str | None = Field(default=None, max_length=1200)
    image_prompt: str | None = Field(default=None, max_length=1200)
    regenerate_image: Literal[True] | None = None
    regenerate_video: Literal[True] | None = None
    generation_kind: Literal["image", "video"] | None = None
    source_start_seconds: float | None = Field(default=None, ge=0, le=180)
    motion: Literal["none", "zoom_in", "pan_left", "pan_right"] | None = None
    transition: Literal["cut", "fade"] | None = None

    @model_validator(mode="after")
    def check_visual(self):
        if self.regenerate_image and self.regenerate_video:
            raise ValueError("Choose one visual generation method")
        if (self.regenerate_video or not self.asset_id and self.generation_kind == "video") and self.duration_seconds > 8:
            raise ValueError("Generated video scenes can be at most eight seconds long")
        if (not self.asset_id or self.regenerate_image or self.regenerate_video) and not (self.image_prompt or "").strip():
            raise ValueError("A generated scene requires an image prompt")
        return self


class VideoCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    brief: str = Field(min_length=1, max_length=4000)
    request_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9:_-]+$")
    mode: Literal["manual", "autopilot"] = "manual"
    language: Literal["ru", "en"] = "ru"
    duration_seconds: int = Field(default=20, ge=10, le=45)
    style: str = Field(default="", max_length=500)
    generated_visuals: Literal["images", "video"] = "images"
    template: Literal["problem_solution", "product_demo", "tutorial"] | None = None
    brand_name: str | None = Field(default=None, max_length=80)
    brand_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    narration_enabled: bool = False
    voice: Literal["Kore", "Charon", "Puck"] = "Kore"
    voice_style: Literal["neutral", "warm", "energetic"] = "neutral"
    billing_reservation_id: str | None = Field(default=None, min_length=36, max_length=36)
    billing_budget_units: int = Field(default=0, ge=0, le=100_000_000)
    scenes: list[VideoScene] = Field(default_factory=list, max_length=MAX_SCENES)

    @model_validator(mode="after")
    def check_duration(self):
        scene_ids = [scene.scene_id for scene in self.scenes if scene.scene_id]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("Scene IDs must be unique")
        if self.narration_enabled and self.mode == "manual" and not self.scenes:
            raise PydanticCustomError("video_speech_needs_scenes", "Narration requires video scenes")
        if sum(s.duration_seconds for s in self.scenes) > MAX_DURATION:
            raise PydanticCustomError("video_duration_limit", "Maximum video duration is {maximum} seconds", {"maximum": MAX_DURATION})
        return self


class VideoRevise(VideoCreate):
    expected_version: int = Field(ge=1)


def capabilities() -> dict:
    settings = get_settings()
    funded = bool(settings.video_ai_enabled and settings.ai_gateway_base_url and settings.ai_gateway_default_model
                     and (settings.postbridge_app_mode == "saas"
                     and settings.saas_base_url and settings.status_event_webhook_token
                     or settings.postbridge_app_mode == "selfhost" and settings.video_ai_selfhost_budget_units > 0))
    autopilot = bool(funded and settings.ai_image_generation_model)
    return {"enabled": settings.video_studio_enabled, "generated_media": autopilot, "autopilot_enabled": autopilot,
            "speech_enabled": bool(funded and settings.video_speech_enabled and settings.video_speech_model == "gemini-2.5-flash-tts"),
            "video_generation_enabled": bool(funded and settings.video_generation_enabled and settings.video_generation_model in {
                "veo-3.1-fast-generate-001", "veo-3.1-fast-generate-001:720p"}),
            "speech_languages": ["ru", "en"], "speech_voices": ["Kore", "Charon", "Puck"],
            "max_upload_bytes": MAX_UPLOAD_BYTES, "max_scenes": MAX_SCENES, "max_duration_seconds": MAX_DURATION,
            "width": WIDTH, "height": HEIGHT, "supports_schedules": bool(settings.video_studio_enabled and
                (settings.postbridge_app_mode == "selfhost" or settings.saas_base_url and settings.status_event_webhook_token))}


def require_enabled():
    if not get_settings().video_studio_enabled:
        raise video_error("VIDEO_STUDIO_DISABLED", "Video studio is not enabled on this installation")


def _project(session: Session, tenant_id: str, project_id: str, *, lock=False) -> VideoProjectOrm:
    if lock:
        # Keep the same lock order for UI edits, batch variants and media admission.
        session.scalar(select(TenantOrm).where(TenantOrm.id == tenant_id).with_for_update())
    stmt = select(VideoProjectOrm).where(VideoProjectOrm.id == project_id, VideoProjectOrm.tenant_id == tenant_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(stmt)
    if row is None:
        raise video_error("VIDEO_PROJECT_NOT_FOUND", "Video project not found")
    return row


def _check_assets(session: Session, tenant_id: str, scenes: list[dict]):
    for asset_id in {s.get("asset_id") for s in scenes} - {None}:
        asset = session.get(MediaAssetOrm, asset_id)
        if asset is None or asset.tenant_id != tenant_id or not asset.content_type.startswith(("image/", "video/")):
            raise video_error("VIDEO_ASSET_NOT_FOUND", "One of the selected materials is unavailable")
    from postbridge.models.domain import VideoMaterialOrm
    for scene in scenes:
        material = session.get(VideoMaterialOrm, scene.get("asset_id")) if scene.get("asset_id") else None
        start = scene.get("source_start_seconds") or 0
        if material:
            meta = material.metadata_json or {}
            if start and (meta.get("kind") != "video" or meta.get("duration_seconds") and start + scene["duration_seconds"] > meta["duration_seconds"] + .05):
                raise video_error("VIDEO_INVALID_TRIM", "Choose a start within the source clip")
        elif start:
            raise video_error("VIDEO_INVALID_TRIM", "Trim requires an inspected library video")
    import hashlib
    for scene in scenes:
        if not scene.get("speech_asset_id"):
            continue
        speech = session.get(VideoSpeechAssetOrm, scene["speech_asset_id"])
        if (speech is None or speech.tenant_id != tenant_id
                or speech.text_hash != hashlib.sha256((scene.get("narration_text") or "").encode()).hexdigest()):
            raise video_error("VIDEO_SPEECH_ASSET_INVALID", "The narration does not match its generated audio")
        if scene["duration_seconds"] + 0.0001 < speech.sample_count / 24000:
            raise video_error("VIDEO_SPEECH_TOO_LONG", "A scene cannot cut off its narration")


def project_payload(session: Session, project: VideoProjectOrm) -> dict:
    from postbridge.models.domain import VideoPostDraftOrm
    from postbridge.services.video_post_drafts import draft_payload
    revisions = session.scalars(select(VideoRevisionOrm).where(VideoRevisionOrm.project_id == project.id)
                                .order_by(VideoRevisionOrm.version.desc())).all()
    drafts = {row.revision_id: row for row in session.scalars(select(VideoPostDraftOrm).where(
        VideoPostDraftOrm.tenant_id == project.tenant_id, VideoPostDraftOrm.revision_id.in_([r.id for r in revisions])))}
    items = []
    for revision in revisions:
        job = session.scalar(select(VideoProductionJobOrm).where(VideoProductionJobOrm.revision_id == revision.id))
        asset = session.get(MediaAssetOrm, job.output_asset_id) if job and job.output_asset_id else None
        public_scenes = []
        progress = (job.progress_json or {}) if job else {}
        planned = progress.get("steps", {}).get("storyboard", {}).get("result", {}).get("scenes", [])
        # The original manifest remains immutable. Expose verified checkpoints so
        # a failed neighbour does not make the user repay a completed visual.
        effective_scenes = [dict(scene) for scene in (progress.get("scenes") or planned or revision.scenes)]
        for index, scene in enumerate(effective_scenes):
            steps = progress.get("steps", {})
            native_step = steps.get(f"video:{index}", {})
            image_step = steps.get(f"image:{index}", {})
            completed = native_step if native_step.get("state") == "done" else image_step
            source_id = completed.get("result", {}).get("asset_id") if completed.get("state") == "done" else None
            source = session.get(MediaAssetOrm, source_id) if source_id else None
            if source and source.tenant_id == project.tenant_id:
                scene["asset_id"] = source.id
                scene.pop("regenerate_video", None)
                scene.pop("regenerate_image", None)
                scene.pop("source_start_seconds", None)
        for index, scene in enumerate(effective_scenes):
            source = session.get(MediaAssetOrm, scene["asset_id"]) if scene.get("asset_id") else None
            speech_asset = session.get(MediaAssetOrm, scene["speech_asset_id"]) if scene.get("speech_asset_id") else None
            preview = next((item for item in progress.get("previews", []) if item["scene_number"] == index + 1), None)
            preview_asset = session.get(MediaAssetOrm, preview["asset_id"]) if preview else None
            public_scenes.append({**scene, **({"preview_url": managed_media_url(preview_asset.id, preview_asset.object_key),
                "preview_at_seconds": preview["at_seconds"]} if preview_asset and preview_asset.tenant_id == project.tenant_id else {}), "url": managed_media_url(source.id, source.object_key) if source else None,
                                  "speech_url": managed_media_url(speech_asset.id, speech_asset.object_key) if speech_asset else None,
                                  "kind": "video" if source and source.content_type.startswith("video/") else "image"})
        items.append({"id": revision.id, "version": revision.version, "scenes": public_scenes,
                      "status": job.status if job else "draft", "stage": job.stage if job else "materials",
                      "job_id": job.id if job else None, "error_code": job.error_code if job else None,
                      "output_url": managed_media_url(asset.id, asset.object_key) if asset else None,
                      "output_asset_id": asset.id if asset else None,
                      "storyboard": {"schema_version": 1, "scenes": public_scenes,
                          "analyses": {key.split(":", 1)[1]: step.get("result") for key, step in progress.get("steps", {}).items()
                              if key.startswith("analysis:") and step.get("state") == "done"}},
                      **{key: (revision.production_options or {}).get(key) for key in ("template", "brand_name", "brand_color")},
                      "created_at": revision.created_at, "mode": (revision.production_options or {}).get("mode", "manual"),
                      "language": (revision.production_options or {}).get("language", "ru"),
                      "narration_enabled": (revision.production_options or {}).get("narration_enabled", False),
                      "generated_visuals": (revision.production_options or {}).get("generated_visuals", "images"),
                      "voice": (revision.production_options or {}).get("voice", "Kore"),
                      "voice_style": (revision.production_options or {}).get("voice_style", "neutral"),
                      "source_version": (revision.production_options or {}).get("source_version"),
                      "post_draft": draft_payload(session, drafts[revision.id]) if revision.id in drafts else None,
                      "usage": job_usage(job)})
    from postbridge.models.domain import VideoAgentExecutionOrm
    origin = session.scalar(select(VideoAgentExecutionOrm).where(VideoAgentExecutionOrm.project_id == project.id))
    if not origin:
        from postbridge.models.domain import VideoBatchItemOrm
        origin = session.scalar(select(VideoAgentExecutionOrm).join(VideoBatchItemOrm,
            VideoBatchItemOrm.batch_id == VideoAgentExecutionOrm.batch_id).where(VideoBatchItemOrm.project_id == project.id))
    return {"id": project.id, "brief": project.brief, "current_version": project.current_version,
            "origin": {"agent_run_id": origin.id, "agent_task_id": origin.task_id} if origin else None,
            "created_at": project.created_at, "updated_at": project.updated_at, "revisions": items,
            "current": next(item for item in items if item["version"] == project.current_version)}


def list_projects(session: Session, tenant_id: str, limit=20, offset=0) -> dict:
    rows = session.scalars(select(VideoProjectOrm).where(VideoProjectOrm.tenant_id == tenant_id)
                           .order_by(VideoProjectOrm.created_at.desc(), VideoProjectOrm.id)
                           .offset(offset).limit(limit + 1)).all()
    return {"items": [project_payload(session, row) for row in rows[:limit]], "has_more": len(rows) > limit}


def get_project(session: Session, tenant_id: str, project_id: str) -> dict:
    return project_payload(session, _project(session, tenant_id, project_id))


def job_usage(job) -> dict:
    progress = (job.progress_json or {}) if job else {}
    steps = progress.get("steps", {}).values()
    return {"tokens_charged": sum(s.get("units", 0) for s in steps),
            "uncertain": any(s.get("state") in {"started", "unknown", "video_pending"} for s in steps),
            "reservation_id": progress.get("billing", {}).get("reservation_id")}


def production_options(payload) -> dict:
    options = {key: getattr(payload, key) for key in ("mode", "language", "duration_seconds", "style")}
    if payload.generated_visuals != "images":
        options["generated_visuals"] = payload.generated_visuals
    if payload.narration_enabled or any(scene.speech_asset_id for scene in payload.scenes):
        options.update(narration_enabled=payload.narration_enabled, voice=payload.voice, voice_style=payload.voice_style)
    options.update({key: getattr(payload, key) for key in ("template", "brand_name", "brand_color") if getattr(payload, key) is not None})
    return options


def project_by_request(session: Session, tenant_id: str, request_key: str) -> dict:
    revision = session.scalar(select(VideoRevisionOrm).where(VideoRevisionOrm.tenant_id == tenant_id,
                                                           VideoRevisionOrm.request_key == request_key))
    if revision is None:
        raise video_error("VIDEO_REQUEST_NOT_FOUND", "Video request not found")
    result = get_project(session, tenant_id, revision.project_id)
    result["requested_revision"] = next(r for r in result["revisions"] if r["id"] == revision.id)
    return result


def _add_revision(session: Session, project: VideoProjectOrm, payload: VideoCreate):
    scenes = [s.model_dump(exclude_none=True) for s in payload.scenes]
    # Serialize admission with media deletion and other submissions in this tenant.
    session.scalar(select(TenantOrm).where(TenantOrm.id == project.tenant_id).with_for_update())
    if session.scalar(select(VideoRevisionOrm.id).where(VideoRevisionOrm.tenant_id == project.tenant_id,
                                                        VideoRevisionOrm.request_key == payload.request_key)):
        raise video_error("VIDEO_REQUEST_CONFLICT", "This request was already used for another video")
    autopilot = payload.mode == "autopilot"
    generated = any(not s.get("asset_id") or s.get("regenerate_image") or s.get("regenerate_video") for s in scenes)
    video_generated = autopilot and payload.generated_visuals == "video" or any(s.get("regenerate_video") or s.get("generation_kind") == "video" and not s.get("asset_id") for s in scenes)
    if video_generated and not capabilities()["video_generation_enabled"]:
        raise video_error("VIDEO_GENERATION_UNAVAILABLE", "AI video scene generation is not configured")
    paid = autopilot or payload.narration_enabled or generated
    if autopilot and any(scene.get("speech_asset_id") for scene in scenes):
        raise video_error("VIDEO_SPEECH_ASSET_INVALID", "Reuse narrated scenes in manual assembly or create a voice variant")
    if payload.narration_enabled and not capabilities()["speech_enabled"]:
        raise video_error("VIDEO_SPEECH_UNAVAILABLE", "Google speech generation is not configured")
    if (autopilot or generated) and not capabilities()["autopilot_enabled"]:
        raise video_error("VIDEO_AUTOPILOT_UNAVAILABLE", "Video autopilot is not configured")
    settings = get_settings()
    if paid and settings.postbridge_app_mode == "saas" and (not payload.billing_reservation_id or payload.billing_budget_units <= 0):
        raise video_error("VIDEO_BUDGET_NOT_AUTHORIZED", "Autopilot requires a budget reservation")
    if scenes or autopilot:
        active = session.scalar(select(func.count()).select_from(VideoProductionJobOrm).where(
            VideoProductionJobOrm.tenant_id == project.tenant_id,
            VideoProductionJobOrm.status.in_(["queued", "running"])))
        if active >= 2:
            raise video_error("VIDEO_QUEUE_FULL", "Two videos are already being assembled; wait or cancel one")
    _check_assets(session, project.tenant_id, scenes)
    revision = VideoRevisionOrm(id=str(uuid4()), tenant_id=project.tenant_id, project_id=project.id,
                                version=project.current_version, request_key=payload.request_key, scenes=scenes, production_options=production_options(payload))
    session.add(revision)
    session.flush()
    if scenes or autopilot:
        billing = {"request_key": payload.request_key, "reservation_id": payload.billing_reservation_id,
                   "limit_units": payload.billing_budget_units if settings.postbridge_app_mode == "saas" else
                       min(settings.video_ai_selfhost_budget_units, payload.billing_budget_units or settings.video_ai_selfhost_budget_units)}
        session.add(VideoProductionJobOrm(id=str(uuid4()), tenant_id=project.tenant_id, revision_id=revision.id,
            status="queued", stage="queued", attempts=0, progress_json={"billing": billing, "steps": {}} if paid else {}))


def create_project(session: Session, tenant_id: str, payload: VideoCreate, *, agent_run_id: str | None = None, commit: bool = True) -> dict:
    require_enabled()
    if payload.request_key.startswith("agent-video:") and payload.request_key != f"agent-video:{agent_run_id}":
        raise video_error("VIDEO_REQUEST_CONFLICT", "Agent video request keys belong to their scheduled run")
    if session.scalar(select(TenantOrm).where(TenantOrm.id == tenant_id).with_for_update()) is None:
        raise video_error("VIDEO_TENANT_NOT_FOUND", "Workspace not found")
    row = session.scalar(select(VideoProjectOrm).where(VideoProjectOrm.tenant_id == tenant_id,
                                                       VideoProjectOrm.request_key == payload.request_key))
    if row:
        _check_replay(session, row, payload)
        return project_by_request(session, tenant_id, payload.request_key)
    row = VideoProjectOrm(id=str(uuid4()), tenant_id=tenant_id, brief=payload.brief,
                          request_key=payload.request_key, current_version=1)
    try:
        session.add(row)
        session.flush()
        _add_revision(session, row, payload)
        session.commit() if commit else session.flush()
    except IntegrityError:
        session.rollback()
        row = session.scalar(select(VideoProjectOrm).where(VideoProjectOrm.tenant_id == tenant_id,
                                                           VideoProjectOrm.request_key == payload.request_key))
        if row is None:
            raise
        _check_replay(session, row, payload)
    return project_by_request(session, tenant_id, payload.request_key)


def _check_replay(session: Session, project: VideoProjectOrm, payload: VideoCreate):
    revision = session.scalar(select(VideoRevisionOrm).where(VideoRevisionOrm.project_id == project.id,
                                                            VideoRevisionOrm.request_key == payload.request_key))
    legacy_options = {"mode": "manual", "language": "ru", "duration_seconds": 20, "style": ""}
    if (revision is None or revision.scenes != [s.model_dump(exclude_none=True) for s in payload.scenes]
            or project.brief != payload.brief or (revision.production_options or legacy_options) != production_options(payload)):
        raise video_error("VIDEO_REQUEST_CONFLICT", "This request was already used with different inputs")


def revise_project(session: Session, tenant_id: str, project_id: str, payload: VideoRevise) -> dict:
    require_enabled()
    if payload.request_key.startswith("agent-video:"):
        raise video_error("VIDEO_REQUEST_CONFLICT", "Agent video requests create their own projects")
    project = _project(session, tenant_id, project_id, lock=True)
    replay = session.scalar(select(VideoRevisionOrm).where(VideoRevisionOrm.project_id == project_id,
                                                           VideoRevisionOrm.request_key == payload.request_key))
    if replay:
        _check_replay(session, project, payload)
        return project_by_request(session, tenant_id, payload.request_key)
    if project.current_version != payload.expected_version or project.brief != payload.brief:
        raise video_error("VIDEO_VERSION_CONFLICT", "The project changed; refresh before editing")
    if project.current_version >= 100:
        raise video_error("VIDEO_REVISION_LIMIT", "This project has reached its revision limit")
    project.current_version += 1
    project.updated_at = datetime.now(UTC)
    _add_revision(session, project, payload)
    session.commit()
    return project_by_request(session, tenant_id, payload.request_key)


def cancel_project(session: Session, tenant_id: str, project_id: str, expected_version: int, source_version: int | None = None) -> dict:
    project = _project(session, tenant_id, project_id, lock=True)
    if expected_version != project.current_version:
        raise video_error("VIDEO_VERSION_CONFLICT", "The project changed; refresh before cancelling")
    revision = session.scalar(select(VideoRevisionOrm).where(VideoRevisionOrm.project_id == project.id,
                                                            VideoRevisionOrm.version == (source_version or expected_version)))
    if revision is None:
        raise video_error("VIDEO_VERSION_CONFLICT", "Video version not found")
    job = session.scalar(select(VideoProductionJobOrm).where(VideoProductionJobOrm.revision_id == revision.id).with_for_update())
    if job and job.status in {"queued", "running"}:
        job.status = "cancelled"
        job.stage = "cancelled"
        job.completed_at = datetime.now(UTC)
    session.commit()
    return project_payload(session, project)


def store_video_material(session: Session, tenant_id: str, path: Path, *, asset_id: str | None = None, name: str | None = None) -> dict:
    if asset_id is None:
        require_enabled()
    if session.get(TenantOrm, tenant_id) is None:
        raise video_error("VIDEO_TENANT_NOT_FOUND", "Workspace not found")
    if path.stat().st_size > MAX_UPLOAD_BYTES:
        raise video_error("VIDEO_FILE_TOO_LARGE", "The maximum file size is 64 MB")
    metadata = inspect_media(path)
    asset_id = asset_id or str(uuid4())
    content_type = "video/mp4" if metadata["kind"] == "video" else "image/png"
    # ffprobe verifies the content; detect the supported container for correct HTTP serving.
    header = path.open("rb")
    with header:
        magic = header.read(16)
    if magic.startswith(bytes([137, 80, 78, 71])):
        content_type = "image/png"
    elif magic[:3] == bytes([255, 216, 255]):
        content_type = "image/jpeg"
    elif magic[:4] == b"RIFF":
        content_type = "image/webp"
    elif magic[:4] == bytes([26, 69, 223, 163]):
        content_type = "video/webm"
    extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
                 "video/mp4": "mp4", "video/webm": "webm"}[content_type]
    key = f"tenants/{tenant_id}/media/{asset_id}.{extension}"
    upload_media_file(key, path, content_type)
    session.add(MediaAssetOrm(id=asset_id, tenant_id=tenant_id, object_key=key, content_type=content_type,
                             byte_size=path.stat().st_size))
    from postbridge.models.domain import VideoMaterialOrm
    session.add(VideoMaterialOrm(asset_id=asset_id, tenant_id=tenant_id, name=(name or "Material")[:180],
        folder="", metadata_json=metadata))
    session.commit()
    return {"media_asset_id": asset_id, "url": managed_media_url(asset_id, key), **metadata}
