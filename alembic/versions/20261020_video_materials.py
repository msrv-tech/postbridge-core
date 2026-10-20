"""Reusable inspected video materials, scoped to their owner workspace."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_video_materials"
down_revision = "20261019_video_speech"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("video_materials",
        sa.Column("asset_id", sa.String(36), sa.ForeignKey("media_assets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("folder", sa.String(120), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_video_materials_tenant_id", "video_materials", ["tenant_id"])
    assets = sa.table("media_assets", sa.column("id"), sa.column("tenant_id"), sa.column("content_type"), sa.column("created_at"))
    jobs = sa.table("video_production_jobs", sa.column("output_asset_id"))
    library = sa.table("video_materials", sa.column("asset_id"), sa.column("tenant_id"), sa.column("name"), sa.column("folder"),
        sa.column("metadata_json", sa.JSON()), sa.column("created_at"))
    rows = op.get_bind().execute(sa.select(assets).where(
        sa.or_(assets.c.content_type.like("image/%"), assets.c.content_type.like("video/%")),
        ~sa.exists(sa.select(jobs.c.output_asset_id).where(jobs.c.output_asset_id == assets.c.id))))
    for batch in rows.mappings().partitions(500):
        op.get_bind().execute(library.insert(), [{"asset_id": row["id"], "tenant_id": row["tenant_id"],
            "name": "Imported material", "folder": "", "created_at": row["created_at"],
            "metadata_json": {"kind": "video" if row["content_type"].startswith("video/") else "image"}} for row in batch])


def downgrade():
    op.drop_table("video_materials")
