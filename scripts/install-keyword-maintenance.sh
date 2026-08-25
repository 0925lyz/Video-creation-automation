#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/jaguartv-content-factory-vnext}"
SERVICE_USER="${SERVICE_USER:-ubuntu}"
PYTHON_BIN="${PYTHON_BIN:-$APP_DIR/.venv/bin/python}"
RUNTIME_KEYWORDS="$APP_DIR/workspace/runtime/daily_keywords.txt"
DATABASE="$APP_DIR/workspace/factory.db"
BACKUP_DIR="$APP_DIR/workspace/backups/keyword-maintenance"

sudo install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$APP_DIR/workspace/runtime" "$BACKUP_DIR"

sudo tee /etc/systemd/system/jaguartv-import-daily-keywords.service >/dev/null <<EOF
[Unit]
Description=Import categorized JaguarTV daily keywords
ConditionPathExists=$RUNTIME_KEYWORDS

[Service]
Type=oneshot
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
ExecStart=$PYTHON_BIN $APP_DIR/scripts/import_daily_keywords.py $RUNTIME_KEYWORDS --db $DATABASE --date today
EOF

sudo tee /etc/systemd/system/jaguartv-import-daily-keywords.timer >/dev/null <<'EOF'
[Unit]
Description=Import JaguarTV daily keywords after the Beijing 05:00 discovery job

[Timer]
OnCalendar=*-*-* 05:10:00 Asia/Shanghai
Persistent=true
Unit=jaguartv-import-daily-keywords.service

[Install]
WantedBy=timers.target
EOF

sudo tee /etc/systemd/system/jaguartv-clear-tag-keywords.service >/dev/null <<EOF
[Unit]
Description=Clear imported JaguarTV tag keywords every two days

[Service]
Type=oneshot
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
ExecStart=$PYTHON_BIN $APP_DIR/scripts/clear_tag_keywords.py --db $DATABASE --backup-dir $BACKUP_DIR
EOF

sudo tee /etc/systemd/system/jaguartv-clear-tag-keywords.timer >/dev/null <<'EOF'
[Unit]
Description=Clear imported JaguarTV tag keywords every two days

[Timer]
OnActiveSec=2d
OnUnitActiveSec=2d
AccuracySec=1min
Unit=jaguartv-clear-tag-keywords.service

[Install]
WantedBy=timers.target
EOF

sudo tee /etc/systemd/system/jaguartv-carry-forward-tag-keywords.service >/dev/null <<EOF
[Unit]
Description=Carry latest JaguarTV tag keywords forward when today's import is absent

[Service]
Type=oneshot
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
ExecStart=$PYTHON_BIN $APP_DIR/scripts/carry_forward_tag_keywords.py --db $DATABASE --date today --backup-dir $BACKUP_DIR
EOF

sudo tee /etc/systemd/system/jaguartv-carry-forward-tag-keywords.timer >/dev/null <<'EOF'
[Unit]
Description=Carry JaguarTV tag keywords forward after the daily import window

[Timer]
OnCalendar=*-*-* 05:20:00 Asia/Shanghai
Persistent=true
Unit=jaguartv-carry-forward-tag-keywords.service

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now \
  jaguartv-import-daily-keywords.timer \
  jaguartv-clear-tag-keywords.timer \
  jaguartv-carry-forward-tag-keywords.timer
