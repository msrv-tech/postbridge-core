"""Типы бандла платформы для реестра интеграций."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from postbridge.integrations.base import SourceFetcher, TargetPublisher
from postbridge.integrations.platform_ai import PlatformAiAdapter
from postbridge.integrations.platform_capabilities import PlatformCapabilities

CredentialDecoder = Callable[[Any], Any]
RuleAdaptPost = Callable[[dict[str, Any]], str]


@dataclass(frozen=True, slots=True)
class PlatformBundle:
    fetcher_cls: type[SourceFetcher] | None
    publisher_cls: type[TargetPublisher] | None
    decode_publish_credentials: CredentialDecoder | None
    decode_fetch_credentials: CredentialDecoder | None
    capabilities: PlatformCapabilities
    ai_adapter: PlatformAiAdapter
    rule_adapt_post: RuleAdaptPost | None
