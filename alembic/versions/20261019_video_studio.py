"""Versioned video projects and durable production jobs.

Revision ID: 20261019_video_studio
Revises: 20260822_batch_import_media_urls
"""
from alembic import op
import sqlalchemy as sa

revision = "20261019_video_studio"
down_revision = "20260822_batch_import_media_urls"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("video_projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "request_key", name="uq_video_project_request"), if_not_exists=True)
    op.create_index("ix_video_projects_tenant_id", "video_projects", ["tenant_id"], if_not_exists=True)
    op.create_table("video_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("video_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("scenes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "version", name="uq_video_revision_version"),
        sa.UniqueConstraint("project_id", "request_key", name="uq_video_revision_request"), if_not_exists=True)
    op.create_index("ix_video_revisions_tenant_id", "video_revisions", ["tenant_id"], if_not_exists=True)
    op.create_table("video_production_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_id", sa.String(36), sa.ForeignKey("video_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("output_asset_id", sa.String(36), sa.ForeignKey("media_assets.id")),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("revision_id", name="uq_video_job_revision"),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_video_job_status"), if_not_exists=True)
    op.create_index("ix_video_production_jobs_tenant_id", "video_production_jobs", ["tenant_id"], if_not_exists=True)
    op.create_index("ix_video_jobs_dispatch", "video_production_jobs", ["status", "lease_until"], if_not_exists=True)


def downgrade():
    op.drop_table("video_production_jobs")
    op.drop_table("video_revisions")
    op.drop_table("video_projects")
