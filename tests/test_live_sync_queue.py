"""Тесты единой очереди live-sync."""

from __future__ import annotations

import threading
import uuid
from unittest.mock import MagicMock

import pytest

from postbridge.services import live_sync_queue


class _ImmediateThread:
    """threading.Thread, который выполняет target при start() синхронно."""

    def __init__(
        self,
        group=None,
        target=None,
        name=None,
        args=(),
        kwargs=None,
        *,
        daemon=None,
    ):
        self._target = target
        self._kwargs = dict(kwargs or {})

    def start(self) -> None:
        if self._target:
            self._target(**self._kwargs)


def test_queue_live_sync_publish_eager_invokes_deliver(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_deliver(**kwargs: object) -> dict[str, str]:
        calls.append(kwargs)
        return {"status": "ok", "source_post_id": "1"}

    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "1")
    monkeypatch.setattr(live_sync_queue, "deliver_publish_to_core", fake_deliver)
    monkeypatch.setattr(threading, "Thread", _ImmediateThread)

    tid = str(uuid.uuid4())
    tcid = str(uuid.uuid4())
    live_sync_queue.queue_live_sync_publish(
        source_channel="-1001",
        target_channel="max_tgt",
        post={"source_post_id": "42", "text": "hi"},
        workspace_id="ws1",
        target_platform="max",
        core_tenant_id=tid,
        target_core_channel_id=tcid,
        producer="test",
    )

    assert len(calls) == 1
    assert calls[0]["source_channel"] == "-1001"
    assert calls[0]["target_channel"] == "max_tgt"
    assert calls[0]["tenant_id"] == tid
    assert calls[0]["target_core_channel_id"] == tcid
    assert calls[0]["producer"] == "test"


def test_queue_live_sync_publish_celery_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CELERY_TASK_ALWAYS_EAGER", raising=False)

    mock_task = MagicMock()
    monkeypatch.setattr(
        "postbridge.workers.live_sync_tasks.publish_live_sync_post",
        mock_task,
    )

    tid = str(uuid.uuid4())
    tcid = str(uuid.uuid4())
    live_sync_queue.queue_live_sync_publish(
        source_channel="s",
        target_channel="t",
        post={"source_post_id": "1"},
        workspace_id="w",
        core_tenant_id=tid,
        target_core_channel_id=tcid,
        producer="unit",
    )

    mock_task.delay.assert_called_once()
    kw = mock_task.delay.call_args.kwargs
    assert kw["producer"] == "unit"
    assert kw["core_tenant_id"] == tid
