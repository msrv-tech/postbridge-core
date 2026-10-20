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


def test_serve_video_as_download(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "video.mp4").write_bytes(b"video bytes")
    monkeypatch.setattr("postbridge.api.main._media_storage_dir", lambda: tmp_path)
    response = client.get("/media/video.mp4?download=1")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="postbridge-video.mp4"'


def test_media_path_cannot_escape_into_a_sibling_with_same_prefix(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "media"
    sibling = tmp_path / "media-private"
    root.mkdir()
    sibling.mkdir()
    (sibling / "secret.txt").write_text("private")
    monkeypatch.setattr("postbridge.api.main._media_storage_dir", lambda: root)
    response = client.get("/media/..%2Fmedia-private%2Fsecret.txt")
    assert response.status_code == 403


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
