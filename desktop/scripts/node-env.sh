#!/usr/bin/env bash

desktop_prefer_system_node() {
  if [[ -x /usr/bin/node ]]; then
    export PATH="/usr/bin:$PATH"
  fi
}

desktop_assert_supported_node() {
  node -e '
const [major, minor] = process.versions.node.split(".").map(Number);
const ok = (major === 20 && minor >= 19) || (major === 22 && minor >= 12) || major > 22;
if (!ok) {
  console.error(`Node ${process.versions.node} is not supported for this build. Use Node 20.19+ or 22.12+.`);
  process.exit(1);
}
'
}
