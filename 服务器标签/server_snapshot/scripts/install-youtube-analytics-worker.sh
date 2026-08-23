#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/jaguartv-content-factory-vnext}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"
SERVICE_NAME="${YOUTUBE_ANALYTICS_SERVICE_NAME:-jaguartv-youtube-analytics-worker}"
PYTHON_BIN="${PYTHON_BIN:-$APP_DIR/.venv/bin/jaguartv}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing JaguarTV executable: $PYTHON_BIN" >&2
  exit 2
fi

sudo tee "/etc/systemd/system/$SERVICE_NAME.service" >/dev/null <<EOF
[Unit]
Description=JaguarTV YouTube Analytics Sync Worker
After=network-online.target jaguartv-content-factory-vnext.service
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=-$APP_DIR/.env
ExecStart=$PYTHON_BIN --config config/pipeline.yaml youtube-analytics-worker --sleep 60 --limit 50
Restart=always
RestartSec=15
KillSignal=SIGINT

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager
