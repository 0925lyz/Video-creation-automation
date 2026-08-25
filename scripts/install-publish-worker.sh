#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/jaguartv-content-factory-vnext}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"
SERVICE_NAME="${PUBLISH_SERVICE_NAME:-jaguartv-youtube-publish-worker}"
JAGUARTV_BIN="${JAGUARTV_BIN:-$APP_DIR/.venv/bin/jaguartv}"
PUBLISH_SLEEP="${PUBLISH_SLEEP:-60}"
PUBLISH_LIMIT="${PUBLISH_LIMIT:-6}"

if [[ ! -x "$JAGUARTV_BIN" ]]; then
  echo "Missing JaguarTV executable: $JAGUARTV_BIN" >&2
  exit 2
fi

sudo tee "/etc/systemd/system/$SERVICE_NAME.service" >/dev/null <<EOF
[Unit]
Description=JaguarTV YouTube and X Publish Worker
After=network-online.target jaguartv-content-factory-vnext.service
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=-$APP_DIR/.env
ExecStart=$JAGUARTV_BIN --config config/pipeline.yaml publish-worker --sleep $PUBLISH_SLEEP --limit $PUBLISH_LIMIT
Restart=always
RestartSec=10
KillSignal=SIGINT

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager
