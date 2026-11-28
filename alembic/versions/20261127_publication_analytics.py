"""Store bounded, per-publication analytics history."""

from alembic import op
import sqlalchemy as sa

revision = "20261127_publication_analytics"
down_revision = "20261126_material_selections"
branch_labels = None
depends_on = None


def _ensure_analytics_table(name, columns, key, parent, indexes):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table(name):
        # The public baseline can already have created this table from current metadata.
        expected_columns = {
            column.name: (column.type.compile(dialect=bind.dialect), column.nullable)
            for column in columns
        }
        reflected_columns = inspector.get_columns(name)
        actual_columns = {
            column["name"]: (column["type"].compile(dialect=bind.dialect), column["nullable"])
            for column in reflected_columns
        }
        if actual_columns != expected_columns or any(column.get("computed") for column in reflected_columns):
            raise RuntimeError(f"Incompatible analytics table {name}: columns differ")
        if inspector.get_pk_constraint(name)["constrained_columns"] != [key]:
            raise RuntimeError(f"Incompatible analytics table {name}: primary key differs")
        foreign_keys = inspector.get_foreign_keys(name)
        expected_fk = {
            "constrained_columns": [key],
            "referred_schema": None,
            "referred_table": parent,
            "referred_columns": ["id"],
            "options": {"ondelete": "CASCADE"},
        }
        if len(foreign_keys) != 1 or any(foreign_keys[0].get(k) != v for k, v in expected_fk.items()):
            raise RuntimeError(f"Incompatible analytics table {name}: foreign key differs")
        if bind.dialect.name == "postgresql" and bind.scalar(sa.text(
            "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint "
            "WHERE conrelid = CAST(:table AS regclass) AND contype = 'f' AND NOT convalidated)"
        ), {"table": name}):
            raise RuntimeError(f"Incompatible analytics table {name}: foreign key is not validated")
        existing_indexes = {index["name"]: index for index in inspector.get_indexes(name)}
        if (inspector.get_check_constraints(name) or inspector.get_unique_constraints(name)
                or any(index["unique"] for index in existing_indexes.values())):
            raise RuntimeError(f"Incompatible analytics table {name}: unexpected constraints")
    else:
        op.create_table(name, *columns)
        existing_indexes = {}

    for index_name, column in indexes.items():
        index = existing_indexes.get(index_name)
        if index is None:
            op.create_index(index_name, name, [column])
        elif (index["column_names"] != [column] or index.get("column_sorting")
              or any(index.get("dialect_options", {}).values())):
            raise RuntimeError(f"Incompatible analytics index {index_name} on {name}")


def upgrade():
    _ensure_analytics_table(
        "publication_analytics",
        [
            sa.Column("target_id", sa.String(36), sa.ForeignKey("publication_targets.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("external_post_id", sa.String(256), nullable=False),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
            sa.Column("attempted_at", sa.DateTime(timezone=True)),
            sa.Column("claim_id", sa.String(36)),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("detail", sa.String(48)),
            sa.Column("history_expires_at", sa.DateTime(timezone=True)),
            sa.Column("history", sa.JSON(), nullable=False),
        ],
        "target_id",
        "publication_targets",
        {
            "ix_publication_analytics_next_attempt": "next_attempt_at",
            "ix_publication_analytics_expires": "history_expires_at",
        },
    )
    _ensure_analytics_table(
        "channel_audience_analytics",
        [
            sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("external_id", sa.String(256), nullable=False),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
            sa.Column("claim_id", sa.String(36)),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("detail", sa.String(48)),
            sa.Column("history_expires_at", sa.DateTime(timezone=True)),
            sa.Column("history", sa.JSON(), nullable=False),
        ],
        "channel_id",
        "channels",
        {
            "ix_channel_audience_due": "next_attempt_at",
            "ix_channel_audience_expires": "history_expires_at",
        },
    )


def downgrade():
    op.drop_index("ix_channel_audience_expires", table_name="channel_audience_analytics")
    op.drop_index("ix_channel_audience_due", table_name="channel_audience_analytics")
    op.drop_table("channel_audience_analytics")
    op.drop_index("ix_publication_analytics_expires", table_name="publication_analytics")
    op.drop_index("ix_publication_analytics_next_attempt", table_name="publication_analytics")
    op.drop_table("publication_analytics")
