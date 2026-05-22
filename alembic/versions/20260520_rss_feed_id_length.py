"""expand RSS feed id length

Revision ID: 20260520_rss_feed_id_length
Revises: 20260518_installation_secrets
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260520_rss_feed_id_length"
down_revision: Union[str, None] = "20260518_installation_secrets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "rss_feed_items" not in set(inspector.get_table_names()):
        return
    op.alter_column(
        "rss_feed_items",
        "feed_id",
        existing_type=sa.String(length=64),
        type_=sa.String(length=128),
        existing_nullable=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "rss_feed_items" not in set(inspector.get_table_names()):
        return
    op.alter_column(
        "rss_feed_items",
        "feed_id",
        existing_type=sa.String(length=128),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
