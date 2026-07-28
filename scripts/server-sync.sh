#!/usr/bin/env bash
set -euo pipefail

# Pull latest GitHub code on the server and restart the Content Factory service.
#
# Usage on server:
#   APP_DIR=/opt/jaguartv-content-factory-vnext bash scripts/server-sync.sh

APP_DIR="${APP_DIR:-/opt/jaguartv-content-factory-vnext}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-jaguartv-content-factory-vnext}"

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

if ! command -v deno >/dev/null 2>&1 || ! deno --version 2>/dev/null | head -n 1 | grep -Eq 'deno (2\.([3-9]|[1-9][0-9]+)\.|([3-9]|[1-9][0-9]+)\.)'; then
  echo "==> Installing a supported Deno runtime for YouTube extraction"
  DENO_VERSION="${DENO_VERSION:-$(curl -fsSL https://api.github.com/repos/denoland/deno/releases/latest | "$APP_DIR/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')}"
  case "$(uname -m)" in
    x86_64) DENO_TARGET="x86_64-unknown-linux-gnu" ;;
    aarch64|arm64) DENO_TARGET="aarch64-unknown-linux-gnu" ;;
    *) echo "Unsupported architecture for Deno: $(uname -m)" >&2; exit 1 ;;
  esac
  DENO_TEMP="$(mktemp -d)"
  curl -fsSL "https://github.com/denoland/deno/releases/download/$DENO_VERSION/deno-$DENO_TARGET.zip" -o "$DENO_TEMP/deno.zip"
  "$APP_DIR/.venv/bin/python" -m zipfile -e "$DENO_TEMP/deno.zip" "$DENO_TEMP"
  sudo install -m 0755 "$DENO_TEMP/deno" /usr/local/bin/deno
  rm -rf "$DENO_TEMP"
fi

echo "==> Restarting service"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager

echo "Synced: $APP_DIR@$BRANCH"
