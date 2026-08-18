#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JAGUARTV_BIN="$ROOT/.venv/bin/jaguartv"

if [[ ! -x "$JAGUARTV_BIN" ]]; then
  echo "Runtime missing. Run: $ROOT/scripts/bootstrap.sh" >&2
  exit 3
fi

exec "$JAGUARTV_BIN" \
  --config "$ROOT/config/pipeline.yaml" \
  integrations \
  --manifest "$ROOT/config/integrations.yaml" \
  --sync \
  "$@"
