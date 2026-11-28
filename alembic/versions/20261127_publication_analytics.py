"""Store bounded, per-publication analytics history."""

from alembic import op
import sqlalchemy as sa

revision = "20261127_publication_analytics"
down_revision = "20261126_material_selections"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "publication_analytics",
        sa.Column("target_id", sa.String(36), sa.ForeignKey("publication_targets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("external_post_id", sa.String(256), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("attempted_at", sa.DateTime(timezone=True)),
        sa.Column("claim_id", sa.String(36)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("detail", sa.String(48)),
        sa.Column("history_expires_at", sa.DateTime(timezone=True)),
        sa.Column("history", sa.JSON(), nullable=False),
    )
    op.create_index("ix_publication_analytics_next_attempt", "publication_analytics", ["next_attempt_at"])
    op.create_index("ix_publication_analytics_expires", "publication_analytics", ["history_expires_at"])
    op.create_table(
        "channel_audience_analytics",
        sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("external_id", sa.String(256), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("claim_id", sa.String(36)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("detail", sa.String(48)),
        sa.Column("history_expires_at", sa.DateTime(timezone=True)),
        sa.Column("history", sa.JSON(), nullable=False),
    )
    op.create_index("ix_channel_audience_due", "channel_audience_analytics", ["next_attempt_at"])
    op.create_index("ix_channel_audience_expires", "channel_audience_analytics", ["history_expires_at"])


def downgrade():
    op.drop_index("ix_channel_audience_expires", table_name="channel_audience_analytics")
    op.drop_index("ix_channel_audience_due", table_name="channel_audience_analytics")
    op.drop_table("channel_audience_analytics")
    op.drop_index("ix_publication_analytics_expires", table_name="publication_analytics")
    op.drop_index("ix_publication_analytics_next_attempt", table_name="publication_analytics")
    op.drop_table("publication_analytics")
