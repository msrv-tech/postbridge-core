"""Minimal i18n service for bot, API, and future web surfaces."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources
from typing import Any

from .models import LocaleResolution

_PARAM_RE = re.compile(r"{{\s*([a-zA-Z0-9_.-]+)\s*}}")


class I18nService:
    def __init__(self, catalogs: dict[str, dict[str, str]], *, default_locale: str = "en") -> None:
        if default_locale not in catalogs:
            raise ValueError(f"default locale {default_locale!r} is not present in catalogs")
        self._catalogs = catalogs
        self._default_locale = default_locale

    @property
    def default_locale(self) -> str:
        return self._default_locale

    def available_locales(self) -> tuple[str, ...]:
        return tuple(sorted(self._catalogs))

    def has_key(self, key: str, *, locale: str | None = None) -> bool:
        resolved = self._normalize_locale(locale) or self._default_locale
        return self._lookup(resolved, key) is not None

    def translate(
        self,
        key: str,
        *,
        locale: str | None = None,
        params: dict[str, Any] | None = None,
        default: str | None = None,
    ) -> str:
        resolved = self._normalize_locale(locale) or self._default_locale
        template = self._lookup(resolved, key)
        if template is None:
            template = default if default is not None else key
        return self._render(template, params or {})

    def resolve_locale(
        self,
        *,
        explicit: str | None = None,
        user_locale: str | None = None,
        platform_locale: str | None = None,
        accept_language: str | None = None,
        fallback: str | None = None,
    ) -> LocaleResolution:
        candidates = [
            ("explicit", explicit),
            ("user", user_locale),
            ("platform", platform_locale),
        ]
        for locale in self._accept_language_candidates(accept_language):
            candidates.append(("accept_language", locale))
        for source, candidate in candidates:
            normalized = self._normalize_locale(candidate)
            if normalized:
                return LocaleResolution(locale=normalized, source=source)
        resolved_fallback = self._normalize_locale(fallback) or self._default_locale
        return LocaleResolution(locale=resolved_fallback, source="fallback")

    def _lookup(self, locale: str, key: str) -> str | None:
        catalog = self._catalogs.get(locale)
        if catalog and key in catalog:
            return catalog[key]
        if locale != self._default_locale:
            return self._catalogs[self._default_locale].get(key)
        return None

    def _normalize_locale(self, locale: str | None) -> str | None:
        if not locale:
            return None
        cleaned = locale.strip().replace("_", "-").lower()
        if not cleaned:
            return None
        if cleaned in self._catalogs:
            return cleaned
        primary = cleaned.split("-", 1)[0]
        if primary in self._catalogs:
            return primary
        return None

    def _accept_language_candidates(self, header: str | None) -> list[str]:
        if not header:
            return []
        parts: list[tuple[float, str]] = []
        for raw_part in header.split(","):
            part = raw_part.strip()
            if not part:
                continue
            locale = part
            quality = 1.0
            if ";" in part:
                locale, *params = [p.strip() for p in part.split(";")]
                for param in params:
                    if param.startswith("q="):
                        try:
                            quality = float(param[2:])
                        except ValueError:
                            quality = 0.0
            if locale:
                parts.append((quality, locale))
        parts.sort(key=lambda item: item[0], reverse=True)
        return [locale for _, locale in parts]

    def _render(self, template: str, params: dict[str, Any]) -> str:
        def _replace(match: re.Match[str]) -> str:
            key = match.group(1)
            value = params.get(key)
            return match.group(0) if value is None else str(value)

        return _PARAM_RE.sub(_replace, template)


def _load_catalogs() -> dict[str, dict[str, str]]:
    base = resources.files("postbridge.i18n").joinpath("locales")
    catalogs: dict[str, dict[str, str]] = {}
    for entry in base.iterdir():
        if not entry.name.endswith(".json"):
            continue
        locale = entry.name[:-5]
        catalogs[locale] = json.loads(entry.read_text(encoding="utf-8"))
    if not catalogs:
        raise RuntimeError("No i18n catalogs found")
    return catalogs


@lru_cache
def get_i18n() -> I18nService:
    from postbridge.config import get_settings

    catalogs = _load_catalogs()
    configured = (get_settings().postbridge_default_locale or "en").strip().lower() or "en"
    default_locale = configured if configured in catalogs else "en"
    return I18nService(catalogs, default_locale=default_locale)
