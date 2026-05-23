from uuid import uuid4

import pytest

from postbridge.db import Base, ENGINE, SESSION_LOCAL, PublishedPostOrm, init_db  # noqa: E402
from postbridge.domain.errors import ExternalApiError  # noqa: E402
from postbridge.models.domain import ChannelOrm, PublicationTargetOrm, TenantOrm  # noqa: E402
from postbridge.services.publication_planning import create_content_with_plan_and_targets  # noqa: E402
from postbridge.services.publication_target_executor import PUBLICATION_TARGET_PENDING  # noqa: E402
from postbridge.storage.batch_import_run_store import BatchImportRunStore  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    yield


def _seed_pending_target(*, platform: str = "max") -> str:
    session = SESSION_LOCAL()
    try:
        tenant_id = str(uuid4())
        session.add(TenantOrm(id=tenant_id, name="t"))
        session.flush()
        channel_id = str(uuid4())
        session.add(
            ChannelOrm(
                id=channel_id,
                tenant_id=tenant_id,
                platform=platform,
                kind="destination",
                title="Channel",
                external_id="chat-1",
                status="connected",
            )
        )
        session.commit()
        result = create_content_with_plan_and_targets(
            session,
            tenant_id=tenant_id,
            channel_ids=[channel_id],
            title="Hi",
            body_markdown="Body",
            target_status=PUBLICATION_TARGET_PENDING,
        )
        session.commit()
        return result.publication_target_ids[0]
    finally:
        session.close()


def _seed_live_sync_claim(*, source_channel: str, source_post_id: str, target_channel: str) -> None:
    session = SESSION_LOCAL()
    try:
        store = BatchImportRunStore(session)
        assert store.claim_publish(source_channel, source_post_id, target_channel) is True
        session.commit()
    finally:
        session.close()


def test_retry_countdown_seconds_clamps_and_adds_small_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    from postbridge.workers.tasks import _retry_countdown_seconds

    class DummySettings:
        batch_import_run_retry_delay_seconds = 10
        batch_import_run_retry_backoff_multiplier = 2
        batch_import_run_retry_max_delay_seconds = 25

    monkeypatch.setattr("postbridge.workers.tasks.get_settings", lambda: DummySettings())

    t1 = _retry_countdown_seconds("job-1", retry_count=1)
    assert 10 <= t1 <= 12

    # exponential backoff would exceed max_delay; should clamp.
    assert _retry_countdown_seconds("job-1", retry_count=3) == 25


def test_live_sync_success_updates_claim_max_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from postbridge.workers.tasks import process_publication_target_task

    target_id = _seed_pending_target(platform="max")

    src_ch = "-100"
    src_post = "p1"
    tgt_ch = "max_tgt"
    _seed_live_sync_claim(source_channel=src_ch, source_post_id=src_post, target_channel=tgt_ch)

    def fake_run(self, _target_id: str, correlation_id: str | None = None) -> int:
        target = self.session.get(PublicationTargetOrm, _target_id)
        assert target is not None
        target.external_post_id = "ext-1"
        self.session.commit()
        return 1

    ok_calls: list[int] = []

    monkeypatch.setattr("postbridge.workers.tasks.PublicationTargetExecutor.run", fake_run)
    monkeypatch.setattr("postbridge.workers.tasks.inc_live_publish_ok", lambda: ok_calls.append(1))

    ret = process_publication_target_task.run(
        target_id,
        "c1",
        live_sync_source_channel=src_ch,
        live_sync_source_post_id=src_post,
        live_sync_target_channel=tgt_ch,
        live_sync_target_platform="max",
        live_sync_workspace_id="ws1",
        live_sync_post_json='{"source_post_id":"p1","text":"x"}',
        live_sync_tenant_id=str(uuid4()),
        live_sync_target_core_channel_id=str(uuid4()),
    )
    assert ret == 1
    assert ok_calls == [1]

    session = SESSION_LOCAL()
    try:
        row = (
            session.query(PublishedPostOrm)
            .filter_by(source_channel=src_ch, source_post_id=src_post, target_channel=tgt_ch)
            .one_or_none()
        )
        assert row is not None
        assert row.max_message_id == "ext-1"
    finally:
        session.close()


def test_live_sync_retry_exhausted_aborts_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    from postbridge.workers.tasks import process_publication_target_task

    target_id = _seed_pending_target(platform="max")

    src_ch = "-100"
    src_post = "p1"
    tgt_ch = "max_tgt"
    _seed_live_sync_claim(source_channel=src_ch, source_post_id=src_post, target_channel=tgt_ch)

    def failing_run(self, _target_id: str, correlation_id: str | None = None) -> int:
        raise ExternalApiError(
            code="MAX_TEMP",
            message="down",
            source="max",
            retryable=True,
        )

    failed_calls: list[int] = []

    monkeypatch.setattr("postbridge.workers.tasks.PublicationTargetExecutor.run", failing_run)
    monkeypatch.setattr(
        "postbridge.workers.tasks.schedule_publication_target_retry",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr("postbridge.workers.tasks.inc_live_publish_failed", lambda: failed_calls.append(1))

    with pytest.raises(ExternalApiError):
        process_publication_target_task.run(
            target_id,
            "c1",
            live_sync_source_channel=src_ch,
            live_sync_source_post_id=src_post,
            live_sync_target_channel=tgt_ch,
            live_sync_target_platform="max",
            live_sync_workspace_id="ws1",
            live_sync_post_json='{"source_post_id":"p1","text":"x"}',
            live_sync_tenant_id=str(uuid4()),
            live_sync_target_core_channel_id=str(uuid4()),
        )

    assert failed_calls == [1]

    session = SESSION_LOCAL()
    try:
        row = (
            session.query(PublishedPostOrm)
            .filter_by(source_channel=src_ch, source_post_id=src_post, target_channel=tgt_ch)
            .one_or_none()
        )
        assert row is None
    finally:
        session.close()


def test_live_sync_success_rss_skips_max_message_id_update(monkeypatch: pytest.MonkeyPatch) -> None:
    from postbridge.workers.tasks import process_publication_target_task

    target_id = _seed_pending_target(platform="max")

    src_ch = "-100"
    src_post = "p1"
    tgt_ch = "rss_tgt"
    _seed_live_sync_claim(source_channel=src_ch, source_post_id=src_post, target_channel=tgt_ch)

    def fake_run(self, _target_id: str, correlation_id: str | None = None) -> int:
        target = self.session.get(PublicationTargetOrm, _target_id)
        assert target is not None
        target.external_post_id = "ext-1"
        self.session.commit()
        return 1

    ok_calls: list[int] = []

    monkeypatch.setattr("postbridge.workers.tasks.PublicationTargetExecutor.run", fake_run)
    monkeypatch.setattr("postbridge.workers.tasks.inc_live_publish_ok", lambda: ok_calls.append(1))

    ret = process_publication_target_task.run(
        target_id,
        "c1",
        live_sync_source_channel=src_ch,
        live_sync_source_post_id=src_post,
        live_sync_target_channel=tgt_ch,
        live_sync_target_platform="rss",
        live_sync_workspace_id="ws1",
        live_sync_post_json='{\"source_post_id\":\"p1\",\"text\":\"x\"}',
        live_sync_tenant_id=str(uuid4()),
        live_sync_target_core_channel_id=str(uuid4()),
    )
    assert ret == 1
    assert ok_calls == [1]

    session = SESSION_LOCAL()
    try:
        row = (
            session.query(PublishedPostOrm)
            .filter_by(source_channel=src_ch, source_post_id=src_post, target_channel=tgt_ch)
            .one_or_none()
        )
        assert row is not None
        assert row.max_message_id in (None, "")
    finally:
        session.close()


def test_live_sync_raw_exception_aborts_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    from postbridge.workers.tasks import process_publication_target_task

    target_id = _seed_pending_target(platform="max")

    src_ch = "-100"
    src_post = "p1"
    tgt_ch = "max_tgt"
    _seed_live_sync_claim(source_channel=src_ch, source_post_id=src_post, target_channel=tgt_ch)

    def failing_run(self, _target_id: str, correlation_id: str | None = None) -> int:
        raise RuntimeError("boom")

    failed_calls: list[int] = []

    monkeypatch.setattr("postbridge.workers.tasks.PublicationTargetExecutor.run", failing_run)
    monkeypatch.setattr("postbridge.workers.tasks.inc_live_publish_failed", lambda: failed_calls.append(1))

    with pytest.raises(RuntimeError):
        process_publication_target_task.run(
            target_id,
            "c1",
            live_sync_source_channel=src_ch,
            live_sync_source_post_id=src_post,
            live_sync_target_channel=tgt_ch,
            live_sync_target_platform="max",
            live_sync_workspace_id="ws1",
            live_sync_post_json='{\"source_post_id\":\"p1\",\"text\":\"x\"}',
            live_sync_tenant_id=str(uuid4()),
            live_sync_target_core_channel_id=str(uuid4()),
        )

    assert failed_calls == [1]

    session = SESSION_LOCAL()
    try:
        row = (
            session.query(PublishedPostOrm)
            .filter_by(source_channel=src_ch, source_post_id=src_post, target_channel=tgt_ch)
            .one_or_none()
        )
        assert row is None
    finally:
        session.close()


def test_non_live_sync_retry_exhausted_non_retryable_skips_exhausted_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from postbridge.workers.tasks import process_publication_target_task

    target_id = _seed_pending_target(platform="max")

    def failing_run(self, _target_id: str, correlation_id: str | None = None) -> int:
        raise ExternalApiError(
            code="MAX_BAD_REQUEST",
            message="bad request",
            source="max",
            retryable=False,
        )

    exhausted_calls: list[str] = []
    inc_calls: list[str] = []

    monkeypatch.setattr("postbridge.workers.tasks.PublicationTargetExecutor.run", failing_run)
    monkeypatch.setattr(
        "postbridge.workers.tasks.schedule_publication_target_retry",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "postbridge.workers.tasks.try_release_batch_import_published_post_claim",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "postbridge.workers.tasks.log_job_retry_exhausted", lambda *_a, **_k: exhausted_calls.append("log")
    )
    monkeypatch.setattr(
        "postbridge.workers.tasks.inc_jobs_retry_exhausted", lambda: inc_calls.append("inc")
    )

    with pytest.raises(ExternalApiError):
        process_publication_target_task.run(target_id, "c1")

    assert exhausted_calls == []
    assert inc_calls == []


def test_non_live_sync_retry_exhausted_retryable_records_exhausted_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from postbridge.workers.tasks import process_publication_target_task

    target_id = _seed_pending_target(platform="max")

    def failing_run(self, _target_id: str, correlation_id: str | None = None) -> int:
        raise ExternalApiError(
            code="MAX_TEMP",
            message="down",
            source="max",
            retryable=True,
        )

    released_claims: list[str] = []
    exhausted_calls: list[tuple[str, str, str, int]] = []
    inc_calls: list[str] = []

    monkeypatch.setattr("postbridge.workers.tasks.PublicationTargetExecutor.run", failing_run)
    monkeypatch.setattr(
        "postbridge.workers.tasks.schedule_publication_target_retry",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "postbridge.workers.tasks.try_release_batch_import_published_post_claim",
        lambda _session, _target_id: released_claims.append(_target_id),
    )

    def fake_log_retry_exhausted(target_id: str, correlation_id: str, code: str, retry_count: int) -> None:
        exhausted_calls.append((target_id, correlation_id, code, retry_count))

    monkeypatch.setattr("postbridge.workers.tasks.log_job_retry_exhausted", fake_log_retry_exhausted)
    monkeypatch.setattr("postbridge.workers.tasks.inc_jobs_retry_exhausted", lambda: inc_calls.append("inc"))

    with pytest.raises(ExternalApiError):
        process_publication_target_task.run(target_id, "corr-exhausted")

    assert released_claims == [target_id]
    assert exhausted_calls
    assert exhausted_calls[0][0] == target_id
    assert exhausted_calls[0][1] == "corr-exhausted"
    assert exhausted_calls[0][2] == "MAX_TEMP"
    assert inc_calls == ["inc"]


def test_live_sync_retry_schedules_apply_async_with_live_sync_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from postbridge.workers.tasks import process_publication_target_task

    target_id = _seed_pending_target(platform="max")

    src_ch = "-100"
    src_post = "p1"
    tgt_ch = "max_tgt"
    _seed_live_sync_claim(source_channel=src_ch, source_post_id=src_post, target_channel=tgt_ch)

    def failing_run(self, _target_id: str, correlation_id: str | None = None) -> int:
        raise ExternalApiError(
            code="MAX_TEMP",
            message="down",
            source="max",
            retryable=True,
        )

    class DummySettings:
        batch_import_run_max_retries = 10
        batch_import_run_retry_delay_seconds = 1
        batch_import_run_retry_backoff_multiplier = 2.0
        batch_import_run_retry_max_delay_seconds = 5

    scheduled_calls: list[str] = []
    apply_calls: list[dict[str, object]] = []

    def fake_schedule_retry(session, _target_id: str, _exc, *, max_retries: int, correlation_id: str) -> bool:
        target = session.get(PublicationTargetOrm, _target_id)
        assert target is not None
        target.retry_count = (target.retry_count or 0) + 1
        session.commit()
        scheduled_calls.append(correlation_id)
        assert max_retries == 10
        return True

    def fake_apply(target_id: str, retry_correlation_id: str, countdown: int, extra_kwargs: dict[str, str | None]) -> None:
        apply_calls.append(
            {
                "target_id": target_id,
                "retry_correlation_id": retry_correlation_id,
                "countdown": countdown,
                "extra_kwargs": extra_kwargs,
            }
        )

    monkeypatch.setattr("postbridge.workers.tasks.get_settings", lambda: DummySettings())
    monkeypatch.setattr("postbridge.workers.tasks.PublicationTargetExecutor.run", failing_run)
    monkeypatch.setattr("postbridge.workers.tasks.schedule_publication_target_retry", fake_schedule_retry)
    monkeypatch.setattr("postbridge.workers.tasks._schedule_process_target_retry_apply", fake_apply)
    monkeypatch.setattr("postbridge.workers.tasks.log_job_retry_scheduled", lambda *_a, **_k: None)
    monkeypatch.setattr("postbridge.workers.tasks.inc_jobs_retry_scheduled", lambda: None)

    ret = process_publication_target_task.run(
        target_id,
        "corr-retry",
        live_sync_source_channel=src_ch,
        live_sync_source_post_id=src_post,
        live_sync_target_channel=tgt_ch,
        live_sync_target_platform="max",
        live_sync_workspace_id="ws1",
        live_sync_post_json='{"source_post_id":"p1","text":"x"}',
        live_sync_tenant_id=str(uuid4()),
        live_sync_target_core_channel_id=str(uuid4()),
    )
    assert ret == 0
    assert scheduled_calls == ["corr-retry"]
    assert len(apply_calls) == 1
    assert apply_calls[0]["target_id"] == target_id
    assert apply_calls[0]["retry_correlation_id"] == "corr-retry"
    assert isinstance(apply_calls[0]["countdown"], int)
    assert apply_calls[0]["extra_kwargs"]["live_sync_source_channel"] == src_ch
    assert apply_calls[0]["extra_kwargs"]["live_sync_source_post_id"] == src_post
    assert apply_calls[0]["extra_kwargs"]["live_sync_target_channel"] == tgt_ch
