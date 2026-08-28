#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/jaguartv-content-factory-vnext}"
SERVICE_USER="${SERVICE_USER:-ubuntu}"
PYTHON_BIN="${PYTHON_BIN:-$APP_DIR/.venv/bin/python}"
RUNTIME_KEYWORDS="$APP_DIR/workspace/runtime/daily_keywords.txt"
DATABASE="$APP_DIR/workspace/factory.db"
BACKUP_DIR="$APP_DIR/workspace/backups/keyword-maintenance"

sudo install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$APP_DIR/workspace/runtime" "$BACKUP_DIR"

sudo tee /etc/systemd/system/jaguartv-trends-run.service >/dev/null <<EOF
[Unit]
Description=Collect JaguarTV Brazil hot keywords after midnight in Sao Paulo

[Service]
Type=oneshot
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
Environment=LAST30DAYS_MEMORY_DIR=$APP_DIR/workspace/runtime/last30days
Environment=LAST30DAYS_PYTHON=$PYTHON_BIN
ExecStart=$PYTHON_BIN -m jaguartv_factory.cli --config $APP_DIR/config/pipeline.yaml trends-run
EOF

sudo tee /etc/systemd/system/jaguartv-trends-run.timer >/dev/null <<'EOF'
[Unit]
Description=Collect JaguarTV Brazil hot keywords after the Sao Paulo day rolls over

[Timer]
OnCalendar=*-*-* 00:10:00 America/Sao_Paulo
Persistent=true
Unit=jaguartv-trends-run.service

[Install]
WantedBy=timers.target
EOF

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
Description=Import JaguarTV daily keywords after the Sao Paulo midnight discovery job

[Timer]
OnCalendar=*-*-* 00:20:00 America/Sao_Paulo
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
OnCalendar=*-*-* 00:30:00 America/Sao_Paulo
Persistent=true
Unit=jaguartv-carry-forward-tag-keywords.service

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now \
  jaguartv-trends-run.timer \
  jaguartv-import-daily-keywords.timer \
  jaguartv-clear-tag-keywords.timer \
  jaguartv-carry-forward-tag-keywords.timer
