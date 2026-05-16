from dataclasses import dataclass


@dataclass(frozen=True)
class LocalizedMessage:
    key: str
    params: dict[str, object] | None = None


@dataclass(frozen=True)
class LocaleResolution:
    locale: str
    source: str
