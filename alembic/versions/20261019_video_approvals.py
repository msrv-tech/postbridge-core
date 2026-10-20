"""Immutable video approval and canonical publication-plan handoff."""
from alembic import op
import sqlalchemy as sa
revision = "20261019_video_approvals"
down_revision = "20261019_video_post_drafts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("video_publication_approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("post_draft_id", sa.String(36), sa.ForeignKey("video_post_drafts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("publication_plan_id", sa.String(36), sa.ForeignKey("publication_plans.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("post_draft_id"), sa.UniqueConstraint("publication_plan_id"), if_not_exists=True)
    op.create_index("ix_video_publication_approvals_tenant_id", "video_publication_approvals", ["tenant_id"], if_not_exists=True)


def downgrade():
    op.drop_table("video_publication_approvals")
