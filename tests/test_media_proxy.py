from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from postbridge.api import main


class _Session:
    def __init__(self, asset: object | None) -> None:
        self.asset = asset
        self.closed = False

    def get(self, model: object, asset_id: str) -> object | None:
        _ = model, asset_id
        return self.asset

    def close(self) -> None:
        self.closed = True


def test_s3_media_is_proxied_by_asset_id(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _Session(
        SimpleNamespace(
            object_key="tenants/tenant-1/media/asset-1.png",
            content_type="image/png",
        )
    )
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(media_storage_type="s3"),
    )
    monkeypatch.setattr(main, "SESSION_LOCAL", lambda: session)
    monkeypatch.setattr(main, "read_media_object", lambda object_key: b"png-data")

    response = main.serve_media("asset-1")

    assert response.body == b"png-data"
    assert response.media_type == "image/png"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert session.closed is True


@pytest.mark.parametrize("path", ["", "tenants/asset-1"])
def test_s3_media_rejects_non_asset_paths(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(media_storage_type="s3"),
    )

    with pytest.raises(HTTPException) as exc_info:
        main.serve_media(path)

    assert exc_info.value.status_code == 404
