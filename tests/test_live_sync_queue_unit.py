from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from postbridge.services import live_sync_queue


class _Session:
    closed = False

    def close(self) -> None:
        self.closed = True


def test_deliver_publish_to_core_ingests_and_queues_publication_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    delayed: list[tuple[str, str, dict]] = []

    monkeypatch.setattr(live_sync_queue, "SESSION_LOCAL", lambda: session)
    monkeypatch.setattr(
        "postbridge.services.live_sync_publish_service.ingest_live_sync_publication",
        lambda *args, **kwargs: SimpleNamespace(skipped=False, target_id="target-1", source_post_id="post-1"),
    )
    monkeypatch.setattr(
        "postbridge.services.live_sync_publish_service.live_sync_executor_task_kwargs",
        lambda **kwargs: {"kw": kwargs},
    )
    monkeypatch.setattr(
        "postbridge.workers.tasks.process_publication_target_task",
        SimpleNamespace(delay=lambda target_id, corr, **kwargs: delayed.append((target_id, corr, kwargs))),
    )

    out = live_sync_queue.deliver_publish_to_core(
        source_channel="source",
        target_channel="target",
        post={"source_post_id": "post-1", "text": "Hello"},
        saas_workspace_id="workspace",
        target_platform="rss",
        tenant_id="tenant",
        target_core_channel_id="target-core",
        producer="test",
    )

    assert out == {"status": "ok", "source_post_id": "post-1"}
    assert delayed == [("target-1", "live-sync-test", {"kw": {"source_channel": "source", "source_post_id": "post-1", "target_channel": "target", "target_platform": "rss", "post": {"source_post_id": "post-1", "text": "Hello"}, "tenant_id": "tenant", "target_core_channel_id": "target-core", "workspace_id": "workspace"}})]
    assert session.closed is True


def test_deliver_publish_to_core_returns_ok_without_queue_when_ingest_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(live_sync_queue, "SESSION_LOCAL", lambda: _Session())
    monkeypatch.setattr(
        "postbridge.services.live_sync_publish_service.ingest_live_sync_publication",
        lambda *args, **kwargs: SimpleNamespace(skipped=True, source_post_id="post-1"),
    )

    assert live_sync_queue.deliver_publish_to_core(
        source_channel="source",
        target_channel="target",
        post={"source_post_id": "post-1"},
        saas_workspace_id="workspace",
        tenant_id="tenant",
        target_core_channel_id="target-core",
    ) == {"status": "ok", "source_post_id": "post-1"}


def test_deliver_publish_to_core_returns_failed_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live_sync_queue, "SESSION_LOCAL", lambda: _Session())
    monkeypatch.setattr(
        "postbridge.services.live_sync_publish_service.ingest_live_sync_publication",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    out = live_sync_queue.deliver_publish_to_core(
        source_channel="source",
        target_channel="target",
        post={"source_post_id": "post-1"},
        saas_workspace_id="workspace",
        tenant_id="tenant",
        target_core_channel_id="target-core",
        persist_failure=False,
    )

    assert out == {"status": "failed", "source_post_id": "post-1"}


def test_deliver_publish_to_core_releases_claim_when_enqueue_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    released: list[tuple[str, str, str]] = []

    monkeypatch.setattr(live_sync_queue, "SESSION_LOCAL", lambda: _Session())
    monkeypatch.setattr(
        "postbridge.services.live_sync_publish_service.ingest_live_sync_publication",
        lambda *args, **kwargs: SimpleNamespace(
            skipped=False, target_id="target-1", source_post_id="post-1"
        ),
    )
    monkeypatch.setattr(
        "postbridge.services.live_sync_publish_service.live_sync_executor_task_kwargs",
        lambda **kwargs: {"kw": kwargs},
    )
    monkeypatch.setattr(
        "postbridge.workers.tasks.process_publication_target_task",
        SimpleNamespace(delay=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("queue down"))),
    )
    monkeypatch.setattr(
        "postbridge.services.live_sync_publish_service.abort_live_sync_after_enqueue_failure",
        lambda _session, **kwargs: released.append(
            (kwargs["source_channel"], kwargs["source_post_id"], kwargs["target_channel"])
        ),
    )

    out = live_sync_queue.deliver_publish_to_core(
        source_channel="source",
        target_channel="target",
        post={"source_post_id": "post-1"},
        saas_workspace_id="workspace",
        tenant_id="tenant",
        target_core_channel_id="target-core",
        persist_failure=False,
    )

    assert out == {"status": "failed", "source_post_id": "post-1"}
    assert released == [("source", "post-1", "target")]


def test_deliver_edit_to_core_posts_payload_and_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(live_sync_queue, "_core_base_url", lambda: "http://core.test")
    monkeypatch.setattr(live_sync_queue, "_sync_publish_headers", lambda: {"X-Sync-Publish-Token": "sync"})

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        captured["url"] = url
        captured.update(kwargs)
        return httpx.Response(200, json={"status": "ok"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(live_sync_queue.httpx, "post", fake_post)

    out = live_sync_queue.deliver_edit_to_core(
        source_channel="source",
        target_channel="target",
        post={"source_post_id": "post-1"},
        target_platform="rss",
        workspace_id="workspace",
        core_tenant_id="tenant",
        target_core_channel_id="target-core",
        producer="test",
    )

    assert out == {"status": "ok", "source_post_id": "post-1"}
    assert captured["url"] == "http://core.test/internal/sync/edit-single"
    assert captured["headers"] == {"X-Sync-Publish-Token": "sync", "Content-Type": "application/json"}
    assert captured["json"] == {
        "source_channel": "source",
        "target_channel": "target",
        "post": {"source_post_id": "post-1"},
        "target_platform": "rss",
        "tenant_id": "tenant",
        "target_core_channel_id": "target-core",
    }


def test_deliver_edit_to_core_falls_back_to_publish_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("POST", "http://core.test/internal/sync/edit-single")
    response = httpx.Response(404, request=request)
    fallback_calls: list[dict[str, object]] = []
    monkeypatch.setattr(live_sync_queue, "_core_base_url", lambda: "http://core.test")
    monkeypatch.setattr(live_sync_queue.httpx, "post", lambda *args, **kwargs: response)
    monkeypatch.setattr(
        live_sync_queue,
        "deliver_publish_to_core",
        lambda **kwargs: fallback_calls.append(kwargs) or {"status": "ok", "source_post_id": "post-1"},
    )

    out = live_sync_queue.deliver_edit_to_core(
        source_channel="source",
        target_channel="target",
        post={"source_post_id": "post-1"},
        workspace_id="workspace",
        core_tenant_id="tenant",
        target_core_channel_id="target-core",
    )

    assert out == {"status": "ok", "source_post_id": "post-1"}
    assert fallback_calls[0]["saas_workspace_id"] == "workspace"


def test_deliver_edit_to_core_requires_core_identifiers() -> None:
    with pytest.raises(ValueError):
        live_sync_queue.deliver_edit_to_core("source", "target", {}, core_tenant_id="")
