#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) platform="linux-x64" ;;
  MINGW64_NT*-x86_64|MSYS_NT*-x86_64|CYGWIN_NT*-x86_64) platform="windows-x64" ;;
  *) echo "Unsupported desktop runtime platform: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

core_dir="$ROOT_DIR/desktop/runtime/bin/$platform/core"
mkdir -p "$core_dir"

if [[ "$platform" == "windows-x64" ]]; then
  echo "Windows desktop runtime requires packaged .exe files; development shell wrappers are Unix-only." >&2
  exit 1
else
  cat > "$core_dir/postbridge-api" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
python -m postbridge.desktop_runtime api "$@"
EOF
  cat > "$core_dir/postbridge-worker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
python -m postbridge.desktop_runtime worker "$@"
EOF
  cat > "$core_dir/postbridge-migrate" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
python -m postbridge.desktop_runtime migrate "$@"
EOF
  chmod +x "$core_dir/postbridge-api" "$core_dir/postbridge-worker" "$core_dir/postbridge-migrate"
fi

echo "Created development backend runtime wrappers in $core_dir"
