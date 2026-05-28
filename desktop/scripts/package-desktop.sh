#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

source "$ROOT_DIR/desktop/scripts/node-env.sh"
desktop_prefer_system_node
desktop_assert_supported_node

npm --prefix "$ROOT_DIR/desktop" ci
npm --prefix "$ROOT_DIR/desktop" run build
