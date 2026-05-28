#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

source "$ROOT_DIR/desktop/scripts/node-env.sh"
desktop_prefer_system_node
desktop_assert_supported_node

npm --prefix "$ROOT_DIR/web" ci
VITE_POSTBRIDGE_APP_MODE=selfhost npm --prefix "$ROOT_DIR/web" run build

rm -rf "$ROOT_DIR/desktop/runtime/bin/web"
mkdir -p "$ROOT_DIR/desktop/runtime/bin"
cp -R "$ROOT_DIR/web/dist" "$ROOT_DIR/desktop/runtime/bin/web"
