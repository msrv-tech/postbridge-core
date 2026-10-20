"""Video production intents on existing agent tasks and runs."""
from alembic import op
import sqlalchemy as sa
revision = "20261019_video_agent_schedules"
down_revision = "20261019_video_autopilot"
branch_labels = None
depends_on = None


def upgrade():
    checks = sa.inspect(op.get_bind()).get_check_constraints("agent_tasks")
    mode = next((c for c in checks if c["name"] == "ck_agent_tasks_mode"), None)
    if mode and "video_autopilot" not in mode["sqltext"]:
        with op.batch_alter_table("agent_tasks") as batch:
            batch.drop_constraint("ck_agent_tasks_mode", type_="check")
            batch.create_check_constraint("ck_agent_tasks_mode", "mode IN ('post_copilot', 'topic_scout', 'video_autopilot')")
    op.create_table("video_agent_executions",
        sa.Column("id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("agent_tasks.id", ondelete="SET NULL")),
        sa.Column("occurrence_key", sa.String(80), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("video_projects.id", ondelete="SET NULL")),
        sa.Column("lease_token", sa.String(36)), sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("notification_acknowledged", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "occurrence_key", name="uq_video_agent_occurrence"),
        sa.UniqueConstraint("project_id"), if_not_exists=True)
    op.create_index("ix_video_agent_executions_tenant_id", "video_agent_executions", ["tenant_id"], if_not_exists=True)
    op.create_index("ix_video_agent_executions_task_id", "video_agent_executions", ["task_id"], if_not_exists=True)


def downgrade():
    op.drop_table("video_agent_executions")
    # Preserve historic video agent tasks and their run references on rollback.
