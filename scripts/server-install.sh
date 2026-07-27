#!/usr/bin/env bash
set -euo pipefail

# Tencent Cloud Lighthouse / Ubuntu-Debian server installer for JaguarTV Content Factory.
#
# Usage on server:
#   REPO_URL=git@github-jaguartv-l4:0925lyz/jaguartv-content-factory-vnext-l4.git \
#   APP_DIR=/opt/jaguartv-content-factory-vnext \
#   JAGUARTV_HOST=0.0.0.0 \
#   JAGUARTV_PORT=8787 \
#   bash scripts/server-install.sh
#
# Notes:
# - For the private GitHub repo, configure an SSH deploy key or use an HTTPS token URL before running.
# - This script intentionally keeps runtime media in APP_DIR/workspace, not in git.

APP_DIR="${APP_DIR:-/opt/jaguartv-content-factory-vnext}"
REPO_URL="${REPO_URL:-https://github.com/0925lyz/jaguartv-content-factory-vnext-l4.git}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-jaguartv-content-factory-vnext}"
JAGUARTV_HOST="${JAGUARTV_HOST:-0.0.0.0}"
JAGUARTV_PORT="${JAGUARTV_PORT:-8787}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"

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
if [[ "$NODE_MAJOR" -lt 18 ]]; then
  echo "==> Installing Node.js 20 for Remotion rendering"
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

echo "==> Preparing app directory: $APP_DIR"
sudo mkdir -p "$APP_DIR"
sudo chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR"

if [[ -d "$APP_DIR/.git" ]]; then
  echo "==> Updating existing repository"
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
else
  echo "==> Cloning repository"
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"

echo "==> Creating Python virtual environment"
"$PYTHON_BIN" -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/pip install -e ".[test]"

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
  {
    echo "JAGUARTV_HOST=$JAGUARTV_HOST"
    echo "JAGUARTV_PORT=$JAGUARTV_PORT"
    echo "JAGUARTV_EVENTS_TOKEN=$EVENTS_TOKEN"
    echo "JAGUARTV_UPLOAD_TOKEN=$UPLOAD_TOKEN"
  } > .env
  chmod 600 .env
fi

if ! grep -q '^JAGUARTV_UPLOAD_TOKEN=' .env; then
  UPLOAD_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
  echo "JAGUARTV_UPLOAD_TOKEN=$UPLOAD_TOKEN" >> .env
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
ExecStart=$APP_DIR/.venv/bin/jaguartv ui --host \${JAGUARTV_HOST:-$JAGUARTV_HOST} --port \${JAGUARTV_PORT:-$JAGUARTV_PORT}
Restart=always
RestartSec=5
KillSignal=SIGINT

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo "==> Running doctor"
.venv/bin/jaguartv doctor

echo
echo "Deployment complete."
echo "Local service status: sudo systemctl status $SERVICE_NAME --no-pager"
echo "Open in browser: http://<SERVER_PUBLIC_IP>:$JAGUARTV_PORT/"
echo "Reaction upload token is stored in $APP_DIR/.env (mode 600)."
