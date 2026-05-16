"""Разрешение on-prem live-sync по bridges (без LIVE_SYNC_* env)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from postbridge.db import Base, ENGINE, SESSION_LOCAL, init_db
from postbridge.models.domain import BridgeOrm, ChannelOrm, TenantOrm
from postbridge.services.onprem_live_sync_resolve import resolve_onprem_live_sync_from_bridges


@pytest.fixture(autouse=True)
def reset_db() -> None:
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    yield


def test_resolve_onprem_live_sync_from_bridges_found() -> None:
    session = SESSION_LOCAL()
    try:
        tenant_id = str(uuid4())
        session.add(TenantOrm(id=tenant_id, name="t"))
        session.flush()

        src_id, tgt_id = str(uuid4()), str(uuid4())
        tg_chat = -100111222333
        session.add(
            ChannelOrm(
                id=src_id,
                tenant_id=tenant_id,
                platform="telegram",
                kind="source",
                title="TG",
                external_id=str(tg_chat),
                status="connected",
            )
        )
        session.add(
            ChannelOrm(
                id=tgt_id,
                tenant_id=tenant_id,
                platform="max",
                kind="destination",
                title="MAX",
                external_id="max_dest_1",
                status="connected",
            )
        )
        session.flush()
        session.add(
            BridgeOrm(
                id="br-test-1",
                tenant_id=tenant_id,
                saas_user_id="user-1",
                source_channel_id=src_id,
                target_channel_id=tgt_id,
                status="active",
                mode="live_sync",
                settings_json={"saas_workspace_id": "ws-abc"},
            )
        )
        session.commit()

        ctx = resolve_onprem_live_sync_from_bridges(session, tg_chat)
        assert ctx is not None
        assert ctx.tenant_id == tenant_id
        assert ctx.target_channel_external_id == "max_dest_1"
        assert ctx.target_core_channel_id == tgt_id
        assert ctx.target_platform == "max"
        assert ctx.workspace_id == "ws-abc"
    finally:
        session.close()


def test_resolve_onprem_live_sync_from_bridges_missing() -> None:
    session = SESSION_LOCAL()
    try:
        assert resolve_onprem_live_sync_from_bridges(session, -999888777666) is None
    finally:
        session.close()
