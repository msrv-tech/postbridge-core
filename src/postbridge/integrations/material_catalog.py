"""Normalized recording and rights information returned by a material catalogue."""
from dataclasses import dataclass
from html import unescape
import re
from typing import Protocol
from unicodedata import normalize

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class MaterialRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    reference: str = Field(min_length=1, max_length=512)
    record_url: str
    image_url: str
    title: str = Field(min_length=1, max_length=240)
    year: int = Field(ge=1, le=9999)
    catalog_date: str = Field(min_length=1, max_length=120)
    place: str = Field(min_length=1, max_length=100)
    creator: str = Field(min_length=1, max_length=150)
    credit: str = Field(min_length=1, max_length=180)
    license: str
    origin_url: str | None = None
    location_evidence: str | None = Field(default=None, max_length=300)
    rights_evidence: str | None = Field(default=None, max_length=240)

    @field_validator("record_url", "image_url", "license", "origin_url")
    @classmethod
    def evidence_url(cls, value):
        if value is None:
            return value
        url = HttpUrl(value)
        if (url.scheme != "https" or url.username or url.password or len(value) > 360
                or any(ch.isspace() or ch in '<>\\' for ch in value)):
            raise ValueError("Material evidence must use a public HTTPS URL")
        return value


class CatalogError(ValueError):
    def __init__(self, code: str, message: str, *, attempts=None):
        super().__init__(message)
        self.code = code
        self.attempts = attempts


@dataclass(frozen=True)
class CatalogueScope:
    places: tuple[str, ...] = ()
    year_from: int = 1
    year_to: int = 9999


def plain(value, limit):
    if not isinstance(value, str):
        return ""
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", value)).split())[:limit]


def normalize_text(value):
    text = normalize("NFKD", value.casefold())
    return " ".join(re.findall(r"\w+", "".join(ch for ch in text if not 0x300 <= ord(ch) <= 0x36f)))


def mentions(text, place):
    normalized = normalize_text(place)
    return bool(normalized and f" {normalized} " in f" {normalize_text(text)} ")


class MaterialCatalog(Protocol):
    search_language: str

    def search(self, phrase: str) -> list[tuple[str, str]]: ...
    def photo(self, lookup: tuple[str, str]) -> MaterialRecord | None: ...
    def download(self, photo: MaterialRecord) -> tuple[bytes, str]: ...
