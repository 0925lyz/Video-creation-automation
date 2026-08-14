#!/usr/bin/env bash
set -euo pipefail

# Connect this GitHub repository to the Tencent Cloud server, install/update
# dependencies, restart services, and run the render smoke test on the server.

SERVER_HOST="${JAGUARTV_SERVER_HOST:-43.134.128.197}"
SERVER_USER="${JAGUARTV_SERVER_USER:-ubuntu}"
SERVER_KEY="${JAGUARTV_SERVER_KEY:-$HOME/.ssh/jarg_tencent.pem}"
SERVER_DIR="${JAGUARTV_SERVER_DIR:-/opt/jaguartv-content-factory-vnext}"
REPO_URL="${REPO_URL:-https://github.com/0925lyz/Video-creation-automation.git}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-jaguartv-content-factory-vnext}"
JAGUARTV_HOST="${JAGUARTV_HOST:-127.0.0.1}"
JAGUARTV_PORT="${JAGUARTV_PORT:-8788}"

if [[ ! -f "$SERVER_KEY" ]]; then
  echo "Missing server SSH key: $SERVER_KEY" >&2
  echo "Set JAGUARTV_SERVER_KEY or place the Tencent key at ~/.ssh/jarg_tencent.pem." >&2
  exit 2
fi

chmod 600 "$SERVER_KEY"

ssh \
  -i "$SERVER_KEY" \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=accept-new \
  "${SERVER_USER}@${SERVER_HOST}" \
  "set -euo pipefail
   if [[ -d $(printf '%q' "$SERVER_DIR")/.git ]]; then
     cd $(printf '%q' "$SERVER_DIR")
     if ! git diff --quiet || ! git diff --cached --quiet || [[ -n \"\$(git ls-files --others --exclude-standard)\" ]]; then
       git stash push --include-untracked -m server-deploy-\$(date -u +%Y%m%dT%H%M%SZ)
     fi
     git remote set-url origin $(printf '%q' "$REPO_URL")
     git fetch origin $(printf '%q' "$BRANCH")
     git checkout -B $(printf '%q' "$BRANCH") origin/$(printf '%q' "$BRANCH")
   else
     sudo mkdir -p $(printf '%q' "$SERVER_DIR")
     sudo chown -R $(printf '%q' "$SERVER_USER"):$(printf '%q' "$SERVER_USER") $(printf '%q' "$SERVER_DIR")
     git clone --branch $(printf '%q' "$BRANCH") $(printf '%q' "$REPO_URL") $(printf '%q' "$SERVER_DIR")
     cd $(printf '%q' "$SERVER_DIR")
   fi
   APP_DIR=$(printf '%q' "$SERVER_DIR") \
   REPO_URL=$(printf '%q' "$REPO_URL") \
   BRANCH=$(printf '%q' "$BRANCH") \
   SERVICE_NAME=$(printf '%q' "$SERVICE_NAME") \
   JAGUARTV_HOST=$(printf '%q' "$JAGUARTV_HOST") \
   JAGUARTV_PORT=$(printf '%q' "$JAGUARTV_PORT") \
   bash scripts/server-install.sh"

echo "Deployed: ${SERVER_USER}@${SERVER_HOST}:${SERVER_DIR} (${BRANCH})"
