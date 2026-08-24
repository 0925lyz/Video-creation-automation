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

if [[ ! -x "$ROOT/.venv/bin/jaguartv" ]]; then
  if [[ "${1:-}" == "doctor" ]] && command -v python3 >/dev/null 2>&1; then
    cd "$ROOT"
    PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
      python3 -m jaguartv_factory.cli --config "$ROOT/config/pipeline.yaml" doctor
    exit $?
  fi
  echo "Runtime missing. Run explicitly: $ROOT/scripts/bootstrap.sh" >&2
  exit 3
fi

if [[ "${1:-}" == "produce" ]]; then
  for argument in "$@"; do
    if [[ "$argument" == "--trigger-source" ]]; then
      exec "$ROOT/.venv/bin/jaguartv" --config "$ROOT/config/pipeline.yaml" "$@"
    fi
  done
  exec "$ROOT/.venv/bin/jaguartv" --config "$ROOT/config/pipeline.yaml" "$@" --trigger-source ai_agent
fi

exec "$ROOT/.venv/bin/jaguartv" --config "$ROOT/config/pipeline.yaml" "$@"
