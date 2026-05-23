from __future__ import annotations

from postbridge.workers import live_sync_tasks, media_group_buffer


class _Redis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.values: set[str] = set()
        self.deleted: list[str] = []

    def rpush(self, key: str, item: str) -> None:
        self.lists.setdefault(key, []).append(item)

    def expire(self, key: str, ttl: int) -> None:
        assert ttl == media_group_buffer.MG_BUFFER_TTL

    def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        assert value == "1"
        assert nx is True
        assert ex == media_group_buffer.MG_SCHEDULED_TTL
        if key in self.values:
            return False
        self.values.add(key)
        return True

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        assert (start, end) == (0, -1)
        return list(self.lists.get(key, []))

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.lists.pop(key, None)
        self.values.discard(key)


def test_media_group_buffer_adds_once_and_pops_items(monkeypatch) -> None:
    redis = _Redis()
    monkeypatch.setattr(media_group_buffer, "_get_redis", lambda: redis)

    assert media_group_buffer.add_to_media_group("-100", "album", 1, "caption", "https://cdn.test/a.png") is True
    assert media_group_buffer.add_to_media_group("-100", "album", 2, "", None) is False

    assert media_group_buffer.pop_media_group("-100", "album") == [
        {"msg_id": 1, "text": "caption", "media_url": "https://cdn.test/a.png"},
        {"msg_id": 2, "text": "", "media_url": None},
    ]
    assert media_group_buffer.pop_media_group("-100", "album") is None


def test_media_group_buffer_handles_redis_failures(monkeypatch) -> None:
    monkeypatch.setattr(media_group_buffer, "_get_redis", lambda: (_ for _ in ()).throw(RuntimeError("down")))

    assert media_group_buffer.add_to_media_group("-100", "album", 1, "caption", None) is False
    assert media_group_buffer.pop_media_group("-100", "album") is None


def test_live_sync_worker_tasks_delegate_to_core_delivery(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        live_sync_tasks,
        "deliver_publish_to_core",
        lambda **kwargs: calls.append(("publish", kwargs)) or {"status": "ok", "source_post_id": "p1"},
    )
    monkeypatch.setattr(
        live_sync_tasks,
        "deliver_edit_to_core",
        lambda **kwargs: calls.append(("edit", kwargs)) or {"status": "ok", "source_post_id": "p1"},
    )

    assert live_sync_tasks.publish_live_sync_post.run(
        "source",
        "target",
        {"source_post_id": "p1"},
        "workspace",
        "rss",
        core_tenant_id="tenant",
        target_core_channel_id="target-core",
        producer="test",
    ) == {"status": "ok", "source_post_id": "p1"}
    assert live_sync_tasks.edit_live_sync_post.run(
        "source",
        "target",
        {"source_post_id": "p1"},
        "rss",
        "workspace",
        core_tenant_id="tenant",
        target_core_channel_id="target-core",
        producer="test",
    ) == {"status": "ok", "source_post_id": "p1"}

    assert calls[0] == (
        "publish",
        {
            "source_channel": "source",
            "target_channel": "target",
            "post": {"source_post_id": "p1"},
            "saas_workspace_id": "workspace",
            "target_platform": "rss",
            "tenant_id": "tenant",
            "target_core_channel_id": "target-core",
            "producer": "test",
        },
    )
    assert calls[1][0] == "edit"
    assert calls[1][1]["workspace_id"] == "workspace"


def test_publish_live_sync_media_group_combines_album(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        live_sync_tasks,
        "pop_media_group",
        lambda source_channel, media_group_id: [
            {"msg_id": 1, "text": "", "media_url": "https://cdn.test/a.png"},
            {"msg_id": 2, "text": "album caption", "media_url": "https://cdn.test/b.png"},
        ],
    )
    monkeypatch.setattr(
        live_sync_tasks,
        "deliver_publish_to_core",
        lambda **kwargs: captured.update(kwargs) or {"status": "ok", "source_post_id": "mg:album"},
    )

    out = live_sync_tasks.publish_live_sync_media_group.run(
        "source",
        "target",
        "workspace",
        "album",
        "rss",
        core_tenant_id="tenant",
        target_core_channel_id="target-core",
        producer="test",
    )

    assert out == {"status": "ok", "source_post_id": "mg:album"}
    assert captured["post"] == {
        "source_post_id": "mg:album",
        "text": "album caption",
        "media_url": None,
        "media_urls": ["https://cdn.test/a.png", "https://cdn.test/b.png"],
    }


def test_publish_live_sync_media_group_skips_empty_album(monkeypatch) -> None:
    monkeypatch.setattr(live_sync_tasks, "pop_media_group", lambda *_args: None)

    assert live_sync_tasks.publish_live_sync_media_group.run(
        "source",
        "target",
        "workspace",
        "album",
        core_tenant_id="tenant",
        target_core_channel_id="target-core",
    ) == {"status": "skipped", "reason": "empty"}
