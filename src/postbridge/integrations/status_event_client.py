from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import requests

from postbridge.config import get_settings
from postbridge.domain.errors import ExternalApiError
from postbridge.domain.models import BatchImportRun

CORRELATION_HEADER = "X-Correlation-Id"
CONTRACT_VERSION_HEADER = "X-Contract-Version"
EVENT_TOKEN_HEADER = "X-Core-Event-Token"
CONTRACT_VERSION = "1.5"


@dataclass(slots=True)
class StatusEvent:
    """Batch import run status event for a configured webhook receiver."""

    event_id: str
    contract_version: str
    event_type: str
    occurred_at: str
    batch_import_run: dict[str, object]


class StatusEventClient:
    """Client for status event webhook delivery."""

    def __init__(self) -> None:
        """Initialize the client from environment settings."""
        self._settings = get_settings()

    def is_enabled(self) -> bool:
        """Return whether status webhook delivery is configured."""
        return bool(self._settings.status_event_webhook_url)

    def build_batch_import_run_status_event(self, run: BatchImportRun) -> StatusEvent:
        """Build a status event from a domain batch import run."""
        error_payload: dict[str, object] | None = None
        if (
            run.error_code is not None
            and run.error_message is not None
            and run.error_source is not None
            and run.error_retryable is not None
        ):
            error_payload = {
                "code": run.error_code,
                "message": run.error_message,
                "details": run.error_details or {},
                "source": run.error_source,
                "retryable": run.error_retryable,
                "correlation_id": run.correlation_id or "unknown",
            }

        return StatusEvent(
            event_id=str(uuid4()),
            contract_version=CONTRACT_VERSION,
            event_type="batch_import_run.status.changed",
            occurred_at=datetime.now(UTC).isoformat(),
            batch_import_run={
                "id": run.id,
                "status": run.status.value,
                "processed_posts": run.processed_posts,
                "retry_count": run.retry_count,
                "correlation_id": run.correlation_id or "unknown",
                "error": error_payload,
            },
        )

    def publish(self, event: StatusEvent, correlation_id: str) -> None:
        """Send a status event to the configured webhook."""
        if not self._settings.status_event_webhook_url:
            return
        headers = {
            CORRELATION_HEADER: correlation_id,
            CONTRACT_VERSION_HEADER: event.contract_version,
        }
        if self._settings.status_event_webhook_token:
            headers[EVENT_TOKEN_HEADER] = self._settings.status_event_webhook_token
        payload = {
            "event_id": event.event_id,
            "contract_version": event.contract_version,
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "batch_import_run": event.batch_import_run,
        }
        try:
            response = requests.post(
                self._settings.status_event_webhook_url,
                json=payload,
                headers=headers,
                timeout=self._settings.status_event_webhook_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_STATUS_EVENT_DELIVERY_ERROR",
                message="failed to deliver status event",
                source="status_webhook",
                retryable=True,
                details={"reason": str(exc)},
            ) from exc

        if response.status_code >= 400:
            raise ExternalApiError(
                code="EXTERNAL_API_STATUS_EVENT_DELIVERY_ERROR",
                message="status event receiver rejected delivery",
                source="status_webhook",
                retryable=True,
                details={"status_code": response.status_code},
            )

    def publish_json_payload(self, body: dict[str, object], correlation_id: str) -> None:
        """Send an arbitrary JSON payload to the same webhook."""
        if not self._settings.status_event_webhook_url:
            return
        headers = {
            CORRELATION_HEADER: correlation_id,
            CONTRACT_VERSION_HEADER: str(body.get("contract_version", CONTRACT_VERSION)),
        }
        if self._settings.status_event_webhook_token:
            headers[EVENT_TOKEN_HEADER] = self._settings.status_event_webhook_token
        try:
            response = requests.post(
                self._settings.status_event_webhook_url,
                json=body,
                headers=headers,
                timeout=self._settings.status_event_webhook_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise ExternalApiError(
                code="EXTERNAL_API_STATUS_EVENT_DELIVERY_ERROR",
                message="failed to deliver event",
                source="status_webhook",
                retryable=True,
                details={"reason": str(exc)},
            ) from exc

        if response.status_code >= 400:
            raise ExternalApiError(
                code="EXTERNAL_API_STATUS_EVENT_DELIVERY_ERROR",
                message="status event receiver rejected delivery",
                source="status_webhook",
                retryable=True,
                details={"status_code": response.status_code},
            )
