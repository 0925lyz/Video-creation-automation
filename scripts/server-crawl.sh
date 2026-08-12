#!/usr/bin/env bash
set -euo pipefail

SERVER_HOST="${JAGUARTV_SERVER_HOST:-43.134.128.197}"
SERVER_USER="${JAGUARTV_SERVER_USER:-ubuntu}"
SERVER_KEY="${JAGUARTV_SERVER_KEY:-$HOME/.ssh/jarg_tencent.pem}"
SERVER_DIR="${JAGUARTV_SERVER_DIR:-/opt/jaguartv-content-factory-vnext}"
REPO_URL="${REPO_URL:-https://github.com/0925lyz/Video-creation-automation.git}"
BRANCH="${BRANCH:-main}"
CONFIG_PATH="${JAGUARTV_CONFIG:-config/pipeline.yaml}"
DISCOVER_LIMIT="${JAGUARTV_DISCOVER_LIMIT:-2}"
DOWNLOAD_LIMIT="${JAGUARTV_DOWNLOAD_LIMIT:-6}"
PLATFORMS="${JAGUARTV_PLATFORMS:-douyin tiktok facebook}"
SYNC_REMOTE="${JAGUARTV_SYNC_REMOTE:-1}"

if [[ ! -f "$SERVER_KEY" ]]; then
  echo "Missing server SSH key: $SERVER_KEY" >&2
  echo "Set JAGUARTV_SERVER_KEY or place the Tencent key at ~/.ssh/jarg_tencent.pem." >&2
  exit 2
fi

chmod 600 "$SERVER_KEY"

platform_args=()
for platform in $PLATFORMS; do
  platform_args+=(--platform "$platform")
done

remote_platform_args=""
for arg in "${platform_args[@]}"; do
  remote_platform_args+=" $(printf '%q' "$arg")"
done

if [[ "$SYNC_REMOTE" == "1" ]]; then
  ssh \
    -i "$SERVER_KEY" \
    -o IdentitiesOnly=yes \
    -o BatchMode=yes \
    "${SERVER_USER}@${SERVER_HOST}" \
    "set -euo pipefail
     cd $(printf '%q' "$SERVER_DIR")
     REPO_URL=$(printf '%q' "$REPO_URL") BRANCH=$(printf '%q' "$BRANCH") bash scripts/server-sync.sh"
fi

ssh \
  -i "$SERVER_KEY" \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  "${SERVER_USER}@${SERVER_HOST}" \
  "set -euo pipefail
   cd $(printf '%q' "$SERVER_DIR")
   ./.agents/skills/jaguartv-content-factory/scripts/factory.sh doctor
   .venv/bin/jaguartv --config $(printf '%q' "$CONFIG_PATH") discover${remote_platform_args} --limit $(printf '%q' "$DISCOVER_LIMIT")
   .venv/bin/jaguartv --config $(printf '%q' "$CONFIG_PATH") download --limit $(printf '%q' "$DOWNLOAD_LIMIT")
   .venv/bin/jaguartv --config $(printf '%q' "$CONFIG_PATH") list --status DOWNLOADED --limit 20"
