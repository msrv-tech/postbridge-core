"""Retry live-sync: apply_async с пробросом live_sync_* kwargs."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from postbridge.db import Base, ENGINE, SESSION_LOCAL, BatchImportRunOrm, init_db  # noqa: E402
from postbridge.domain.errors import ExternalApiError  # noqa: E402
from postbridge.models.domain import ChannelOrm, PublicationTargetOrm, TenantOrm  # noqa: E402
from postbridge.services.publication_planning import create_content_with_plan_and_targets  # noqa: E402
from postbridge.services.publication_target_executor import PUBLICATION_TARGET_PENDING  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    session = SESSION_LOCAL()
    session.query(BatchImportRunOrm).delete()
    session.commit()
    session.close()
    yield


def _seed_pending_target() -> str:
    session = SESSION_LOCAL()
    tenant_id = str(uuid4())
    session.add(TenantOrm(id=tenant_id, name="t"))
    session.flush()
    ch_id = str(uuid4())
    session.add(
        ChannelOrm(
            id=ch_id,
            tenant_id=tenant_id,
            platform="max",
            kind="destination",
            title="Max ch",
            external_id="chat-42",
            status="connected",
        )
    )
    session.commit()
    result = create_content_with_plan_and_targets(
        session,
        tenant_id=tenant_id,
        channel_ids=[ch_id],
        title="Hi",
        body_markdown="Body",
        target_status=PUBLICATION_TARGET_PENDING,
    )
    session.commit()
    tid = result.publication_target_ids[0]
    session.close()
    return tid


class _FailingPublisher:
    def publish_post(self, target_channel, post, credentials=None):
        raise ExternalApiError(
            code="MAX_TEMP",
            message="down",
            source="max",
            retryable=True,
        )


def test_live_sync_retryable_schedules_apply_async_with_live_sync_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from postbridge.workers.tasks import process_publication_target_task

    monkeypatch.setattr(
        "postbridge.services.publication_target_executor.get_publisher",
        lambda _platform: _FailingPublisher(),
    )

    target_id = _seed_pending_target()
    post_json = '{"source_post_id":"p1","text":"x"}'
    ls_kw = {
        "live_sync_source_channel": "-100",
        "live_sync_source_post_id": "p1",
        "live_sync_target_channel": "max_tgt",
        "live_sync_target_platform": "max",
        "live_sync_workspace_id": "ws1",
        "live_sync_post_json": post_json,
        "live_sync_tenant_id": str(uuid4()),
        "live_sync_target_core_channel_id": str(uuid4()),
    }

    captured: list[dict] = []

    def fake_apply_async(*args, **kwargs):
        captured.append(kwargs)
        return MagicMock()

    monkeypatch.setattr(process_publication_target_task, "apply_async", fake_apply_async)

    ret = process_publication_target_task.run(target_id, "c1", **ls_kw)
    assert ret == 0
    assert len(captured) == 1
    ac = captured[0]
    assert ac["args"] == [target_id, "c1"]
    assert ac["countdown"] is not None
    task_kw = ac["kwargs"]
    assert task_kw.get("live_sync_post_json") == post_json
    assert task_kw.get("live_sync_source_channel") == "-100"

    session = SESSION_LOCAL()
    t = session.get(PublicationTargetOrm, target_id)
    assert t is not None
    assert t.status == PUBLICATION_TARGET_PENDING
    session.close()
