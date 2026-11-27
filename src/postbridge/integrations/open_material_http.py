"""Bounded public catalogue transport shared by the material adapters."""
from email.utils import parsedate_to_datetime
from fcntl import LOCK_EX, LOCK_NB, flock
import json
from pathlib import Path
from tempfile import gettempdir
from time import monotonic, sleep, time
from urllib.parse import urlsplit

import httpx

from postbridge.integrations.material_catalog import CatalogError

USER_AGENT = "PostbridgeMaterialScout/1.0 (https://postbridge.io/)"
MAX_IMAGE_BYTES = 8_000_000
MAX_METADATA_BYTES = 1_000_000


def official_url(value, host, prefix):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https" and parsed.netloc == host and parsed.path.startswith(prefix)
                and not any(ch.isspace() or ch in '<>\\' for ch in value))
    except ValueError:
        return False


def retry_seconds(header, fallback, *, now=None):
    if header is None:
        return fallback
    if header.isascii() and header.isdecimal():
        return max(1, int(header)) if len(header) < 12 else 31_536_000
    try:
        retry_at = parsedate_to_datetime(header)
        if retry_at.tzinfo is not None:
            return max(1, retry_at.timestamp() - (time() if now is None else now))
    except (ValueError, OverflowError):
        pass
    return fallback


class PublicCatalogueHTTP:
    def __init__(self, client, catalogue_id, *, interval, deadline, backoff, state_dir=None):
        self.client = client
        self.interval, self.deadline, self.backoff = interval, deadline, backoff
        root = Path(state_dir) if state_dir is not None else Path(gettempdir()) / "postbridge-catalogue-limits"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state_path = root / catalogue_id

    def _remaining(self):
        if (remaining := self.deadline - monotonic()) <= 0:
            raise CatalogError("MATERIAL_CATALOG_UNAVAILABLE", "Catalogue request budget elapsed")
        return remaining

    def get(self, url, *, maximum, params=None, api=False):
        # One in-flight request across worker processes in this Core runtime.
        with self.state_path.open("a+") as state:
            while True:
                self._remaining()
                try:
                    flock(state, LOCK_EX | LOCK_NB)
                    break
                except BlockingIOError:
                    sleep(min(.05, self._remaining()))
            state.seek(0)
            try:
                next_request = float(state.read() or "0")
            except ValueError:
                next_request = 0
            delay = next_request - time()
            if delay > self.interval + .05:
                raise CatalogError("MATERIAL_CATALOG_BACKOFF", "Catalogue requested a pause before retrying")
            if delay > 0:
                if delay >= self._remaining():
                    raise CatalogError("MATERIAL_CATALOG_UNAVAILABLE", "Catalogue request budget elapsed")
                sleep(delay)
            next_request = time() + self.interval
            try:
                with self.client.stream("GET", url, params=params, follow_redirects=False,
                                        timeout=min(7, self._remaining())) as response:
                    if response.status_code in {429, 503}:
                        next_request = time() + retry_seconds(response.headers.get("Retry-After"), self.backoff)
                        raise CatalogError("MATERIAL_CATALOG_BACKOFF", "Catalogue requested a pause before retrying")
                    if response.status_code in {404, 410} and not api:
                        return None, None
                    response.raise_for_status()
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        self._remaining()
                        content.extend(chunk)
                        if len(content) > maximum:
                            return None, None
                    if api:
                        body = json.loads(content)
                        api_error = body.get("error", {}) if isinstance(body, dict) else {}
                        if isinstance(api_error, dict) and api_error.get("code") in {"maxlag", "ratelimited", "readonly"}:
                            next_request = time() + retry_seconds(response.headers.get("Retry-After"), self.backoff)
                            raise CatalogError("MATERIAL_CATALOG_BACKOFF", "Catalogue requested a pause before retrying")
                        return body
                    mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                    return bytes(content), mime
            except httpx.HTTPStatusError as exc:
                if not api and exc.response.status_code in {400, 403, 404, 410, 413}:
                    return None, None
                raise CatalogError("MATERIAL_CATALOG_UNAVAILABLE", "Catalogue HTTP request failed") from exc
            except (httpx.RequestError, ValueError, TypeError) as exc:
                if isinstance(exc, CatalogError):
                    raise
                raise CatalogError("MATERIAL_CATALOG_UNAVAILABLE", "Catalogue response was unavailable") from exc
            finally:
                state.seek(0)
                state.truncate()
                state.write(str(next_request))
                state.flush()

    def json(self, url, *, params=None):
        body = self.get(url, maximum=MAX_METADATA_BYTES, params=params, api=True)
        if not isinstance(body, dict) or "error" in body:
            raise CatalogError("MATERIAL_CATALOG_UNAVAILABLE", "Catalogue returned no usable API response")
        return body

    def image(self, url):
        data, mime = self.get(url, maximum=MAX_IMAGE_BYTES)
        if not data:
            return b"", ""
        signatures = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n", "image/webp": b"RIFF"}
        if mime not in signatures or not data.startswith(signatures[mime]) or mime == "image/webp" and data[8:12] != b"WEBP":
            return b"", ""
        return data, mime
