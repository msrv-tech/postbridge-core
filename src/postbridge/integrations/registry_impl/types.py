"""Внутренний тип строки реестра при сборке."""

from __future__ import annotations

from dataclasses import dataclass

from postbridge.integrations.base import SourceFetcher, TargetPublisher
from postbridge.integrations.registry_bundle import PlatformBundle


@dataclass(frozen=True, slots=True)
class PlatformRegistration:
    key: str
    fetcher_cls: type[SourceFetcher] | None
    publisher_cls: type[TargetPublisher] | None
    bundle: PlatformBundle
    """Если не None — ключ попадает в RULE_POST_TEXT_LIMITS."""
    rule_post_text_limit: int | None
