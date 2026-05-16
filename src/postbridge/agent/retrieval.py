from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from fnmatch import fnmatch
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from postbridge.config import get_settings
from postbridge.domain.errors import ExternalApiError


def fetch_url_source(url: str, *, timeout_seconds: float = 15.0) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds), follow_redirects=True) as client:
            response = client.get(url, headers={"User-Agent": "postbridge-agent/1.0"})
    except httpx.TimeoutException as exc:
        raise ExternalApiError(
            code="EXTERNAL_SOURCE_FETCH_TIMEOUT",
            message="source fetch timed out",
            source="agent_retrieval",
            retryable=True,
            details={"url": url},
        ) from exc
    except httpx.HTTPError as exc:
        raise ExternalApiError(
            code="EXTERNAL_SOURCE_FETCH_TRANSPORT",
            message="source fetch transport error",
            source="agent_retrieval",
            retryable=True,
            details={"url": url, "reason": str(exc)},
        ) from exc
    if response.status_code >= 400:
        raise ExternalApiError(
            code="EXTERNAL_SOURCE_FETCH_HTTP_ERROR",
            message="source fetch returned error status",
            source="agent_retrieval",
            retryable=response.status_code >= 500 or response.status_code == 429,
            details={"url": url, "status_code": response.status_code},
        )
    text = response.text
    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    title = html.unescape(_collapse_ws(_strip_tags(title_match.group(1)))) if title_match else None
    plain = _collapse_ws(_strip_tags(text))
    published_at, updated_at = _extract_page_timestamps(text)
    image_urls = _extract_page_image_urls(text, base_url=str(response.url))
    return {
        "url": str(response.url),
        "title": title,
        "text_excerpt": plain[:4000],
        "published_at": published_at,
        "updated_at": updated_at,
        "image_urls": image_urls,
        "preview_image_url": image_urls[0] if image_urls else None,
    }


def fetch_seed_sources(urls: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in urls:
        url = (raw or "").strip()
        if not url:
            continue
        item = fetch_url_source(url)
        item["retrieval_score"] = 1.0
        item["retrieval_backend"] = "seed_urls"
        item["retrieval_reason"] = "explicit seed url"
        out.append(item)
    return out


def search_sources(query: str, *, max_results: int | None = None) -> list[dict[str, Any]]:
    settings = get_settings()
    limit = max_results or settings.agent_search_max_results
    backends = _search_backends(settings)
    if not backends or backends == ["disabled"]:
        return []
    variants = _expand_search_queries(query, max_variants=settings.agent_search_query_variants)
    merged: list[dict[str, Any]] = []
    last_error: ExternalApiError | None = None
    for variant_rank, variant in enumerate(variants, start=1):
        for backend in backends:
            try:
                results = _search_with_backend(backend, variant, max_results=max(limit * 2, limit))
            except ExternalApiError as exc:
                last_error = exc
                continue
            for item in results:
                enriched = dict(item)
                enriched["search_query_variant"] = variant
                enriched["search_query_variant_rank"] = variant_rank
                enriched["search_backend"] = backend
                merged.append(enriched)
        if len(merged) >= max(limit * 3, limit):
            break
    if not merged and last_error is not None:
        raise last_error
    return _merge_search_results(merged, max_results=limit)


def collect_topic_sources(
    topic: str,
    *,
    seed_urls: list[str] | None = None,
    max_results: int | None = None,
    preferred_domains: set[str] | None = None,
    blocked_domains: set[str] | None = None,
    blocked_url_patterns: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    settings = get_settings()
    preferred = {str(item).strip().lower() for item in (preferred_domains or set()) if str(item).strip()}
    blocked = {
        str(item).strip().lower()
        for item in (set(settings.agent_search_blocked_domains) | set(blocked_domains or set()))
        if str(item).strip()
    }
    blocked_patterns = [
        str(item).strip()
        for item in (blocked_url_patterns or [])
        if isinstance(item, str) and str(item).strip()
    ]
    seeds = fetch_seed_sources(seed_urls or [])
    if seeds:
        return _apply_source_filters(
            seeds,
            allowed_domains=set(settings.agent_search_allowed_domains),
            blocked_domains=blocked,
            blocked_source_types=set(settings.agent_search_blocked_source_types),
            max_source_age_hours=settings.agent_search_max_source_age_hours,
            blocked_url_patterns=blocked_patterns,
        )
    search_results = search_sources(topic, max_results=max_results)
    sources: list[dict[str, Any]] = []
    for result in search_results[: settings.agent_search_fetch_budget]:
        url = (result.get("url") or "").strip()
        if not url:
            continue
        try:
            fetched = fetch_url_source(url)
        except ExternalApiError:
            continue
        fetched["search_snippet"] = result.get("snippet")
        fetched["search_backend"] = result.get("search_backend") or result.get("backend")
        fetched["search_backends"] = result.get("search_backends")
        fetched["search_rank"] = result.get("rank")
        fetched["search_score"] = result.get("search_score")
        fetched["search_query_variant"] = result.get("search_query_variant")
        fetched["search_query_variant_rank"] = result.get("search_query_variant_rank")
        sources.append(fetched)
    ranked = _rank_collected_sources(
        topic,
        sources,
        max_results=max_results or len(sources),
        preferred_domains=preferred,
    )
    return _apply_source_filters(
        ranked,
        allowed_domains=set(settings.agent_search_allowed_domains),
        blocked_domains=blocked,
        blocked_source_types=set(settings.agent_search_blocked_source_types),
        max_source_age_hours=settings.agent_search_max_source_age_hours,
        blocked_url_patterns=blocked_patterns,
    )


def _strip_tags(text: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return html.unescape(text)


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_page_image_urls(text: str, *, base_url: str) -> list[str]:
    candidates: list[str] = []
    meta_patterns = [
        r'(?is)<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'(?is)<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'(?is)<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']',
        r'(?is)<img[^>]+src=["\']([^"\']+)["\']',
    ]
    for pattern in meta_patterns:
        for raw in re.findall(pattern, text):
            normalized = _normalize_image_url(raw, base_url=base_url)
            if normalized:
                candidates.append(normalized)
    out: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
        if len(out) >= 8:
            break
    return out


def _normalize_image_url(raw: str, *, base_url: str) -> str | None:
    value = html.unescape((raw or "").strip())
    if not value or value.startswith("data:"):
        return None
    absolute = urljoin(base_url, value)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return absolute


def _duckduckgo_search(query: str, *, max_results: int) -> list[dict[str, Any]]:
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0), follow_redirects=True) as client:
            response = client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "postbridge-agent/1.0"},
            )
    except httpx.TimeoutException as exc:
        raise ExternalApiError(
            code="EXTERNAL_SEARCH_TIMEOUT",
            message="search request timed out",
            source="agent_search",
            retryable=True,
            details={"query": query},
        ) from exc
    except httpx.HTTPError as exc:
        raise ExternalApiError(
            code="EXTERNAL_SEARCH_TRANSPORT",
            message="search transport error",
            source="agent_search",
            retryable=True,
            details={"query": query, "reason": str(exc)},
        ) from exc
    if response.status_code >= 400:
        raise ExternalApiError(
            code="EXTERNAL_SEARCH_HTTP_ERROR",
            message="search returned error status",
            source="agent_search",
            retryable=response.status_code >= 500 or response.status_code == 429,
            details={"query": query, "status_code": response.status_code},
        )
    text = response.text
    links = re.findall(
        r'(?is)<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        text,
    )
    snippets = re.findall(r'(?is)<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', text)
    out: list[dict[str, Any]] = []
    for idx, (href, title_html) in enumerate(links[:max_results]):
        title = _collapse_ws(_strip_tags(title_html))
        snippet = _collapse_ws(_strip_tags(snippets[idx])) if idx < len(snippets) else None
        normalized_url = _normalize_duckduckgo_result_url(html.unescape(href))
        if not normalized_url:
            continue
        out.append(
            {
                "url": normalized_url,
                "title": title,
                "snippet": snippet,
                "rank": idx + 1,
                "search_score": round(max(0.0, 1.0 - idx * 0.08), 4),
                "backend": "duckduckgo",
            }
        )
    return out


def _normalize_duckduckgo_result_url(raw: str) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    if value.startswith("//"):
        value = f"https:{value}"
    parsed = urlparse(value)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [None])[0]
        if isinstance(target, str) and target.strip():
            value = target.strip()
            parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value


def _searxng_search(
    query: str,
    *,
    max_results: int,
    base_url: str,
    api_key: str | None,
    language: str | None,
) -> list[dict[str, Any]]:
    headers = {"User-Agent": "postbridge-agent/1.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    params = {"q": query, "format": "json"}
    if language:
        params["language"] = language
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0), follow_redirects=True) as client:
            response = client.get(f"{base_url.rstrip('/')}/search", params=params, headers=headers)
    except httpx.TimeoutException as exc:
        raise ExternalApiError(
            code="EXTERNAL_SEARCH_TIMEOUT",
            message="search request timed out",
            source="agent_search",
            retryable=True,
            details={"query": query, "backend": "searxng"},
        ) from exc
    except httpx.HTTPError as exc:
        raise ExternalApiError(
            code="EXTERNAL_SEARCH_TRANSPORT",
            message="search transport error",
            source="agent_search",
            retryable=True,
            details={"query": query, "backend": "searxng", "reason": str(exc)},
        ) from exc
    if response.status_code >= 400:
        raise ExternalApiError(
            code="EXTERNAL_SEARCH_HTTP_ERROR",
            message="search returned error status",
            source="agent_search",
            retryable=response.status_code >= 500 or response.status_code == 429,
            details={"query": query, "backend": "searxng", "status_code": response.status_code},
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ExternalApiError(
            code="EXTERNAL_SEARCH_INVALID_RESPONSE",
            message="search returned non-json body",
            source="agent_search",
            retryable=False,
            details={"query": query, "backend": "searxng"},
        ) from exc
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return []
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(results[:max_results]):
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        out.append(
            {
                "url": url.strip(),
                "title": _collapse_ws(str(item.get("title") or "")) or None,
                "snippet": _collapse_ws(str(item.get("content") or item.get("snippet") or "")) or None,
                "rank": idx + 1,
                "search_score": round(max(0.0, 1.0 - idx * 0.06), 4),
                "backend": "searxng",
            }
        )
    return out


def _search_with_backend(backend: str, query: str, *, max_results: int) -> list[dict[str, Any]]:
    settings = get_settings()
    if backend == "disabled":
        return []
    if backend == "duckduckgo":
        return _duckduckgo_search(query, max_results=max_results)
    if backend == "searxng":
        if not settings.agent_search_searxng_base_url:
            return []
        return _searxng_search(
            query,
            max_results=max_results,
            base_url=settings.agent_search_searxng_base_url or "",
            api_key=settings.agent_search_searxng_api_key,
            language=settings.agent_search_language,
        )
    return []


def _extract_page_timestamps(text: str) -> tuple[str | None, str | None]:
    candidates = {
        "published_at": [
            r'(?is)<meta[^>]+property="article:published_time"[^>]+content="([^"]+)"',
            r"(?is)<meta[^>]+name=\"pubdate\"[^>]+content=\"([^\"]+)\"",
            r"(?is)<time[^>]+datetime=\"([^\"]+)\"",
        ],
        "updated_at": [
            r'(?is)<meta[^>]+property="article:modified_time"[^>]+content="([^"]+)"',
            r'(?is)<meta[^>]+property="og:updated_time"[^>]+content="([^"]+)"',
        ],
    }
    published_at = _extract_first_datetime(text, candidates["published_at"])
    updated_at = _extract_first_datetime(text, candidates["updated_at"])
    return published_at, updated_at


def _extract_first_datetime(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        normalized = _normalize_datetime_string(match.group(1))
        if normalized:
            return normalized
    return None


def _normalize_datetime_string(raw: str) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _rank_collected_sources(
    topic: str,
    sources: list[dict[str, Any]],
    *,
    max_results: int,
    preferred_domains: set[str] | None = None,
) -> list[dict[str, Any]]:
    if not sources:
        return []
    topic_tokens = _tokenize(topic)
    preferred = {str(item).strip().lower() for item in (preferred_domains or set()) if str(item).strip()}
    domain_counts: dict[str, int] = {}
    for item in sources:
        domain = _domain(item.get("url"))
        if domain:
            domain_counts[domain] = domain_counts.get(domain, 0) + 1
    ranked: list[dict[str, Any]] = []
    for item in sources:
        title = item.get("title") or ""
        excerpt = item.get("text_excerpt") or item.get("search_snippet") or ""
        domain = _domain(item.get("url"))
        freshness = _source_recency_score(item.get("published_at") or item.get("updated_at"))
        title_overlap = _overlap(topic_tokens, _tokenize(title))
        excerpt_overlap = _overlap(topic_tokens, _tokenize(excerpt[:800]))
        domain_penalty = 0.1 * max(domain_counts.get(domain or "", 0) - 1, 0)
        preferred_bonus = 0.12 if domain and domain in preferred else 0.0
        score = (
            float(item.get("search_score") or 0.0) * 0.35
            + freshness * 0.25
            + title_overlap * 0.2
            + excerpt_overlap * 0.1
            + _query_variant_bonus(item.get("search_query_variant_rank")) * 0.05
            + (0.05 if isinstance(item.get("title"), str) and item.get("title") else 0.0)
            + (0.05 if isinstance(item.get("search_snippet"), str) and item.get("search_snippet") else 0.0)
            + preferred_bonus
            - domain_penalty
        )
        ranked_item = dict(item)
        ranked_item["retrieval_score"] = round(max(score, 0.0), 4)
        ranked_item["retrieval_backend"] = item.get("search_backend") or item.get("backend")
        ranked_item["retrieval_backends"] = item.get("search_backends") or [ranked_item["retrieval_backend"]]
        ranked_item["retrieval_reason"] = _build_retrieval_reason(
            freshness=freshness,
            title_overlap=title_overlap,
            excerpt_overlap=excerpt_overlap,
            domain_penalty=domain_penalty,
        )
        ranked_item["domain_preferred"] = bool(domain and domain in preferred)
        ranked.append(ranked_item)
    ranked.sort(key=lambda row: float(row.get("retrieval_score") or 0.0), reverse=True)
    for idx, item in enumerate(ranked, start=1):
        item["retrieval_rank"] = idx
    return ranked[:max_results]


def _apply_source_filters(
    sources: list[dict[str, Any]],
    *,
    allowed_domains: set[str],
    blocked_domains: set[str],
    blocked_source_types: set[str],
    max_source_age_hours: int,
    blocked_url_patterns: list[str] | tuple[str, ...],
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for item in sources:
        url = str(item.get("url") or "").strip()
        domain = _domain(item.get("url"))
        if allowed_domains and domain and domain not in allowed_domains:
            continue
        if blocked_domains and domain and domain in blocked_domains:
            continue
        if blocked_url_patterns and _matches_blocked_url_pattern(url, blocked_url_patterns):
            continue
        source_type = _infer_source_type(item)
        if blocked_source_types and source_type in blocked_source_types:
            continue
        age_hours = _source_age_hours(item.get("published_at") or item.get("updated_at"))
        if age_hours is not None and age_hours > float(max_source_age_hours):
            continue
        normalized = dict(item)
        normalized["source_type"] = normalized.get("source_type") or source_type
        filtered.append(normalized)
    return filtered


def _matches_blocked_url_pattern(url: str, patterns: list[str] | tuple[str, ...]) -> bool:
    value = str(url or "").strip().lower()
    if not value:
        return False
    for raw in patterns:
        pattern = str(raw or "").strip().lower()
        if pattern and fnmatch(value, pattern):
            return True
    return False


def _search_backends(settings: Any) -> list[str]:
    explicit = [item for item in settings.agent_search_backends if item]
    if explicit:
        return explicit
    backend = settings.agent_search_backend
    if backend == "auto":
        return ["searxng", "duckduckgo"]
    return [backend]


def _expand_search_queries(query: str, *, max_variants: int) -> list[str]:
    base = _collapse_ws(query)
    if not base:
        return []
    signals = _infer_topic_signals(base)
    variants = [base]
    locality_phrase = " ".join(signals["locality_tokens"][:3]).strip()
    if signals["intent"] == "news":
        variants.append(base if re.search(r"\b(news|новост|fresh|latest|update|updates)\b", base.lower()) else f"{base} latest news")
        if locality_phrase:
            variants.append(f"{locality_phrase} latest headlines")
    elif locality_phrase:
        variants.append(f"{base} update")
        variants.append(f"{locality_phrase} news")
    deduped: list[str] = []
    seen: set[str] = set()
    for item in variants:
        normalized = _collapse_ws(item)
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
        if len(deduped) >= max_variants:
            break
    return deduped


def _merge_search_results(results: list[dict[str, Any]], *, max_results: int) -> list[dict[str, Any]]:
    by_url: dict[str, dict[str, Any]] = {}
    for item in results:
        url = (item.get("url") or "").strip()
        if not url:
            continue
        current = by_url.get(url)
        if current is None:
            merged = dict(item)
            merged["search_backends"] = [str(item.get("search_backend") or item.get("backend") or "unknown")]
            by_url[url] = merged
            continue
        current_score = float(current.get("search_score") or 0.0)
        incoming_score = float(item.get("search_score") or 0.0)
        current_variant_rank = int(current.get("search_query_variant_rank") or 999)
        incoming_variant_rank = int(item.get("search_query_variant_rank") or 999)
        backends = set(current.get("search_backends") or [])
        backends.add(str(item.get("search_backend") or item.get("backend") or "unknown"))
        winner = current
        if incoming_score > current_score or (
            incoming_score == current_score and incoming_variant_rank < current_variant_rank
        ):
            winner = dict(item)
        winner["search_backends"] = sorted(backends)
        by_url[url] = winner
    merged = list(by_url.values())
    merged.sort(
        key=lambda item: (
            float(item.get("search_score") or 0.0),
            -int(item.get("search_query_variant_rank") or 999),
            -int(item.get("rank") or 999),
        ),
        reverse=True,
    )
    for idx, item in enumerate(merged, start=1):
        item["rank"] = idx
    return merged[:max_results]


def _infer_topic_signals(text: str) -> dict[str, Any]:
    tokens = _tokenize(text)
    lowered = text.lower()
    intent = "general"
    if any(token in lowered for token in ("news", "новост", "headline", "update", "updates", "daily", "свеж", "fresh", "latest")):
        intent = "news"
    locality_tokens = sorted(
        {
            token
            for token in tokens
            if len(token) >= 4 and token not in {"news", "daily", "fresh", "latest", "today", "новости", "свежие"}
        }
    )
    return {"intent": intent, "tokens": tokens, "locality_tokens": locality_tokens}


def _query_variant_bonus(raw_rank: Any) -> float:
    if not isinstance(raw_rank, int):
        return 0.0
    if raw_rank <= 1:
        return 1.0
    if raw_rank == 2:
        return 0.7
    if raw_rank == 3:
        return 0.4
    return 0.2


def _source_recency_score(raw: Any) -> float:
    if not isinstance(raw, str) or not raw:
        return 0.0
    value = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    age_hours = max((datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds() / 3600.0, 0.0)
    if age_hours <= 6:
        return 1.0
    if age_hours <= 24:
        return 0.8
    if age_hours <= 72:
        return 0.5
    return 0.15


def _source_age_hours(raw: Any) -> float | None:
    if not isinstance(raw, str) or not raw:
        return None
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max((datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds() / 3600.0, 0.0)


def _infer_source_type(item: dict[str, Any]) -> str:
    text = " ".join(
        str(part or "").lower()
        for part in [item.get("url"), item.get("title"), item.get("text_excerpt"), item.get("search_snippet")]
    )
    if any(marker in text for marker in ("docs.", "/docs", "documentation", "manual", "/help", "/kb/")):
        return "documentation"
    if any(marker in text for marker in ("t.me/", "telegram", "twitter.com", "x.com", "facebook.com", "instagram.com", "vk.com")):
        return "social"
    if any(marker in text for marker in ("reddit.com", "forum", "/community", "discuss", "comment")):
        return "community"
    if any(marker in text for marker in ("shop", "store", "product", "pricing", "catalog", "marketplace")):
        return "commercial"
    if any(marker in text for marker in ("news", "новости", "report", "breaking", "headline")):
        return "news_article"
    return "unknown"


def _tokenize(text: str) -> set[str]:
    return {token for token in re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ]+", " ", (text or "").lower()).split() if len(token) >= 3}


def _overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _domain(url: Any) -> str | None:
    if not isinstance(url, str) or "://" not in url:
        return None
    return (urlparse(url).hostname or "").lower() or None


def _build_retrieval_reason(*, freshness: float, title_overlap: float, excerpt_overlap: float, domain_penalty: float) -> str:
    parts: list[str] = []
    if freshness >= 0.8:
        parts.append("fresh source")
    elif freshness > 0.0:
        parts.append("dated source")
    if title_overlap >= 0.2:
        parts.append("title matches topic")
    if excerpt_overlap >= 0.15:
        parts.append("snippet matches topic")
    if domain_penalty > 0.0:
        parts.append("domain repetition penalty applied")
    return ", ".join(parts) or "baseline retrieval score"
