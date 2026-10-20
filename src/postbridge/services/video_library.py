"""Tenant-owned, reusable media with inspected metadata and bounded browsing."""
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from postbridge.infrastructure.media_storage import managed_media_url
from postbridge.models.domain import MediaAssetOrm, VideoMaterialOrm
from postbridge.services.video_media import video_error


class MaterialUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=180)
    folder: str = Field(default="", max_length=120)


def material_payload(row, asset):
    return {"asset_id": asset.id, "name": row.name, "folder": row.folder,
        "url": managed_media_url(asset.id, asset.object_key), "created_at": row.created_at,
        **row.metadata_json}


def list_materials(session, tenant_id, *, folder=None, search="", limit=20, offset=0):
    stmt = select(VideoMaterialOrm, MediaAssetOrm).join(MediaAssetOrm, MediaAssetOrm.id == VideoMaterialOrm.asset_id).where(
        VideoMaterialOrm.tenant_id == tenant_id, MediaAssetOrm.tenant_id == tenant_id)
    if folder is not None:
        stmt = stmt.where(VideoMaterialOrm.folder == folder)
    if search:
        stmt = stmt.where(VideoMaterialOrm.name.icontains(search, autoescape=True))
    rows = session.execute(stmt.order_by(VideoMaterialOrm.created_at.desc(), VideoMaterialOrm.asset_id).offset(offset).limit(limit + 1)).all()
    folders = session.scalars(select(VideoMaterialOrm.folder).where(VideoMaterialOrm.tenant_id == tenant_id).distinct().order_by(VideoMaterialOrm.folder).limit(200)).all()
    return {"items": [material_payload(r, a) for r, a in rows[:limit]], "has_more": len(rows) > limit, "folders": folders}


def update_material(session, tenant_id, asset_id, payload):
    row = session.scalar(select(VideoMaterialOrm).where(VideoMaterialOrm.asset_id == asset_id,
        VideoMaterialOrm.tenant_id == tenant_id).with_for_update())
    asset = session.get(MediaAssetOrm, asset_id)
    if not row or not asset or asset.tenant_id != tenant_id:
        raise video_error("VIDEO_ASSET_NOT_FOUND", "Material not found")
    row.name, row.folder = payload.name, payload.folder
    session.commit()
    return material_payload(row, asset)
