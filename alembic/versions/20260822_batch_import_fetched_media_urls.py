"""add media_urls_json to batch_import_fetched_posts

Revision ID: 20260822_batch_import_fetched_media_urls
Revises: 20260520_rss_feed_id_length
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260822_batch_import_fetched_media_urls"
down_revision: Union[str, None] = "20260520_rss_feed_id_length"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "batch_import_fetched_posts" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("batch_import_fetched_posts")}
    if "media_urls_json" in columns:
        return
    op.add_column(
        "batch_import_fetched_posts",
        sa.Column("media_urls_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "batch_import_fetched_posts" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("batch_import_fetched_posts")}
    if "media_urls_json" not in columns:
        return
    op.drop_column("batch_import_fetched_posts", "media_urls_json")
