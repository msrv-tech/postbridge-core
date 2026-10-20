"""Idempotent handoff from a completed video revision to an editable post draft."""
from alembic import op
import sqlalchemy as sa

revision = "20261019_video_post_drafts"
down_revision = "20261019_video_agent_schedules"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("video_post_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_id", sa.String(36), sa.ForeignKey("video_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_item_id", sa.String(36), sa.ForeignKey("content_items.id", ondelete="SET NULL")),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("revision_id"), sa.UniqueConstraint("content_item_id"), if_not_exists=True)
    op.create_index("ix_video_post_drafts_tenant_id", "video_post_drafts", ["tenant_id"], if_not_exists=True)


def downgrade():
    op.drop_table("video_post_drafts")
