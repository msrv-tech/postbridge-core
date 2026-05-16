"""Tests for GET /media/{path:path} endpoint (on-premise media serving)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from postbridge.api.main import app  # noqa: E402
from postbridge.db import Base, ENGINE, init_db  # noqa: E402
from postbridge.observability.metrics import reset_for_tests  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db():
    reset_for_tests()
    Base.metadata.drop_all(bind=ENGINE)
    init_db()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_serve_media_returns_file(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """GET /media/test.txt returns file content when file exists."""
    (tmp_path / "test.txt").write_text("hello media")
    monkeypatch.setattr("postbridge.api.main._media_storage_dir", lambda: tmp_path)

    response = client.get("/media/test.txt")
    assert response.status_code == 200
    assert response.content == b"hello media"


def test_serve_media_not_found(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """GET /media/nonexistent returns 404."""
    monkeypatch.setattr("postbridge.api.main._media_storage_dir", lambda: tmp_path)

    response = client.get("/media/nonexistent")
    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == "HTTP_ERROR"
    assert payload["message_key"] == "error.http.not_found"
    assert payload["message"] == "Not found."


def test_serve_media_path_traversal_forbidden(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """GET /media/..%2F..%2Fetc%2Fpasswd returns 403 (path traversal blocked)."""
    monkeypatch.setattr("postbridge.api.main._media_storage_dir", lambda: tmp_path)
    # Use encoded .. to avoid URL normalization; path becomes "../etc/passwd"
    response = client.get("/media/..%2F..%2Fetc%2Fpasswd")
    assert response.status_code == 403
    payload = response.json()
    assert payload["code"] == "HTTP_ERROR"
    assert payload["message_key"] == "error.http.invalid_path"
    assert payload["message"] == "Invalid path."
