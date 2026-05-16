"""Shared botkit models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PendingChannel:
    title: str
    chat_id: int
    workspace_id: str | None


@dataclass(frozen=True, slots=True)
class LiveSyncTarget:
    target_channel_id: str
    workspace_id: str
    target_platform: str
    core_tenant_id: str
    target_core_channel_id: str
