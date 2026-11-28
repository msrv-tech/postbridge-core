"""Exercise real Alembic installs and legacy upgrades on disposable test databases."""

import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from postbridge.models.domain import (
    ChannelAudienceOrm, ChannelOrm, MediaAssetOrm, PublicationAnalyticsOrm, TenantOrm, VideoMaterialOrm,
)
from postbridge.services.publication_planning import create_content_with_plan_and_targets


ANALYTICS_SCHEMA = {
    "publication_analytics": {
        "ix_publication_analytics_next_attempt": ["next_attempt_at"],
        "ix_publication_analytics_expires": ["history_expires_at"],
    },
    "channel_audience_analytics": {
        "ix_channel_audience_due": ["next_attempt_at"],
        "ix_channel_audience_expires": ["history_expires_at"],
    },
}


def assert_analytics_indexes(engine):
    inspector = inspect(engine)
    for table, expected in ANALYTICS_SCHEMA.items():
        indexes = {index["name"]: index["column_names"] for index in inspector.get_indexes(table)}
        assert indexes == expected


@pytest.fixture
def migration_database(monkeypatch):
    url = make_url(os.environ["DATABASE_URL"])
    if url.get_backend_name() != "postgresql" or not url.database.endswith("_test"):
        raise RuntimeError("Migration regressions require the canonical PostgreSQL test environment")
    name = "postbridge_migration_" + uuid4().hex + "_test"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    target = url.set(database=name)
    engine = create_engine(target)
    monkeypatch.setenv("DATABASE_URL", target.render_as_string(hide_password=False))
    # Keep Alembic's logging configuration out of the surrounding pytest process.
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    try:
        yield config, engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


def test_empty_database_reaches_current_head_and_can_be_checked_again(migration_database):
    config, engine = migration_database
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == ScriptDirectory.from_config(config).get_current_head()
    inspector = inspect(engine)
    assert_analytics_indexes(engine)
    assert {"video_materials", "video_batches", "video_batch_items"}.issubset(inspector.get_table_names())
    assert "batch_id" in {column["name"] for column in inspector.get_columns("video_agent_executions")}
    assert "publication_json" in {column["name"] for column in inspector.get_columns("video_agent_executions")}
    assert "alignment_json" in {column["name"] for column in inspector.get_columns("video_speech_assets")}


def seed_assets(engine, *, keep_named_material=False):
    tenant, first, second = (str(uuid4()) for _ in range(3))
    with Session(engine) as session:
        session.add(TenantOrm(id=tenant, name="Migration regression"))
        session.commit()
        for asset in [first, second]:
            session.add(MediaAssetOrm(id=asset, tenant_id=tenant, object_key="migration-fixture/" + asset,
                                      content_type="image/png", byte_size=10))
        session.commit()
        if keep_named_material:
            session.add(VideoMaterialOrm(asset_id=first, tenant_id=tenant, name="User name", folder="User folder", metadata_json={"kept": True}))
            session.commit()
    return first, second


def test_legacy_video_upgrade_creates_missing_objects_and_keeps_assets(migration_database):
    config, engine = migration_database
    command.upgrade(config, "20261019_video_speech")
    # The public baseline builds the current metadata. Reconstruct the shape
    # an existing database had before these later video revisions.
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE video_batch_items, video_batches, video_materials, video_delivery_states CASCADE")
        connection.exec_driver_sql("ALTER TABLE video_agent_executions DROP COLUMN batch_id")
        connection.exec_driver_sql("ALTER TABLE video_agent_executions DROP COLUMN publication_json")
        connection.exec_driver_sql("ALTER TABLE video_publication_approvals DROP COLUMN auto_agent_run_id")
        connection.exec_driver_sql("ALTER TABLE video_speech_assets DROP COLUMN alignment_json")
        connection.exec_driver_sql("DROP TABLE publication_analytics, channel_audience_analytics")
    ids = seed_assets(engine)
    command.upgrade(config, "head")
    assert_analytics_indexes(engine)
    with Session(engine) as session:
        for asset in ids:
            assert session.get(MediaAssetOrm, asset).byte_size == 10
            material = session.get(VideoMaterialOrm, asset)
            assert material.name == "Imported material"
            assert material.metadata_json == {"kind": "image"}


def test_current_metadata_baseline_backfills_only_missing_materials(migration_database):
    config, engine = migration_database
    command.upgrade(config, "20261019_video_speech")
    first, second = seed_assets(engine, keep_named_material=True)
    channel = str(uuid4())
    with Session(engine) as session:
        tenant = session.get(MediaAssetOrm, first).tenant_id
        session.add(ChannelOrm(id=channel, tenant_id=tenant, platform="telegram", kind="destination",
                               title="Analytics fixture", status="connected"))
        session.flush()
        target = create_content_with_plan_and_targets(
            session, tenant_id=tenant, channel_ids=[channel], body_markdown="Analytics fixture",
        ).publication_target_ids[0]
        session.add_all([
            PublicationAnalyticsOrm(target_id=target, external_post_id="fixture-post", status="ok",
                                    history=[{"views": 17}]),
            ChannelAudienceOrm(channel_id=channel, external_id="fixture-channel", status="ok",
                               history=[{"followers": 29}]),
        ])
        session.commit()
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX ix_channel_audience_due")
    command.upgrade(config, "head")
    assert_analytics_indexes(engine)
    with Session(engine) as session:
        published = session.get(PublicationAnalyticsOrm, target)
        audience = session.get(ChannelAudienceOrm, channel)
        assert (published.external_post_id, published.history) == ("fixture-post", [{"views": 17}])
        assert (audience.external_id, audience.history) == ("fixture-channel", [{"followers": 29}])
        kept = session.get(VideoMaterialOrm, first)
        assert (kept.name, kept.folder, kept.metadata_json) == ("User name", "User folder", {"kept": True})
        assert session.get(VideoMaterialOrm, second).name == "Imported material"
        assert len(session.scalars(select(VideoMaterialOrm)).all()) == 2


@pytest.mark.parametrize(("ddl", "error"), [
    (["ALTER TABLE publication_analytics DROP COLUMN attempted_at"], "columns differ"),
    (["ALTER TABLE channel_audience_analytics ALTER COLUMN external_id DROP NOT NULL"], "columns differ"),
    (["ALTER TABLE channel_audience_analytics ALTER COLUMN history TYPE jsonb"], "columns differ"),
    (["ALTER TABLE publication_analytics DROP CONSTRAINT publication_analytics_pkey"], "primary key differs"),
    (["ALTER TABLE channel_audience_analytics DROP CONSTRAINT channel_audience_analytics_channel_id_fkey",
      "ALTER TABLE channel_audience_analytics ADD FOREIGN KEY (channel_id) REFERENCES channels(id)"],
     "foreign key differs"),
    (["ALTER TABLE channel_audience_analytics DROP CONSTRAINT channel_audience_analytics_channel_id_fkey",
      "ALTER TABLE channel_audience_analytics ADD FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE NOT VALID"],
     "foreign key is not validated"),
    (["DROP INDEX ix_publication_analytics_next_attempt",
      "CREATE INDEX ix_publication_analytics_next_attempt ON publication_analytics (next_attempt_at) WHERE status='ok'"],
     "Incompatible analytics index"),
], ids=["missing-column", "nullable", "type", "primary-key", "foreign-key", "unvalidated-foreign-key", "partial-index"])
def test_incompatible_analytics_schema_blocks_revision(migration_database, ddl, error):
    config, engine = migration_database
    previous = "20261126_material_selections"
    command.upgrade(config, previous)
    with engine.begin() as connection:
        for statement in ddl:
            connection.exec_driver_sql(statement)

    with pytest.raises(RuntimeError, match=error):
        command.upgrade(config, "head")

    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == previous
