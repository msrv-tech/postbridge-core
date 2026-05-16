#!/usr/bin/env bash
# Проверка: из сервиса api контейнера SOCKS на хосте доступен и открывает api.telegram.org.
# Запуск из корня postbridge-core: ./tools/check_telegram_proxy_from_docker.sh
# Требует: docker compose up -d api, Xray с listen 0.0.0.0:10808 (см. docs/telegram-proxy.md).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PROXY="${TELEGRAM_PROXY_URL:-socks5h://host.docker.internal:10808}"
export TELEGRAM_PROXY_URL="$PROXY"
docker compose exec -T api python -c "
import os, sys
import httpx
u = (os.environ.get('TELEGRAM_PROXY_URL') or '').strip()
if not u:
    print('TELEGRAM_PROXY_URL пуст — пропуск проверки прокси', file=sys.stderr)
    sys.exit(0)
try:
    r = httpx.get('https://api.telegram.org', proxy=u, timeout=60.0)
    print('OK', u, '-> api.telegram.org', r.status_code)
except Exception as e:
    print('FAIL', u, e, file=sys.stderr)
    sys.exit(1)
"
