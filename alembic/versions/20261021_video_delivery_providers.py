"""Encrypted checkpoints for multi-stage video deliveries."""
from alembic import op
import sqlalchemy as sa

revision = "20261021_video_delivery"
down_revision = "20261020_video_materials"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("video_delivery_states"):
        return
    op.create_table("video_delivery_states",
        sa.Column("target_id", sa.String(36), sa.ForeignKey("publication_targets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("encrypted_state", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM video_delivery_states LIMIT 1")).first():
        raise RuntimeError("Cannot discard delivery checkpoints; finish or reconcile deliveries first")
    op.drop_table("video_delivery_states")
