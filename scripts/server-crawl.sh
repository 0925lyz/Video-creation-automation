#!/usr/bin/env bash
set -euo pipefail

SERVER_HOST="${JAGUARTV_SERVER_HOST:-43.134.128.197}"
SERVER_USER="${JAGUARTV_SERVER_USER:-ubuntu}"
SERVER_KEY="${JAGUARTV_SERVER_KEY:-$HOME/.ssh/jarg_tencent.pem}"
SERVER_DIR="${JAGUARTV_SERVER_DIR:-/opt/jaguartv-content-factory-vnext}"
CONFIG_PATH="${JAGUARTV_CONFIG:-config/pipeline.yaml}"
DISCOVER_LIMIT="${JAGUARTV_DISCOVER_LIMIT:-2}"
DOWNLOAD_LIMIT="${JAGUARTV_DOWNLOAD_LIMIT:-6}"
PLATFORMS="${JAGUARTV_PLATFORMS:-youtube bilibili douyin tiktok}"
SYNC_LOCAL="${JAGUARTV_SYNC_LOCAL:-1}"

if [[ ! -f "$SERVER_KEY" ]]; then
  echo "Missing server SSH key: $SERVER_KEY" >&2
  echo "Set JAGUARTV_SERVER_KEY or place the Tencent key at ~/.ssh/jarg_tencent.pem." >&2
  exit 2
fi

platform_args=()
for platform in $PLATFORMS; do
  platform_args+=(--platform "$platform")
done

remote_platform_args=""
for arg in "${platform_args[@]}"; do
  remote_platform_args+=" $(printf '%q' "$arg")"
done

if [[ "$SYNC_LOCAL" == "1" ]]; then
  tar -czf - \
    config/pipeline.yaml \
    config/keywords.brazil.yaml \
    src/jaguartv_factory/core.py |
    ssh \
      -i "$SERVER_KEY" \
      -o IdentitiesOnly=yes \
      -o BatchMode=yes \
      "${SERVER_USER}@${SERVER_HOST}" \
      "set -euo pipefail
       cd $(printf '%q' "$SERVER_DIR")
       tar -xzf -"
else
  ssh \
    -i "$SERVER_KEY" \
    -o IdentitiesOnly=yes \
    -o BatchMode=yes \
    "${SERVER_USER}@${SERVER_HOST}" \
    "set -euo pipefail
     cd $(printf '%q' "$SERVER_DIR")
     git pull --ff-only"
fi

ssh \
  -i "$SERVER_KEY" \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  "${SERVER_USER}@${SERVER_HOST}" \
  "set -euo pipefail
   cd $(printf '%q' "$SERVER_DIR")
   .venv/bin/jaguartv --config $(printf '%q' "$CONFIG_PATH") discover${remote_platform_args} --limit $(printf '%q' "$DISCOVER_LIMIT")
   .venv/bin/jaguartv --config $(printf '%q' "$CONFIG_PATH") download --limit $(printf '%q' "$DOWNLOAD_LIMIT")
   .venv/bin/jaguartv --config $(printf '%q' "$CONFIG_PATH") list --status DOWNLOADED --limit 20"
