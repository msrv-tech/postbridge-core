"""Versioned delivery time and missed-deadline protection for video approvals."""
from alembic import op
import sqlalchemy as sa
revision = "20261019_video_delivery_time"
down_revision = "20261019_video_approvals"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("video_publication_approvals")}
    for column in [sa.Column("initial_timing", sa.JSON()),
                   sa.Column("schedule_version", sa.Integer(), nullable=False, server_default="1"),
                   sa.Column("schedule_history", sa.JSON(), nullable=False, server_default="[]"),
                   sa.Column("deadline_at", sa.DateTime(timezone=True))]:
        if column.name not in columns:
            op.add_column("video_publication_approvals", column)


def downgrade():
    active = op.get_bind().execute(sa.text("""
        SELECT 1 FROM video_publication_approvals AS approval
        JOIN publication_targets AS target ON target.publication_plan_id = approval.publication_plan_id
        WHERE approval.deadline_at IS NOT NULL AND target.status = 'pending' LIMIT 1
    """)).first()
    if active:
        raise RuntimeError("Cancel pending timed video deliveries before downgrading delivery-window support")
    for name in ["deadline_at", "schedule_history", "schedule_version", "initial_timing"]:
        op.drop_column("video_publication_approvals", name)
