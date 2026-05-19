"""installation secrets

Revision ID: 20260518_installation_secrets
Revises: 20260516_public_baseline
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260518_installation_secrets"
down_revision: Union[str, None] = "20260516_public_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "installation_secrets" in set(inspector.get_table_names()):
        return
    op.create_table(
        "installation_secrets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=True),
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "category", name="uq_installation_secrets_tenant_category"),
    )
    op.create_index(
        "ix_installation_secrets_tenant_id",
        "installation_secrets",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "installation_secrets" not in set(inspector.get_table_names()):
        return
    op.drop_index("ix_installation_secrets_tenant_id", table_name="installation_secrets")
    op.drop_table("installation_secrets")
