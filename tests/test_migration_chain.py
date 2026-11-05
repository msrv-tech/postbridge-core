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

from postbridge.models.domain import MediaAssetOrm, TenantOrm, VideoMaterialOrm


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
    ids = seed_assets(engine)
    command.upgrade(config, "head")
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
    command.upgrade(config, "head")
    with Session(engine) as session:
        kept = session.get(VideoMaterialOrm, first)
        assert (kept.name, kept.folder, kept.metadata_json) == ("User name", "User folder", {"kept": True})
        assert session.get(VideoMaterialOrm, second).name == "Imported material"
        assert len(session.scalars(select(VideoMaterialOrm)).all()) == 2
