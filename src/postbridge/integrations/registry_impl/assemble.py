"""Сборка FETCHERS, PUBLISHERS, PLATFORM_BUNDLES, RULE_POST_TEXT_LIMITS из регистраций платформ."""

from __future__ import annotations

from postbridge.integrations.base import SourceFetcher, TargetPublisher
from postbridge.integrations.platform_ai import DefaultPlatformAiAdapter
from postbridge.integrations.registry_bundle import PlatformBundle
from postbridge.integrations.registry_impl import max as max_plat
from postbridge.integrations.registry_impl import linkedin as linkedin_plat
from postbridge.integrations.registry_impl import postbridge_source
from postbridge.integrations.registry_impl import rss as rss_plat
from postbridge.integrations.registry_impl import telegram as telegram_plat
from postbridge.integrations.registry_impl import vk as vk_plat
from postbridge.integrations.registry_impl import zen as zen_plat
from postbridge.integrations.registry_impl.types import PlatformRegistration

_DEFAULT_AI = DefaultPlatformAiAdapter()


def _all_registrations() -> list[PlatformRegistration]:
    ai = _DEFAULT_AI
    return [
        telegram_plat.make_registration(ai),
        max_plat.make_registration(ai),
        vk_plat.make_registration(ai),
        linkedin_plat.make_registration(ai),
        rss_plat.make_registration(ai),
        zen_plat.make_registration(ai),
        postbridge_source.make_registration(ai),
    ]


def _build() -> tuple[
    dict[str, type[SourceFetcher]],
    dict[str, type[TargetPublisher]],
    dict[str, PlatformBundle],
    dict[str, int],
]:
    fetchers: dict[str, type[SourceFetcher]] = {}
    publishers: dict[str, type[TargetPublisher]] = {}
    bundles: dict[str, PlatformBundle] = {}
    limits: dict[str, int] = {}
    for r in _all_registrations():
        if r.fetcher_cls is not None:
            fetchers[r.key] = r.fetcher_cls
        if r.publisher_cls is not None:
            publishers[r.key] = r.publisher_cls
        bundles[r.key] = r.bundle
        if r.rule_post_text_limit is not None:
            limits[r.key] = r.rule_post_text_limit
    return fetchers, publishers, bundles, limits


FETCHERS, PUBLISHERS, PLATFORM_BUNDLES, RULE_POST_TEXT_LIMITS = _build()
DEFAULT_AI = _DEFAULT_AI
