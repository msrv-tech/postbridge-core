"""Outbox событий publication.target.status.changed (v1.4) и доставка в webhook."""

from uuid import uuid4

import pytest

from postbridge.db import Base, ENGINE, SESSION_LOCAL, BatchImportRunOrm, init_db  # noqa: E402
from postbridge.models.domain import (  # noqa: E402
    ChannelOrm,
    PublicationTargetOrm,
    TenantOrm,
)
from postbridge.services.publication_planning import create_content_with_plan_and_targets  # noqa: E402
from postbridge.services.publication_target_executor import (  # noqa: E402
    PUBLICATION_TARGET_PENDING,
    claim_publication_target_pending,
)
from postbridge.storage.publication_status_event_outbox import (  # noqa: E402
    enqueue_publication_target_status_changed,
)
from postbridge.workers.tasks import dispatch_status_event_outbox_task  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    session = SESSION_LOCAL()
    session.query(BatchImportRunOrm).delete()
    session.commit()
    session.close()
    yield


def _seed_pending_target() -> tuple[str, str]:
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
            title="Max",
            external_id="chat-99",
            status="connected",
        )
    )
    session.commit()
    session.close()
    session = SESSION_LOCAL()
    result = create_content_with_plan_and_targets(
        session,
        tenant_id=tenant_id,
        channel_ids=[ch_id],
        title="T",
        body_markdown="B",
        target_status=PUBLICATION_TARGET_PENDING,
    )
    session.commit()
    tid = result.publication_target_ids[0]
    session.close()
    return tenant_id, tid


def test_publication_status_event_dispatch_posts_v14_payload(
    monkeypatch: pytest.MonkeyPatch,
):
    class DummyResponse:
        status_code = 200

    captured: dict[str, object] = {}

    def fake_post(url: str, **kwargs: object) -> DummyResponse:
        captured["url"] = url
        captured.update(kwargs)
        return DummyResponse()

    _, tid = _seed_pending_target()
    assert claim_publication_target_pending(
        SESSION_LOCAL(), tid, correlation_id="corr-pub-outbox"
    )

    monkeypatch.setenv("STATUS_EVENT_WEBHOOK_URL", "http://saas.test/internal/core/events/status")
    monkeypatch.setattr(
        "postbridge.integrations.status_event_client.requests.post",
        fake_post,
    )

    processed = dispatch_status_event_outbox_task.run()
    assert processed >= 1
    body = captured.get("json")
    assert isinstance(body, dict)
    assert body.get("event_type") == "publication.target.status.changed"
    assert body.get("contract_version") == "1.4"
    pt = body.get("publication_target")
    assert isinstance(pt, dict)
    assert pt.get("id") == tid
    assert pt.get("status") == "publishing"


def test_enqueue_publication_status_failed_payload():
    import json

    from sqlalchemy import select

    from postbridge.db import PublicationStatusEventOutboxOrm

    _, tid = _seed_pending_target()
    session = SESSION_LOCAL()
    try:
        claim_publication_target_pending(session, tid, correlation_id="c2")
        target = session.get(PublicationTargetOrm, tid)
        assert target is not None
        target.status = "failed"
        target.error_code = "E_TEST"
        target.error_message = "boom"
        enqueue_publication_target_status_changed(session, target, correlation_id="c2")
        session.commit()
    finally:
        session.close()

    session = SESSION_LOCAL()
    try:
        rows = list(session.scalars(select(PublicationStatusEventOutboxOrm)).all())
        assert rows
        last = rows[-1]
        payload = json.loads(last.payload_json)
        assert payload["publication_target"]["status"] == "failed"
        assert payload["publication_target"]["error"]["code"] == "E_TEST"
    finally:
        session.close()
