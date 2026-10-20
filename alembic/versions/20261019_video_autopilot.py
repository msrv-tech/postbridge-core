"""Persist autopilot input options and checkpointed paid steps."""
from alembic import op
import sqlalchemy as sa
revision = "20261019_video_autopilot"
down_revision = "20261019_video_studio"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for table, name in [("video_revisions", "production_options"), ("video_production_jobs", "progress_json")]:
        if name not in {c["name"] for c in sa.inspect(bind).get_columns(table)}:
            op.add_column(table, sa.Column(name, sa.JSON(), server_default=sa.text("'{}'"), nullable=False))
    op.create_index("uq_video_tenant_request", "video_revisions", ["tenant_id", "request_key"], unique=True, if_not_exists=True)


def downgrade():
    op.drop_index("uq_video_tenant_request", table_name="video_revisions")
    op.drop_column("video_production_jobs", "progress_json")
    op.drop_column("video_revisions", "production_options")
