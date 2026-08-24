#!/usr/bin/env bash
set -euo pipefail

# Pull latest GitHub code on the server and restart the Content Factory service.
#
# Usage on server:
#   APP_DIR=/opt/jaguartv-content-factory-vnext bash scripts/server-sync.sh

APP_DIR="${APP_DIR:-/opt/jaguartv-content-factory-vnext}"
REPO_URL="${REPO_URL:-https://github.com/0925lyz/Video-creation-automation.git}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-jaguartv-content-factory-vnext}"
PYTHON_BIN="${PYTHON_BIN:-$APP_DIR/.venv/bin/python}"
NODE_MIN_MAJOR="${NODE_MIN_MAJOR:-22}"

if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "Not a git checkout: $APP_DIR" >&2
  exit 1
fi

git_has_changes() {
  ! git -C "$APP_DIR" diff --quiet ||
    ! git -C "$APP_DIR" diff --cached --quiet ||
    [[ -n "$(git -C "$APP_DIR" ls-files --others --exclude-standard)" ]]
}

if git_has_changes; then
  echo "Refusing to deploy over server-side changes. Review them first." >&2
  git -C "$APP_DIR" status --short >&2
  exit 2
fi

echo "==> Pulling latest code"
git -C "$APP_DIR" remote set-url origin "$REPO_URL"
git -C "$APP_DIR" fetch origin "$BRANCH"
git -C "$APP_DIR" checkout -B "$BRANCH" "origin/$BRANCH"

cd "$APP_DIR"

NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
if [[ "$NODE_MAJOR" -lt "$NODE_MIN_MAJOR" ]]; then
  echo "==> Installing Node.js $NODE_MIN_MAJOR for Remotion tooling"
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MIN_MAJOR}.x" | sudo -E bash -
  sudo apt-get install -y nodejs
fi

echo "==> Updating Python package"
"$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel
"$PYTHON_BIN" -m pip install -e "$APP_DIR[test]"

if [[ -f "$APP_DIR/workspace/factory.db" ]]; then
  echo "==> Applying compatible database migration"
  "$PYTHON_BIN" "$APP_DIR/scripts/migrate_db.py" "$APP_DIR/workspace/factory.db" --verify-rollback
fi

echo "==> Updating Node helper packages"
npm --prefix "$APP_DIR" ci --no-audit --no-fund
npm --prefix "$APP_DIR/src/jaguartv_factory/remotion_template" ci --no-audit --no-fund

if ! command -v deno >/dev/null 2>&1 || ! deno --version 2>/dev/null | head -n 1 | grep -Eq 'deno (2\.([3-9]|[1-9][0-9]+)\.|([3-9]|[1-9][0-9]+)\.)'; then
  echo "==> Installing a supported Deno runtime for YouTube extraction"
  DENO_VERSION="${DENO_VERSION:-$(curl -fsSL https://api.github.com/repos/denoland/deno/releases/latest | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')}"
  case "$(uname -m)" in
    x86_64) DENO_TARGET="x86_64-unknown-linux-gnu" ;;
    aarch64|arm64) DENO_TARGET="aarch64-unknown-linux-gnu" ;;
    *) echo "Unsupported architecture for Deno: $(uname -m)" >&2; exit 1 ;;
  esac
  DENO_TEMP="$(mktemp -d)"
  curl -fsSL "https://github.com/denoland/deno/releases/download/$DENO_VERSION/deno-$DENO_TARGET.zip" -o "$DENO_TEMP/deno.zip"
  "$PYTHON_BIN" -m zipfile -e "$DENO_TEMP/deno.zip" "$DENO_TEMP"
  sudo install -m 0755 "$DENO_TEMP/deno" /usr/local/bin/deno
  rm -rf "$DENO_TEMP"
fi

echo "==> Verifying install"
"$PYTHON_BIN" -m pytest tests/test_core.py tests/test_platform_and_brand.py tests/test_localization.py
"$APP_DIR/.agents/skills/jaguartv-content-factory/scripts/factory.sh" doctor
npm --prefix "$APP_DIR" run build
"$APP_DIR/scripts/remotion-smoke.sh"

echo "==> Restarting service"
bash "$APP_DIR/scripts/install-source-services.sh"
bash "$APP_DIR/scripts/install-youtube-analytics-worker.sh"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager

echo "Synced: $APP_DIR@$BRANCH"
