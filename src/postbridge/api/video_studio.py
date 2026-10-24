"""Shared video API for the hosted BFF and authenticated self-host app."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from postbridge.api.service_auth import require_service_tenant
from postbridge.db import get_db_session
from postbridge.services import video_studio as service
from postbridge.services.video_media import MAX_UPLOAD_BYTES, video_error
from postbridge.services.video_post_drafts import PrepareVideoPost, prepare_post_draft
from postbridge.services.video_publication import ApproveVideoPublication, approve_publication, publication_view, retry_failed_target, cancel_pending
from postbridge.services.video_publication import reconcile_target
from postbridge.services.video_delivery_time import RescheduleVideoPublication, reschedule_publication
from postbridge.services.video_speech import VoiceVariantRequest, create_voice_variant
from postbridge.services.video_library import MaterialUpdate, list_materials, update_material
from postbridge.services import video_batches as batches
from postbridge.services import video_schedules as schedules


def selfhost_tenant(request: Request, session: Session = Depends(get_db_session)) -> str:
    from postbridge.api.app_public import _require_selfhost_tenant, require_selfhost_app_auth
    require_selfhost_app_auth(request, authorization=request.headers.get("Authorization"), session=session)
    return _require_selfhost_tenant(session).id


class CancelVideo(BaseModel):
    expected_version: int = Field(ge=1)
    source_version: int | None = Field(default=None, ge=1)


def build_router(prefix, tenant_dependency):
    router = APIRouter(prefix=prefix, tags=["video-studio"])

    @router.get("/capabilities")
    def capabilities(tenant_id: str = Depends(tenant_dependency)):
        return service.capabilities()

    @router.get("/generation-price")
    def generation_price(tenant_id: str = Depends(tenant_dependency)):
        from postbridge.services.video_generation import generation_price
        return generation_price()

    @router.get("/schedules")
    def video_schedules(tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        from sqlalchemy import select
        from postbridge.models.domain import AgentTaskOrm
        rows = session.scalars(select(AgentTaskOrm).where(AgentTaskOrm.tenant_id == tenant_id,
            AgentTaskOrm.mode == "video_autopilot").order_by(AgentTaskOrm.created_at.desc()).limit(200))
        return {"items": [schedules.schedule_payload(row) for row in rows]}

    @router.patch("/schedules/{task_id}")
    def edit_video_schedule(task_id: UUID, payload: schedules.VideoScheduleEdit,
                            tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        row = schedules.update_video_agent_task(session, tenant_id=tenant_id, task_id=str(task_id), **payload.model_dump())
        session.commit()
        return schedules.schedule_payload(row)

    @router.get("/tiktok/channels/{channel_id}/creator-info")
    def tiktok_creator_info(channel_id: UUID, tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        from postbridge.services.tiktok import get_creator_info
        return get_creator_info(session, tenant_id, str(channel_id))

    @router.delete("/tiktok/channels/{channel_id}/connection")
    def disconnect_tiktok(channel_id: UUID, tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        from postbridge.services.tiktok import disconnect
        return disconnect(session, tenant_id, str(channel_id))

    @router.get("")
    def list_projects(limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0),
                      tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        return service.list_projects(session, tenant_id, limit, offset)

    @router.post("", status_code=201)
    def create(payload: service.VideoCreate, tenant_id: str = Depends(tenant_dependency),
               session: Session = Depends(get_db_session)):
        return service.create_project(session, tenant_id, payload)

    from postbridge.services import video_auto_publication as automatic

    @router.get("/schedule-bridges")
    def schedule_bridges(channel_id: UUID, tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        return automatic.schedule_bridges(session, tenant_id, str(channel_id))

    @router.post("/schedules/runs/{run_id}/auto-posts")
    def auto_posts(run_id: UUID, tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        return automatic.prepare_auto_posts(session, tenant_id, str(run_id))

    @router.get("/schedules/runs/{run_id}/posts/{content_id}/approval")
    def auto_approval(run_id: UUID, content_id: UUID, tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        return automatic.auto_approval(session, tenant_id, str(run_id), str(content_id)).model_dump(mode="json")

    @router.post("/schedules/runs/{run_id}/posts/{content_id}/approve")
    def auto_approve(run_id: UUID, content_id: UUID, payload: ApproveVideoPublication,
                     tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        return automatic.approve_auto_post(session, tenant_id, str(run_id), str(content_id), payload)

    @router.get("/materials")
    def materials(folder: str | None = Query(None, max_length=120), search: str = Query("", max_length=180),
                  limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0),
                  tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        return list_materials(session, tenant_id, folder=folder, search=search, limit=limit, offset=offset)

    @router.patch("/materials/{asset_id}")
    def material_update(asset_id: UUID, payload: MaterialUpdate, tenant_id: str = Depends(tenant_dependency),
                        session: Session = Depends(get_db_session)):
        return update_material(session, tenant_id, str(asset_id), payload)

    @router.post("/media/upload", status_code=201)
    async def upload(file: UploadFile = File(...), tenant_id: str = Depends(tenant_dependency),
                     session: Session = Depends(get_db_session)):
        service.require_enabled()
        with TemporaryDirectory(prefix="postbridge-upload-") as tmp:
            path, size = Path(tmp) / "material", 0
            with path.open("wb") as destination:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise video_error("VIDEO_FILE_TOO_LARGE", "The maximum file size is 64 MB")
                    destination.write(chunk)
            # UploadFile is spooled by Starlette; probing and storage never block the event loop.
            return await asyncio.to_thread(service.store_video_material, session, tenant_id, path, name=file.filename)

    @router.get("/by-request/{request_key}")
    def by_request(request_key: str, tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        return service.project_by_request(session, tenant_id, request_key)

    @router.get("/batches")
    def list_batches(project_id: UUID | None = None, limit: int = Query(20, ge=1, le=50),
                     tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        return batches.list_batches(session, tenant_id, project_id=str(project_id) if project_id else None, limit=limit)

    from postbridge.services.video_metrics import VideoMetricsRequest, read_video_metrics

    @router.post("/publication-metrics")
    def publication_metrics(payload: VideoMetricsRequest, tenant_id: str = Depends(tenant_dependency),
                            session: Session = Depends(get_db_session)):
        return read_video_metrics(session, tenant_id, payload)

    @router.post("/batches", status_code=201)
    def create_batch(payload: batches.ProjectBatchCreate, tenant_id: str = Depends(tenant_dependency),
                     session: Session = Depends(get_db_session)):
        return batches.create_project_batch(session, tenant_id, payload)

    @router.get("/batches/{batch_id}")
    def get_batch(batch_id: UUID, tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        return batches.get_batch(session, tenant_id, str(batch_id))

    @router.post("/batches/{batch_id}/cancel")
    def cancel_batch(batch_id: UUID, tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        return batches.cancel_batch(session, tenant_id, str(batch_id))

    @router.post("/{project_id}/voice-batches", status_code=201)
    def create_voice_batch(project_id: UUID, payload: batches.VoiceBatchCreate,
                           tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        return batches.create_voice_batch(session, tenant_id, str(project_id), payload)

    @router.get("/posts/{content_id}/publication")
    def publication(content_id: UUID, tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        return publication_view(session, tenant_id, str(content_id))

    @router.post("/posts/{content_id}/publication/approve")
    def approve(content_id: UUID, payload: ApproveVideoPublication, tenant_id: str = Depends(tenant_dependency),
                session: Session = Depends(get_db_session)):
        return approve_publication(session, tenant_id, str(content_id), payload)

    @router.post("/posts/{content_id}/publication/targets/{target_id}/retry")
    def retry(content_id: UUID, target_id: UUID, tenant_id: str = Depends(tenant_dependency),
              session: Session = Depends(get_db_session)):
        return retry_failed_target(session, tenant_id, str(content_id), str(target_id))

    @router.post("/posts/{content_id}/publication/cancel")
    def cancel_delivery(content_id: UUID, tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        return cancel_pending(session, tenant_id, str(content_id))

    @router.post("/posts/{content_id}/publication/targets/{target_id}/reconcile")
    def reconcile_delivery(content_id: UUID, target_id: UUID, tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        return reconcile_target(session, tenant_id, str(content_id), str(target_id))

    @router.post("/posts/{content_id}/publication/reschedule")
    def reschedule_delivery(content_id: UUID, payload: RescheduleVideoPublication, tenant_id: str = Depends(tenant_dependency),
                            session: Session = Depends(get_db_session)):
        return reschedule_publication(session, tenant_id, str(content_id), payload)

    @router.get("/{project_id}")
    def get(project_id: UUID, tenant_id: str = Depends(tenant_dependency), session: Session = Depends(get_db_session)):
        return service.get_project(session, tenant_id, str(project_id))

    @router.post("/{project_id}/revisions", status_code=201)
    def revise(project_id: UUID, payload: service.VideoRevise, tenant_id: str = Depends(tenant_dependency),
               session: Session = Depends(get_db_session)):
        return service.revise_project(session, tenant_id, str(project_id), payload)

    @router.post("/{project_id}/cancel")
    def cancel(project_id: UUID, payload: CancelVideo, tenant_id: str = Depends(tenant_dependency),
               session: Session = Depends(get_db_session)):
        return service.cancel_project(session, tenant_id, str(project_id), payload.expected_version, payload.source_version)

    @router.post("/{project_id}/voice-variants", status_code=201)
    def voice_variant(project_id: UUID, payload: VoiceVariantRequest, tenant_id: str = Depends(tenant_dependency),
                      session: Session = Depends(get_db_session)):
        return create_voice_variant(session, tenant_id, str(project_id), payload)

    @router.post("/{project_id}/post-draft")
    def prepare_post(project_id: UUID, payload: PrepareVideoPost, tenant_id: str = Depends(tenant_dependency),
                     session: Session = Depends(get_db_session)):
        return prepare_post_draft(session, tenant_id, str(project_id), payload)

    return router


router = build_router("/internal/service/videos", require_service_tenant)
selfhost_router = build_router("/api/app/videos", selfhost_tenant)
