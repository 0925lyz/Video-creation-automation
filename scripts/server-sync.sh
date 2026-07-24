#!/usr/bin/env bash
set -euo pipefail

# Pull latest GitHub code on the server and restart the Content Factory service.
#
# Usage on server:
#   APP_DIR=/opt/jaguartv-content-factory bash scripts/server-sync.sh

APP_DIR="${APP_DIR:-/opt/jaguartv-content-factory}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-jaguartv-content-factory}"

if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "Not a git checkout: $APP_DIR" >&2
  exit 1
fi

echo "==> Pulling latest code"
git -C "$APP_DIR" fetch origin "$BRANCH"
git -C "$APP_DIR" checkout "$BRANCH"
git -C "$APP_DIR" pull --ff-only origin "$BRANCH"

echo "==> Updating Python package"
"$APP_DIR/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$APP_DIR/.venv/bin/pip" install -e "$APP_DIR[test]"

echo "==> Restarting service"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager

echo "Synced: $APP_DIR@$BRANCH"

