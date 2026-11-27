"""Persist verified selections and bind each consumer occurrence to one version."""
from alembic import op
import sqlalchemy as sa

revision = "20261126_material_selections"
down_revision = "20261023_video_speech_align"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "material_selections" not in tables:
        op.create_table(
            "material_selections",
            sa.Column("id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_task_id", sa.String(36), sa.ForeignKey("agent_tasks.id", ondelete="CASCADE"), nullable=False),
            sa.Column("edition_key", sa.String(64), nullable=False),
            sa.Column("snapshot", sa.JSON(), nullable=False),
            sa.UniqueConstraint("source_task_id", "edition_key", name="uq_material_source_edition"),
        )
        op.create_index("ix_material_selections_source", "material_selections", ["tenant_id", "source_task_id"])
    if "material_selection_uses" not in tables:
        op.create_table(
            "material_selection_uses",
            sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("task_id", sa.String(36), sa.ForeignKey("agent_tasks.id", ondelete="CASCADE"), nullable=False),
            sa.Column("selection_id", sa.String(36), sa.ForeignKey("material_selections.id", ondelete="CASCADE"), nullable=False),
            sa.UniqueConstraint("task_id", "selection_id", name="uq_material_consumer_edition"),
        )


def downgrade():
    op.drop_table("material_selection_uses")
    op.drop_table("material_selections")
