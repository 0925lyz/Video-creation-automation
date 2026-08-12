#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/jaguartv-content-factory-vnext}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"
PYTHON_BIN="${PYTHON_BIN:-$APP_DIR/.venv/bin/python}"
JAGUARTV_CONFIG="${JAGUARTV_CONFIG:-config/pipeline.yaml}"

if ! command -v sudo >/dev/null 2>&1; then
  echo "sudo is required." >&2
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python virtualenv not found: $PYTHON_BIN" >&2
  exit 1
fi

cd "$APP_DIR"
"$PYTHON_BIN" -m pip install -e ".[test]"
"$PYTHON_BIN" -m playwright install chromium

install_unit() {
  local name="$1"
  local mode="$2"
  local port="$3"
  sudo tee "/etc/systemd/system/$name.service" >/dev/null <<EOF
[Unit]
Description=JaguarTV $mode source helper
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=-$APP_DIR/.env
ExecStart=$PYTHON_BIN -m jaguartv_factory.source_service --config $JAGUARTV_CONFIG --host 127.0.0.1 --port $port --mode $mode
Restart=always
RestartSec=5
KillSignal=SIGINT

[Install]
WantedBy=multi-user.target
EOF
}

install_unit "jaguartv-douyin-source" "douyin" "8000"
install_unit "jaguartv-xhs-source" "xhs" "5556"

sudo systemctl daemon-reload
sudo systemctl enable jaguartv-douyin-source jaguartv-xhs-source
sudo systemctl restart jaguartv-douyin-source jaguartv-xhs-source
sudo systemctl status jaguartv-douyin-source --no-pager
sudo systemctl status jaguartv-xhs-source --no-pager
