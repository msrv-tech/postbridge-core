from datetime import UTC, datetime

from postbridge.domain.models import BatchImportRun, BatchImportRunStatus  # noqa: E402
from postbridge.integrations.status_event_client import StatusEventClient  # noqa: E402


def test_status_event_client_builds_event_payload() -> None:
    client = StatusEventClient()
    run = BatchImportRun(
        id="job-1",
        tenant_id="00000000-0000-4000-8000-000000000001",
        source_channel="tg/source",
        target_channel="max/target",
        status=BatchImportRunStatus.RUNNING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        requested_limit=10,
        processed_posts=3,
        correlation_id="corr-1",
        retry_count=1,
    )
    event = client.build_batch_import_run_status_event(run)
    assert event.contract_version == "1.5"
    assert event.event_type == "batch_import_run.status.changed"
    assert event.batch_import_run["id"] == "job-1"
    assert event.batch_import_run["status"] == "running"
    assert event.batch_import_run["processed_posts"] == 3
    assert event.batch_import_run["retry_count"] == 1
    assert event.batch_import_run["correlation_id"] == "corr-1"
