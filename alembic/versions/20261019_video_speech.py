"""Generated speech cache for versioned language variants."""
from alembic import op
import sqlalchemy as sa

revision = "20261019_video_speech"
down_revision = "20261019_video_delivery_time"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("video_speech_assets",
        sa.Column("asset_id", sa.String(36), sa.ForeignKey("media_assets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "cache_key", name="uq_video_speech_cache"),
        sa.CheckConstraint("sample_count > 0 AND sample_count <= 360000", name="ck_video_speech_samples"),
        if_not_exists=True)
    op.create_index("ix_video_speech_assets_tenant_id", "video_speech_assets", ["tenant_id"], if_not_exists=True)


def downgrade():
    # Older workers would silently discard speech or fail on its scene fields.
    if op.get_bind().execute(sa.text("SELECT 1 FROM video_speech_assets LIMIT 1")).first():
        raise RuntimeError("Retain speech support while generated speech assets are in use")
    op.drop_table("video_speech_assets")
