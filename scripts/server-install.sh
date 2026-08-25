#!/usr/bin/env bash
set -euo pipefail

# Tencent Cloud Lighthouse / Ubuntu-Debian server installer for JaguarTV Content Factory.
#
# Usage on server:
#   REPO_URL=https://github.com/0925lyz/Video-creation-automation.git \
#   APP_DIR=/opt/jaguartv-content-factory-vnext \
#   JAGUARTV_HOST=127.0.0.1 \
#   JAGUARTV_PORT=8788 \
#   bash scripts/server-install.sh
#
# Notes:
# - For private forks, configure an SSH deploy key or use an HTTPS token URL before running.
# - This script intentionally keeps runtime media in APP_DIR/workspace, not in git.

APP_DIR="${APP_DIR:-/opt/jaguartv-content-factory-vnext}"
REPO_URL="${REPO_URL:-https://github.com/0925lyz/Video-creation-automation.git}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-jaguartv-content-factory-vnext}"
JAGUARTV_HOST="${JAGUARTV_HOST:-127.0.0.1}"
JAGUARTV_PORT="${JAGUARTV_PORT:-8787}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
NODE_MIN_MAJOR="${NODE_MIN_MAJOR:-22}"

git_has_changes() {
  ! git -C "$APP_DIR" diff --quiet ||
    ! git -C "$APP_DIR" diff --cached --quiet ||
    [[ -n "$(git -C "$APP_DIR" ls-files --others --exclude-standard)" ]]
}

refuse_local_changes() {
  if [[ -d "$APP_DIR/.git" ]] && git_has_changes; then
    echo "Refusing to install over server-side changes. Review them first." >&2
    git -C "$APP_DIR" status --short >&2
    exit 2
  fi
}

if ! command -v sudo >/dev/null 2>&1; then
  echo "sudo is required. Please install sudo or run from a sudo-capable user." >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This installer currently supports Ubuntu/Debian servers with apt-get." >&2
  exit 1
fi

echo "==> Installing system dependencies"
sudo apt-get update
sudo apt-get install -y \
  ca-certificates curl git gnupg lsb-release software-properties-common \
  ffmpeg tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-chi-tra \
  build-essential pkg-config

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "==> Installing Python 3.12"
  if apt-cache show python3.12 >/dev/null 2>&1; then
    sudo apt-get install -y python3.12 python3.12-venv python3.12-dev
  else
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt-get update
    sudo apt-get install -y python3.12 python3.12-venv python3.12-dev
  fi
fi

NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
if [[ "$NODE_MAJOR" -lt "$NODE_MIN_MAJOR" ]]; then
  echo "==> Installing Node.js $NODE_MIN_MAJOR for Remotion tooling"
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MIN_MAJOR}.x" | sudo -E bash -
  sudo apt-get install -y nodejs
fi

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

echo "==> Preparing app directory: $APP_DIR"
sudo mkdir -p "$APP_DIR"
sudo chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR"

if [[ "${SKIP_GIT_UPDATE:-0}" == "1" ]]; then
  echo "==> Using preloaded Git commit"
  [[ -d "$APP_DIR/.git" ]] || { echo "Missing preloaded repository: $APP_DIR" >&2; exit 1; }
  refuse_local_changes
elif [[ -d "$APP_DIR/.git" ]]; then
  echo "==> Updating existing repository"
  refuse_local_changes
  git -C "$APP_DIR" remote set-url origin "$REPO_URL"
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" checkout -B "$BRANCH" "origin/$BRANCH"
else
  echo "==> Cloning repository"
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"

echo "==> Creating Python virtual environment"
"$PYTHON_BIN" -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/pip install -e ".[test]"

echo "==> Installing pinned discovery and download integrations"
APP_DIR="$APP_DIR" PYTHON_BIN="$APP_DIR/.venv/bin/python" bash scripts/install-runtime-integrations.sh

echo "==> Installing Node helper packages"
npm --prefix "$APP_DIR" ci --no-audit --no-fund
npm --prefix "$APP_DIR/src/jaguartv_factory/remotion_template" ci --no-audit --no-fund

mkdir -p workspace/server_media/review workspace/server_media/uploads/source \
  workspace/server_media/uploads/reaction assets/bgm

if [[ ! -f .env ]]; then
  EVENTS_TOKEN="$(.venv/bin/python - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
  UPLOAD_TOKEN="$(.venv/bin/python - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
  DASHBOARD_TOKEN="$(.venv/bin/python - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
  {
    echo "JAGUARTV_HOST=$JAGUARTV_HOST"
    echo "JAGUARTV_PORT=$JAGUARTV_PORT"
    echo "JAGUARTV_EVENTS_TOKEN=$EVENTS_TOKEN"
    echo "JAGUARTV_UPLOAD_TOKEN=$UPLOAD_TOKEN"
    echo "JAGUARTV_DASHBOARD_TOKEN=$DASHBOARD_TOKEN"
    echo "JAGUARTV_DASHBOARD_PUBLIC=0"
  } > .env
  chmod 600 .env
fi

if ! grep -q '^JAGUARTV_UPLOAD_TOKEN=' .env; then
  UPLOAD_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
  echo "JAGUARTV_UPLOAD_TOKEN=$UPLOAD_TOKEN" >> .env
fi

if ! grep -q '^JAGUARTV_DASHBOARD_TOKEN=' .env; then
  DASHBOARD_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
  echo "JAGUARTV_DASHBOARD_TOKEN=$DASHBOARD_TOKEN" >> .env
fi

echo "==> Installing systemd service: $SERVICE_NAME"
sudo tee "/etc/systemd/system/$SERVICE_NAME.service" >/dev/null <<EOF
[Unit]
Description=JaguarTV Content Factory vNEXT
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=-$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/jaguartv ui --host $JAGUARTV_HOST --port $JAGUARTV_PORT
Restart=always
RestartSec=5
KillSignal=SIGINT

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo "==> Installing source helper services"
bash scripts/install-source-services.sh

echo "==> Installing YouTube analytics worker"
bash scripts/install-youtube-analytics-worker.sh

echo "==> Installing publication worker"
bash scripts/install-publish-worker.sh

echo "==> Installing keyword maintenance timers"
bash scripts/install-keyword-maintenance.sh

echo "==> Verifying install"
.venv/bin/python -m pytest tests/test_core.py tests/test_platform_and_brand.py tests/test_localization.py
./.agents/skills/jaguartv-content-factory/scripts/factory.sh doctor
npm --prefix "$APP_DIR" run build
./scripts/remotion-smoke.sh

echo
echo "Deployment complete."
echo "Local service status: sudo systemctl status $SERVICE_NAME --no-pager"
echo "Open in browser: http://<SERVER_PUBLIC_IP>:$JAGUARTV_PORT/"
echo "Reaction upload token is stored in $APP_DIR/.env (mode 600)."
