#!/usr/bin/env bash
set -euo pipefail

SERVER_HOST="${JAGUARTV_SERVER_HOST:-43.134.128.197}"
SERVER_USER="${JAGUARTV_SERVER_USER:-ubuntu}"
SERVER_KEY="${JAGUARTV_SERVER_KEY:-$HOME/.ssh/jarg_tencent.pem}"
SERVER_DIR="${JAGUARTV_SERVER_DIR:-/opt/jaguartv-content-factory-vnext}"
CONFIG_PATH="${JAGUARTV_CONFIG:-config/pipeline.yaml}"
PRODUCE_CANDIDATES="${JAGUARTV_PRODUCE_CANDIDATES:-}"
RIGHTS_STATUS="${JAGUARTV_RIGHTS_STATUS:-MANUAL_REVIEW}"

if [[ ! -f "$SERVER_KEY" ]]; then
  echo "Missing server SSH key: $SERVER_KEY" >&2
  echo "Place the Tencent key at ~/.ssh/jarg_tencent.pem or set JAGUARTV_SERVER_KEY." >&2
  exit 2
fi

chmod 600 "$SERVER_KEY"

tar -czf - \
  config/pipeline.yaml \
  config/keywords.brazil.yaml \
  scripts/server-crawl.sh \
  src/jaguartv_factory/core.py \
  workspace/factory.db |
  ssh \
    -i "$SERVER_KEY" \
    -o IdentitiesOnly=yes \
    -o BatchMode=yes \
    "${SERVER_USER}@${SERVER_HOST}" \
    "set -euo pipefail
     cd $(printf '%q' "$SERVER_DIR")
     mkdir -p workspace
     tar -xzf -"

ssh \
  -i "$SERVER_KEY" \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  "${SERVER_USER}@${SERVER_HOST}" \
  "set -euo pipefail
   cd $(printf '%q' "$SERVER_DIR")
   .venv/bin/jaguartv doctor || true
   .venv/bin/jaguartv --config $(printf '%q' "$CONFIG_PATH") list --status DISCOVERED --limit 20"

if [[ -n "$PRODUCE_CANDIDATES" ]]; then
  for candidate in $PRODUCE_CANDIDATES; do
    ssh \
      -i "$SERVER_KEY" \
      -o IdentitiesOnly=yes \
      -o BatchMode=yes \
      "${SERVER_USER}@${SERVER_HOST}" \
      "set -euo pipefail
       cd $(printf '%q' "$SERVER_DIR")
       .venv/bin/jaguartv --config $(printf '%q' "$CONFIG_PATH") download --candidate $(printf '%q' "$candidate")
       .venv/bin/jaguartv --config $(printf '%q' "$CONFIG_PATH") produce --candidate $(printf '%q' "$candidate") --rights-status $(printf '%q' "$RIGHTS_STATUS")"
  done
  ssh \
    -i "$SERVER_KEY" \
    -o IdentitiesOnly=yes \
    -o BatchMode=yes \
    "${SERVER_USER}@${SERVER_HOST}" \
    "set -euo pipefail
     cd $(printf '%q' "$SERVER_DIR")
     .venv/bin/jaguartv --config $(printf '%q' "$CONFIG_PATH") review"
fi
