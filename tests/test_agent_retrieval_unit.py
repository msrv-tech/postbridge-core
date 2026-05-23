from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import Client as RealHttpxClient

from postbridge.agent import retrieval
from postbridge.agent.retrieval import (
    collect_topic_sources,
    fetch_url_source,
    search_sources,
)
from postbridge.domain.errors import ExternalApiError


def test_normalize_duckduckgo_result_url_supports_redirect_and_scheme_relative() -> None:
    assert retrieval._normalize_duckduckgo_result_url("//example.com/a") == "https://example.com/a"
    assert (
        retrieval._normalize_duckduckgo_result_url(
            "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Ftarget"
        )
        == "https://example.com/target"
    )
    assert retrieval._normalize_duckduckgo_result_url("javascript:alert(1)") is None


def test_extract_page_image_urls_normalizes_and_dedupes() -> None:
    html = """
    <html><head>
      <meta property="og:image" content="/img.png">
      <meta name="twitter:image" content="https://example.com/img.png">
      <meta property="og:image:secure_url" content="data:bad">
    </head>
    <body>
      <img src="/img.png">
    </body></html>
    """
    out = retrieval._extract_page_image_urls(html, base_url="https://example.com/page")
    assert out[0] == "https://example.com/img.png"
    assert out.count("https://example.com/img.png") == 1


def test_fetch_url_source_extracts_title_text_images_and_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    published = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    updated = (now - timedelta(hours=1)).isoformat()
    body = f"""
    <html><head>
      <title> Hello <b>world</b> </title>
      <meta property="article:published_time" content="{published}">
      <meta property="article:modified_time" content="{updated}">
      <meta property="og:image" content="/img.png">
    </head>
    <body>
      <script>ignored()</script>
      <style>.x{{}}</style>
      <p>Text <b>excerpt</b></p>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, request=request)

    monkeypatch.setattr(
        "postbridge.agent.retrieval.httpx.Client",
        lambda **kwargs: RealHttpxClient(transport=httpx.MockTransport(handler), **kwargs),
    )

    out = fetch_url_source("https://example.com/page")

    assert out["url"] == "https://example.com/page"
    assert out["title"] == "Hello world"
    assert "Text excerpt" in out["text_excerpt"]
    assert out["published_at"] is not None
    assert out["updated_at"] is not None
    assert out["preview_image_url"] == "https://example.com/img.png"


def test_fetch_url_source_wraps_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter([httpx.TimeoutException("slow"), httpx.Response(503)])

    def handler(request: httpx.Request) -> httpx.Response:
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(
        "postbridge.agent.retrieval.httpx.Client",
        lambda **kwargs: RealHttpxClient(transport=httpx.MockTransport(handler), **kwargs),
    )

    with pytest.raises(ExternalApiError) as timeout_exc:
        fetch_url_source("https://example.com/page")
    with pytest.raises(ExternalApiError) as http_exc:
        fetch_url_source("https://example.com/page")

    assert timeout_exc.value.code == "EXTERNAL_SOURCE_FETCH_TIMEOUT"
    assert http_exc.value.code == "EXTERNAL_SOURCE_FETCH_HTTP_ERROR"
    assert http_exc.value.retryable is True


def test_apply_source_filters_honors_domain_and_type_and_age() -> None:
    now = datetime.now(UTC)
    old = (now - timedelta(hours=400)).isoformat()
    fresh = (now - timedelta(hours=1)).isoformat()
    sources = [
        {"url": "https://allowed.example/docs/intro", "title": "Documentation", "published_at": fresh},
        {"url": "https://blocked.example/a", "title": "Ok", "published_at": fresh},
        {"url": "https://allowed.example/social", "title": "Telegram t.me/x", "published_at": fresh},
        {"url": "https://allowed.example/old", "title": "Old", "published_at": old},
        {"url": "https://allowed.example/pattern", "title": "Ok", "published_at": fresh},
    ]
    out = retrieval._apply_source_filters(
        sources,
        allowed_domains={"allowed.example"},
        blocked_domains={"blocked.example"},
        blocked_source_types={"social"},
        max_source_age_hours=24,
        blocked_url_patterns=["*://allowed.example/pattern*"],
    )
    assert [item["url"] for item in out] == ["https://allowed.example/docs/intro"]
    assert out[0]["source_type"] == "documentation"


def test_merge_search_results_dedupes_and_picks_highest_score() -> None:
    merged = retrieval._merge_search_results(
        [
            {"url": "https://example.com/a", "search_score": 0.4, "backend": "duckduckgo", "rank": 3},
            {"url": "https://example.com/a", "search_score": 0.9, "backend": "searxng", "rank": 9},
            {"url": "https://example.com/b", "search_score": 0.5, "backend": "duckduckgo", "rank": 1},
        ],
        max_results=5,
    )
    assert [item["url"] for item in merged] == ["https://example.com/a", "https://example.com/b"]
    assert merged[0]["search_backends"] == ["duckduckgo", "searxng"]
    assert merged[0]["rank"] == 1


def test_expand_search_queries_adds_news_variants_and_dedupes() -> None:
    variants = retrieval._expand_search_queries("Paris economy", max_variants=4)
    assert variants[0] == "Paris economy"
    assert any(item.endswith("update") for item in variants)
    assert any(item.endswith(" news") for item in variants)
    assert len({item.lower() for item in variants}) == len(variants)


def test_rank_collected_sources_adds_reason_and_preferred_bonus() -> None:
    now = datetime.now(UTC)
    fresh = (now - timedelta(hours=2)).isoformat()
    sources: list[dict[str, Any]] = [
        {
            "url": "https://preferred.example/a",
            "title": "Topic title matches",
            "search_score": 0.2,
            "published_at": fresh,
            "search_backend": "duckduckgo",
            "search_snippet": "topic snippet",
        },
        {
            "url": "https://other.example/b",
            "title": "Unrelated",
            "search_score": 0.9,
            "published_at": fresh,
            "search_backend": "duckduckgo",
            "search_snippet": "other",
        },
        {
            "url": "https://other.example/c",
            "title": "Unrelated again",
            "search_score": 0.8,
            "published_at": fresh,
            "search_backend": "duckduckgo",
            "search_snippet": "other",
        },
    ]
    ranked = retrieval._rank_collected_sources(
        "topic",
        sources,
        max_results=5,
        preferred_domains={"preferred.example"},
    )
    assert all("retrieval_score" in item for item in ranked)
    assert all("retrieval_reason" in item for item in ranked)
    assert all("retrieval_rank" in item for item in ranked)
    assert ranked[0]["retrieval_rank"] == 1
    assert any(item["domain_preferred"] for item in ranked)


def test_search_sources_raises_last_error_when_all_backends_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_SEARCH_BACKEND", "duckduckgo")

    def fail(backend: str, query: str, *, max_results: int) -> list[dict[str, Any]]:
        raise ExternalApiError(
            code="EXTERNAL_SEARCH_TIMEOUT",
            message="search request timed out",
            source="agent_search",
            retryable=True,
            details={"query": query},
        )

    monkeypatch.setattr(retrieval, "_search_with_backend", fail)
    monkeypatch.setattr(retrieval, "_expand_search_queries", lambda q, *, max_variants: [q])

    with pytest.raises(ExternalApiError) as exc_info:
        search_sources("topic", max_results=3)

    assert exc_info.value.code == "EXTERNAL_SEARCH_TIMEOUT"


def test_collect_topic_sources_returns_seed_sources_after_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_SEARCH_BACKEND", "disabled")
    monkeypatch.setenv("AGENT_SEARCH_ALLOWED_DOMAINS", "allowed.example")
    monkeypatch.setenv("AGENT_SEARCH_BLOCKED_DOMAINS", "")
    monkeypatch.setenv("AGENT_SEARCH_BLOCKED_SOURCE_TYPES", "")
    monkeypatch.setenv("AGENT_SEARCH_MAX_SOURCE_AGE_HOURS", "24")

    monkeypatch.setattr(
        retrieval,
        "fetch_seed_sources",
        lambda urls: [{"url": "https://allowed.example/a", "title": "Ok", "published_at": datetime.now(UTC).isoformat()}],
    )

    out = collect_topic_sources("topic", seed_urls=["https://allowed.example/a"])
    assert len(out) == 1
    assert out[0]["url"] == "https://allowed.example/a"
