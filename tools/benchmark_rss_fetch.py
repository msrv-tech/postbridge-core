#!/usr/bin/env python3
"""Benchmark RSS fetch latency.

By default this performs HTTP GET through httpx and then parses the response body with feedparser.
Use --legacy-url-fetch to let feedparser open the URL itself.

Примеры:
  cd postbridge-core && .venv/bin/python tools/benchmark_rss_fetch.py
  RSS_URL=https://example.com/feed.xml python tools/benchmark_rss_fetch.py --rounds 5
  python tools/benchmark_rss_fetch.py --via-core
  python tools/benchmark_rss_fetch.py --legacy-url-fetch --rounds 2
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

import feedparser
import httpx


def _load_env(path: Path, *, overwrite: bool = False) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if not k:
            continue
        if overwrite or k not in os.environ:
            os.environ[k] = v


def _resolve_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _rss_url_from_env() -> str:
    for key in ("RSS_URL", "E2E_RSS_FETCH_URL", "BENCH_RSS_URL"):
        u = (os.environ.get(key) or "").strip()
        if u.startswith("https://") or u.startswith("http://"):
            return u
    return ""


def bench_httpx_then_parse(
    rss_url: str,
    *,
    rounds: int,
    limit: int,
    http_timeout: float,
) -> None:
    """GET с таймаутом + парсинг тела (предсказуемо при блокирующих хостах)."""
    ua = "Postbridge-bench/1.0"
    times: list[float] = []
    fetch_ms: list[float] = []
    parse_ms: list[float] = []
    parsed_last = None
    timeout = httpx.Timeout(http_timeout)
    for i in range(rounds):
        t0 = time.perf_counter()
        with httpx.Client(timeout=timeout) as client:
            t_f0 = time.perf_counter()
            r = client.get(rss_url, headers={"User-Agent": ua})
            r.raise_for_status()
            body = r.content
            t_f1 = time.perf_counter()
            parsed = feedparser.parse(body)
            t_p1 = time.perf_counter()
        dt = t_p1 - t0
        times.append(dt)
        fetch_ms.append((t_f1 - t_f0) * 1000)
        parse_ms.append((t_p1 - t_f1) * 1000)
        parsed_last = parsed
        entries_n = len((parsed.entries or [])[:limit])
        print(
            f"  round {i + 1}/{rounds}: total={dt * 1000:.1f} ms  "
            f"http={fetch_ms[-1]:.1f} ms  parse={parse_ms[-1]:.1f} ms  "
            f"entries(<={limit})={entries_n}  bozo={bool(parsed.bozo)}"
        )
    print(
        f"httpx+parse: median={statistics.median(times) * 1000:.1f} ms  "
        f"mean={statistics.mean(times) * 1000:.1f} ms  min={min(times) * 1000:.1f} ms  max={max(times) * 1000:.1f} ms"
    )
    print(
        f"  (доля) http median={statistics.median(fetch_ms):.1f} ms  "
        f"parse median={statistics.median(parse_ms):.1f} ms"
    )
    if parsed_last is not None and not (parsed_last.entries or []):
        print("  (предупреждение: 0 записей — проверьте URL и тело ответа)", file=sys.stderr)


def bench_feedparser_opens_url(rss_url: str, *, rounds: int, limit: int) -> None:
    """Как Core RSSFetcher._fetch_sync: feedparser сам качает URL (без явного таймаута)."""
    times: list[float] = []
    parsed_last = None
    for i in range(rounds):
        t0 = time.perf_counter()
        parsed = feedparser.parse(rss_url, agent="Postbridge-bench/1.0")
        dt = time.perf_counter() - t0
        times.append(dt)
        parsed_last = parsed
        entries_n = len((parsed.entries or [])[:limit])
        print(f"  round {i + 1}/{rounds}: {dt * 1000:.1f} ms  entries(<={limit})={entries_n}  bozo={bool(parsed.bozo)}")
    print(
        f"feedparser.parse(URL): median={statistics.median(times) * 1000:.1f} ms  "
        f"mean={statistics.mean(times) * 1000:.1f} ms  min={min(times) * 1000:.1f} ms  max={max(times) * 1000:.1f} ms"
    )
    if parsed_last is not None and not (parsed_last.entries or []):
        print("  (предупреждение: 0 записей — проверьте URL и доступность фида)", file=sys.stderr)


def bench_core_fetch_posts(
    rss_url: str,
    *,
    base_url: str,
    token: str | None,
    tenant_id: str,
    source_core_channel_id: str,
    rounds: int,
    limit: int,
) -> None:
    headers: dict[str, str] = {}
    if token:
        headers["X-Sync-Publish-Token"] = token
    body = {
        "tenant_id": tenant_id,
        "source_core_channel_id": source_core_channel_id,
        "source_platform": "rss",
        "source_channel": rss_url,
        "limit": limit,
    }
    times: list[float] = []
    last_status = 0
    for i in range(rounds):
        t0 = time.perf_counter()
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=120.0, headers=headers) as client:
            r = client.post("/internal/fetch-posts", json=body)
        dt = time.perf_counter() - t0
        times.append(dt)
        last_status = r.status_code
        n = 0
        try:
            n = len((r.json() or {}).get("posts") or [])
        except Exception:
            pass
        print(
            f"  round {i + 1}/{rounds}: {dt * 1000:.1f} ms  HTTP {r.status_code}  posts={n}"
        )
    print(
        f"POST /internal/fetch-posts: median={statistics.median(times) * 1000:.1f} ms  "
        f"mean={statistics.mean(times) * 1000:.1f} ms  min={min(times) * 1000:.1f} ms  max={max(times) * 1000:.1f} ms"
    )
    if last_status >= 400:
        print(f"  последний статус {last_status} — проверьте Core, SYNC_PUBLISH_TOKEN", file=sys.stderr)


async def bench_async_to_thread(
    rss_url: str,
    *,
    rounds: int,
    limit: int,
    http_timeout: float,
    legacy_url: bool,
) -> None:
    """Как RSSFetcher: asyncio.to_thread(...). С legacy_url — parse(URL); иначе httpx+parse."""

    def _work_legacy() -> int:
        p = feedparser.parse(rss_url, agent="Postbridge-bench/1.0")
        return len((p.entries or [])[:limit])

    def _work_httpx() -> int:
        with httpx.Client(timeout=httpx.Timeout(http_timeout)) as c:
            r = c.get(rss_url, headers={"User-Agent": "Postbridge-bench/1.0"})
            r.raise_for_status()
            p = feedparser.parse(r.content)
        return len((p.entries or [])[:limit])

    fn = _work_legacy if legacy_url else _work_httpx
    times: list[float] = []
    for i in range(rounds):
        t0 = time.perf_counter()
        n = await asyncio.to_thread(fn)
        dt = time.perf_counter() - t0
        times.append(dt)
        print(f"  round {i + 1}/{rounds}: {dt * 1000:.1f} ms  entries(<=limit)={n}")
    label = "asyncio.to_thread(parse URL)" if legacy_url else "asyncio.to_thread(httpx+parse)"
    print(f"{label}: median={statistics.median(times) * 1000:.1f} ms  mean={statistics.mean(times) * 1000:.1f} ms")


def main() -> int:
    root = _resolve_root()
    _load_env(root / ".env")

    ap = argparse.ArgumentParser(description="Замер чтения RSS (feedparser и опционально Core).")
    ap.add_argument("--url", default="", help="URL ленты (иначе RSS_URL / E2E_RSS_FETCH_URL)")
    ap.add_argument(
        "--rounds",
        type=int,
        default=max(1, int(os.environ.get("BENCH_ROUNDS", "3"))),
    )
    ap.add_argument("--limit", type=int, default=5, help="limit постов для fetch-posts / обрезка entries")
    ap.add_argument(
        "--via-core",
        action="store_true",
        help="Дополнительно замерить POST /internal/fetch-posts (нужен поднятый Core)",
    )
    ap.add_argument(
        "--fetch-tenant-id",
        default=os.environ.get("BENCH_FETCH_TENANT_ID", ""),
        help="UUID tenant Core для fetch-posts (или env BENCH_FETCH_TENANT_ID)",
    )
    ap.add_argument(
        "--fetch-source-core-channel-id",
        default=os.environ.get("BENCH_FETCH_SOURCE_CORE_CHANNEL_ID", ""),
        help="UUID канала-источника в Core с channel_credentials (или env BENCH_FETCH_SOURCE_CORE_CHANNEL_ID)",
    )
    ap.add_argument(
        "--core-only",
        action="store_true",
        help="Только Core (без прямого feedparser)",
    )
    ap.add_argument(
        "--async-thread",
        action="store_true",
        help="Дополнительно: замер через asyncio.to_thread (как в RSSFetcher)",
    )
    ap.add_argument(
        "--http-timeout",
        type=float,
        default=float(os.environ.get("BENCH_RSS_HTTP_TIMEOUT", "30")),
        help="Таймаут HTTP для httpx (сек)",
    )
    ap.add_argument(
        "--legacy-url-fetch",
        action="store_true",
        help="Вместо httpx: feedparser.parse(URL) как в Core (может долго висеть без таймаута)",
    )
    args = ap.parse_args()

    rss_url = (args.url or _rss_url_from_env()).strip()
    if not rss_url:
        print(
            "Задайте URL: --url https://... или RSS_URL / E2E_RSS_FETCH_URL в env / .env.shared",
            file=sys.stderr,
        )
        return 2

    core_base = (
        os.environ.get("E2E_CORE_BASE_URL")
        or os.environ.get("CORE_BASE_URL")
        or "http://127.0.0.1:8010"
    ).strip()
    sync_token = (os.environ.get("E2E_SYNC_PUBLISH_TOKEN") or os.environ.get("SYNC_PUBLISH_TOKEN") or "").strip()

    print(f"RSS URL: {rss_url[:80]}{'…' if len(rss_url) > 80 else ''}")
    print(f"rounds={args.rounds} limit={args.limit} http_timeout={args.http_timeout}s\n")

    if not args.core_only:
        if args.legacy_url_fetch:
            print("=== 1) feedparser.parse(URL) — как RSSFetcher в Core (без таймаута на URL) ===")
            bench_feedparser_opens_url(rss_url, rounds=args.rounds, limit=args.limit)
        else:
            print("=== 1) httpx GET + feedparser.parse(body) — с таймаутом HTTP ===")
            try:
                bench_httpx_then_parse(
                    rss_url,
                    rounds=args.rounds,
                    limit=args.limit,
                    http_timeout=args.http_timeout,
                )
            except httpx.HTTPError as e:
                print(f"  ошибка HTTP: {e}", file=sys.stderr)
                return 1
        print()

    if args.async_thread and not args.core_only:
        print("=== 2) asyncio.to_thread(...) — как обёртка RSSFetcher.fetch_posts ===")
        asyncio.run(
            bench_async_to_thread(
                rss_url,
                rounds=args.rounds,
                limit=args.limit,
                http_timeout=args.http_timeout,
                legacy_url=args.legacy_url_fetch,
            )
        )
        print()

    if args.via_core or args.core_only:
        tid = (args.fetch_tenant_id or "").strip()
        scid = (args.fetch_source_core_channel_id or "").strip()
        if not tid or not scid:
            print(
                "Для --via-core нужны --fetch-tenant-id и --fetch-source-core-channel-id "
                "(или BENCH_FETCH_TENANT_ID / BENCH_FETCH_SOURCE_CORE_CHANNEL_ID)",
                file=sys.stderr,
            )
            return 2
        print(f"=== POST /internal/fetch-posts  base={core_base} ===")
        if not sync_token:
            print("  (нет SYNC_PUBLISH_TOKEN в env — если Core требует токен, будет 401)", file=sys.stderr)
        bench_core_fetch_posts(
            rss_url,
            base_url=core_base,
            token=sync_token or None,
            tenant_id=tid,
            source_core_channel_id=scid,
            rounds=args.rounds,
            limit=args.limit,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
