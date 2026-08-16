#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="${JAGUARTV_FACTORY_ROOT:-}"

if [[ -z "$ROOT" ]]; then
  PROJECT_CANDIDATE="$(cd "$SCRIPT_DIR/../../../.." 2>/dev/null && pwd || true)"
  if [[ -f "$PROJECT_CANDIDATE/pyproject.toml" ]]; then
    ROOT="$PROJECT_CANDIDATE"
  elif [[ -f "$PWD/pyproject.toml" ]]; then
    ROOT="$PWD"
  else
    echo "Set JAGUARTV_FACTORY_ROOT to the shared project directory." >&2
    exit 2
  fi
fi

if [[ ! -x "$ROOT/.venv/bin/workbuddy" ]]; then
  echo "Runtime missing. Run: $ROOT/scripts/bootstrap.sh" >&2
  exit 3
fi

exec "$ROOT/.venv/bin/workbuddy" --config "$ROOT/config/pipeline.yaml" "$@"
