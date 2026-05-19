"""public baseline

Revision ID: 20260516_public_baseline
Revises: 20260421_tenant_image_style
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

from postbridge.db import Base

revision: str = "20260516_public_baseline"
down_revision: Union[str, None] = "20260421_tenant_image_style"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        vector_extension_available = bool(
            bind.execute(text("SELECT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector')")).scalar()
        )
        if vector_extension_available:
            op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    Base.metadata.create_all(bind=bind)

    if bind.dialect.name == "postgresql":
        vector_type_available = bool(bind.execute(text("SELECT to_regtype('vector') IS NOT NULL")).scalar())
        if vector_type_available:
            op.execute("ALTER TABLE content_embeddings ADD COLUMN IF NOT EXISTS vector_pg vector(1536)")
            op.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_content_embeddings_vector_pg_ivfflat
                ON content_embeddings
                USING ivfflat (vector_pg vector_cosine_ops)
                """
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_content_embeddings_vector_pg_ivfflat")
    Base.metadata.drop_all(bind=bind)
