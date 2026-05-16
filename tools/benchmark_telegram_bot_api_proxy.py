#!/usr/bin/env python3
"""Compare Telegram Bot API getMe latency through TELEGRAM_PROXY_URL and directly.

Loads environment variables from postbridge-core/.env unless they are already set.

Пример:
  cd postbridge-core && .venv/bin/python tools/benchmark_telegram_bot_api_proxy.py
  BENCH_ROUNDS=5 python tools/benchmark_telegram_bot_api_proxy.py
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from pathlib import Path

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


def _measure_rounds(
    url: str, proxy: str | None, rounds: int, *, timeout: float = 60.0
) -> tuple[list[float], str | None]:
    """Возвращает (времена_успешных_раундов, ошибка_или_None)."""
    times: list[float] = []
    last_err: str | None = None
    for i in range(rounds):
        t0 = time.perf_counter()
        try:
            with httpx.Client(timeout=timeout, proxy=proxy) as c:
                r = c.get(url)
        except (httpx.TimeoutException, httpx.RequestError) as e:
            last_err = f"раунд {i + 1}: {type(e).__name__}: {e}"
            break
        elapsed = time.perf_counter() - t0
        times.append(elapsed)
        if r.status_code != 200:
            last_err = f"HTTP {r.status_code} {r.text[:200]}"
            break
    return times, last_err


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    _load_env(root / ".env", overwrite=False)
    token = (
        os.environ.get("TELEGRAM_BOT_TOKEN")
        or os.environ.get("E2E_TELEGRAM_BOT_TOKEN")
        or ""
    ).strip()
    if not token:
        print(
            "TELEGRAM_BOT_TOKEN is required in the environment or .env",
            file=sys.stderr,
        )
        return 1

    proxy_cfg = (os.environ.get("TELEGRAM_PROXY_URL") or "").strip()
    proxy_url = proxy_cfg if proxy_cfg else None
    url = f"https://api.telegram.org/bot{token}/getMe"
    rounds = int(os.environ.get("BENCH_ROUNDS", "3"))

    rows: list[tuple[str, str | None, list[float], str | None]] = []
    if proxy_url:
        tt, err = _measure_rounds(url, proxy_url, rounds)
        rows.append(("через TELEGRAM_PROXY_URL", proxy_url, tt, err))
    tt2, err2 = _measure_rounds(url, None, rounds)
    rows.append(("напрямую (proxy=None)", None, tt2, err2))

    print(f"getMe, до {rounds} раундов на режим (таймаут соединения 60s)")
    exit_code = 0
    for label, p, tt, err in rows:
        mode = label + (f" → {p}" if p else "")
        if not tt:
            print(f"  {mode}: нет успешных раундов. {err or 'unknown'}")
            exit_code = 1
            continue
        print(
            f"  {mode}: n={len(tt)} "
            f"min={min(tt):.3f}s avg={statistics.mean(tt):.3f}s max={max(tt):.3f}s"
        )
        if err:
            print(f"    (прервано: {err})")
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
