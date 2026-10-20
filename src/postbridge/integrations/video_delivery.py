"""Shared provider delivery results and conservative HTTP error classification."""
from dataclasses import dataclass
import logging
import httpx
from postbridge.domain.errors import ExternalApiError


class DeliveryPending(Exception):
    """Provider accepted the job; poll the same job without creating another."""


@dataclass
class VideoDeliveryReceipt:
    external_id: str
    url: str | None = None


def unknown(provider, message="The delivery outcome is unknown; reconcile before resending"):
    return ExternalApiError(code=f"EXTERNAL_API_{provider.upper()}_DELIVERY_UNKNOWN", message=message,
        source=provider, retryable=True, details={})


def check_response(response, provider):
    if 300 <= response.status_code < 400 or response.status_code >= 500 or response.status_code in {408, 409, 425}:
        raise unknown(provider)
    if response.status_code >= 400:
        raise ExternalApiError(code=f"EXTERNAL_API_{provider.upper()}_REJECTED", message=f"{provider} rejected the request (HTTP {response.status_code})",
            source=provider, retryable=False, details={"status_code": response.status_code})




# HTTPX logs request URLs at INFO. Resumable upload IDs are bearer-like
# checkpoints and must not escape the encrypted receipt into worker logs.
class ProviderUrlFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple):
            def safe(value):
                if isinstance(value, httpx.URL) and (
                        'access_token' in value.params or 'upload_token' in value.params
                        or value.host.endswith('.tiktokapis.com') and value.host.startswith('open-upload')
                        or value.host in {'www.googleapis.com', 'youtube.googleapis.com'} and value.path.startswith('/upload/youtube/')):
                    return value.copy_with(query=b'redacted')
                return value
            record.args = tuple(safe(value) for value in record.args)
        return True


logging.getLogger('httpx').addFilter(ProviderUrlFilter())
