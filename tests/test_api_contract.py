from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from postbridge.api.main import app  # noqa: E402
from postbridge.db import Base, ENGINE, SESSION_LOCAL, BatchImportRunOrm, init_db  # noqa: E402
from postbridge.domain.errors import ExternalApiError  # noqa: E402
from postbridge.observability.metrics import reset_for_tests  # noqa: E402
from postbridge.storage.batch_import_run_store import BatchImportRunStore  # noqa: E402
from postbridge.workers.tasks import process_batch_import_run_task  # noqa: E402
from tests.migration_helpers import (  # noqa: E402
    seed_max_destination_channel,
    seed_telegram_source_channel,
)

SVC_TENANT = "20000000-0000-4000-8000-000000000001"
CORE_TARGET_CH_ID = "30000000-0000-4000-8000-000000000001"
CORE_SOURCE_CH_ID = "40000000-0000-4000-8000-000000000001"


def _job_body(**overrides: object) -> dict:
    body: dict = {
        "source_channel": "tg/source",
        "target_channel": "max/target",
        "requested_limit": 1,
        "source_core_channel_id": CORE_SOURCE_CH_ID,
        "target_core_channel_id": CORE_TARGET_CH_ID,
    }
    body.update(overrides)
    return body


def _svc_headers(*, correlation_id: str | None = None, idempotency_key: str | None = None) -> dict[str, str]:
    h = {
        "Authorization": "Bearer svc-test-secret",
        "X-Tenant-Id": SVC_TENANT,
    }
    if correlation_id:
        h["X-Correlation-Id"] = correlation_id
    if idempotency_key:
        h["X-Idempotency-Key"] = idempotency_key
    return h


@pytest.fixture(autouse=True)
def reset_db():
    reset_for_tests()
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    session = SESSION_LOCAL()
    session.query(BatchImportRunOrm).delete()
    seed_max_destination_channel(session, SVC_TENANT, channel_id=CORE_TARGET_CH_ID)
    seed_telegram_source_channel(session, SVC_TENANT, channel_id=CORE_SOURCE_CH_ID)
    session.commit()
    session.close()


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(process_batch_import_run_task, "delay", lambda *args, **kwargs: None)
    return TestClient(app)


def test_list_jobs_returns_array(client: TestClient):
    """GET /internal/service/batch-import-runs returns list for tenant."""
    response = client.get("/internal/service/batch-import-runs", headers=_svc_headers())
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    correlation_id = str(uuid4())
    response2 = client.post(
        "/internal/service/batch-import-runs",
        headers=_svc_headers(correlation_id=correlation_id),
        json=_job_body(source_channel="tg/1", target_channel="max/1"),
    )
    assert response2.status_code == 201
    response3 = client.get("/internal/service/batch-import-runs", headers=_svc_headers())
    assert response3.status_code == 200
    jobs = response3.json()
    assert len(jobs) >= 1
    assert jobs[0]["id"]
    assert jobs[0]["status"] in ("pending", "running", "completed", "failed")


def test_create_job_returns_contract_shape(client: TestClient):
    correlation_id = str(uuid4())
    response = client.post(
        "/internal/service/batch-import-runs",
        headers=_svc_headers(correlation_id=correlation_id),
        json=_job_body(requested_limit=3),
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["idempotency_key"] is None
    assert payload["correlation_id"] == correlation_id
    assert payload["error"] is None
    assert payload["metrics"]["retry_count"] == 0
    assert "duration_ms" in payload["metrics"]


def test_create_job_requires_source_core_channel_uuid(client: TestClient):
    """Missing source_core_channel_id -> 422."""
    response = client.post(
        "/internal/service/batch-import-runs",
        headers=_svc_headers(),
        json={
            "source_channel": "tg/source",
            "target_channel": "max/target",
            "requested_limit": 1,
            "target_core_channel_id": CORE_TARGET_CH_ID,
        },
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "VALIDATION_REQUEST_INVALID"


def test_missing_job_returns_unified_error(client: TestClient):
    correlation_id = str(uuid4())
    response = client.get(
        "/internal/service/batch-import-runs/missing-id",
        headers=_svc_headers(correlation_id=correlation_id),
    )
    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == "VALIDATION_MIGRATION_RUN_NOT_FOUND"
    assert payload["source"] == "core"
    assert payload["retryable"] is False
    assert payload["details"]["run_id"] == "missing-id"
    assert payload["correlation_id"] == correlation_id


def test_invalid_payload_returns_validation_error(client: TestClient):
    response = client.post(
        "/internal/service/batch-import-runs",
        headers=_svc_headers(),
        json={
            "source_channel": "",
            "target_channel": "max/target",
            "requested_limit": 3,
            "source_core_channel_id": CORE_SOURCE_CH_ID,
            "target_core_channel_id": CORE_TARGET_CH_ID,
        },
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "VALIDATION_REQUEST_INVALID"
    assert payload["source"] == "core"
    assert isinstance(payload["details"].get("errors"), list)


def test_create_job_requires_target_core_channel_uuid(client: TestClient):
    response = client.post(
        "/internal/service/batch-import-runs",
        headers=_svc_headers(),
        json={
            "source_channel": "tg/source",
            "target_channel": "max/target",
            "requested_limit": 1,
        },
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "VALIDATION_REQUEST_INVALID"


def test_correlation_id_header_is_generated_when_missing(client: TestClient):
    create_response = client.post(
        "/internal/service/batch-import-runs",
        headers=_svc_headers(),
        json=_job_body(),
    )
    assert create_response.status_code == 201
    create_payload = create_response.json()
    assert isinstance(create_payload["correlation_id"], str)
    assert create_payload["correlation_id"]
    assert create_response.headers["X-Correlation-Id"] == create_payload["correlation_id"]

    get_response = client.get(
        f"/internal/service/batch-import-runs/{create_payload['id']}",
        headers=_svc_headers(),
    )
    assert get_response.status_code == 200
    get_payload = get_response.json()
    assert isinstance(get_payload["correlation_id"], str)
    assert get_response.headers["X-Correlation-Id"]


def test_get_failed_job_returns_error_payload(client: TestClient):
    session = SESSION_LOCAL()
    correlation_id = str(uuid4())
    try:
        store = BatchImportRunStore(session)
        job, _ = store.create_run(
            SVC_TENANT,
            "tg/source",
            "max/target",
            requested_limit=1,
            correlation_id=correlation_id,
            source_core_channel_id=CORE_SOURCE_CH_ID,
            target_core_channel_id=CORE_TARGET_CH_ID,
        )
        store.mark_failed(
            job.id,
            ExternalApiError(
                code="EXTERNAL_API_MAX_HTTP_ERROR",
                message="MAX API request failed",
                source="max",
                retryable=True,
                details={"status_code": 429},
            ),
            correlation_id=correlation_id,
        )
    finally:
        session.close()

    response = client.get(
        f"/internal/service/batch-import-runs/{job.id}",
        headers=_svc_headers(correlation_id=correlation_id),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "EXTERNAL_API_MAX_HTTP_ERROR"
    assert payload["error"]["source"] == "max"
    assert payload["error"]["retryable"] is True
    assert payload["error"]["correlation_id"] == correlation_id


def test_create_job_internal_error_returns_unified_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def fail_create(*args, **kwargs):
        raise RuntimeError("db write failure")

    monkeypatch.setattr(BatchImportRunStore, "create_run", fail_create)
    correlation_id = str(uuid4())
    response = client.post(
        "/internal/service/batch-import-runs",
        headers=_svc_headers(correlation_id=correlation_id),
        json=_job_body(requested_limit=2),
    )
    assert response.status_code == 500
    payload = response.json()
    assert payload["code"] == "INTERNAL_UNEXPECTED_ERROR"
    assert payload["source"] == "core"
    assert payload["retryable"] is False
    assert payload["correlation_id"] == correlation_id
    assert response.headers["X-Correlation-Id"] == correlation_id


def test_create_job_with_same_idempotency_key_returns_existing_job(client: TestClient):
    idempotency_key = "create-job-key-1"
    first = client.post(
        "/internal/service/batch-import-runs",
        headers=_svc_headers(idempotency_key=idempotency_key),
        json=_job_body(requested_limit=3),
    )
    assert first.status_code == 201
    first_payload = first.json()
    assert first_payload["idempotency_key"] == idempotency_key

    second = client.post(
        "/internal/service/batch-import-runs",
        headers=_svc_headers(idempotency_key=idempotency_key),
        json=_job_body(
            source_channel="tg/source-ignored",
            target_channel="max/target-ignored",
            requested_limit=5,
        ),
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["id"] == first_payload["id"]
    assert second_payload["idempotency_key"] == idempotency_key
    assert second_payload["source_channel"] == first_payload["source_channel"]


def test_metrics_endpoint_returns_prometheus_format(client: TestClient):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")
    body = response.text
    if body.strip():
        for line in body.splitlines():
            if line and not line.startswith("#"):
                parts = line.split()
                assert len(parts) >= 2
                assert parts[0].startswith("postbridge_sync_")
                assert float(parts[1]) >= 0


def test_metrics_increment_on_job_creation(client: TestClient):
    response = client.get("/metrics")
    initial = response.text

    client.post(
        "/internal/service/batch-import-runs",
        headers=_svc_headers(),
        json=_job_body(),
    )

    response = client.get("/metrics")
    assert "postbridge_batch_import_runs_created_total" in response.text
    if "postbridge_batch_import_runs_created_total" in initial:
        for line in response.text.splitlines():
            if line.startswith("postbridge_batch_import_runs_created_total "):
                val = float(line.split()[1])
                assert val >= 1
                break
    else:
        assert (
            "postbridge_batch_import_runs_created_total 1" in response.text
            or "postbridge_batch_import_runs_created_total 1.0" in response.text
        )


def test_metrics_idempotency_dedup_increments(client: TestClient):
    key = "metrics-dedup-key-1"
    client.post(
        "/internal/service/batch-import-runs",
        headers=_svc_headers(idempotency_key=key),
        json=_job_body(source_channel="tg/s", target_channel="max/t"),
    )
    client.post(
        "/internal/service/batch-import-runs",
        headers=_svc_headers(idempotency_key=key),
        json=_job_body(source_channel="tg/s", target_channel="max/t"),
    )

    response = client.get("/metrics")
    assert "postbridge_batch_import_runs_created_idempotency_dedup_total" in response.text
