"""Durable multilingual and multi-video batches on the existing production queue."""
from alembic import op
import sqlalchemy as sa

revision = "20261022_video_batches"
down_revision = "20261021_video_delivery"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("video_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(128)),
        sa.Column("source_project_id", sa.String(36), sa.ForeignKey("video_projects.id")),
        sa.Column("source_version", sa.Integer()),
        sa.Column("expected_project_version", sa.Integer()),
        sa.Column("policy_json", sa.JSON(), nullable=False),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "request_key", name="uq_video_batch_request"),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_video_batch_status"))
    op.create_index("ix_video_batches_tenant_id", "video_batches", ["tenant_id"])
    op.create_table("video_batch_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("video_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("video_projects.id")),
        sa.Column("revision_id", sa.String(36), sa.ForeignKey("video_revisions.id")),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.UniqueConstraint("batch_id", "position", name="uq_video_batch_position"))
    op.create_index("ix_video_batch_items_batch_id", "video_batch_items", ["batch_id"])
    with op.batch_alter_table("video_agent_executions") as batch:
        batch.add_column(sa.Column("batch_id", sa.String(36)))
        batch.create_foreign_key("fk_video_execution_batch", "video_batches", ["batch_id"], ["id"])


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM video_batches LIMIT 1")).first():
        raise RuntimeError("Video batches must be retained with their production provenance")
    with op.batch_alter_table("video_agent_executions") as batch:
        batch.drop_constraint("fk_video_execution_batch", type_="foreignkey")
        batch.drop_column("batch_id")
    op.drop_table("video_batch_items")
    op.drop_table("video_batches")
