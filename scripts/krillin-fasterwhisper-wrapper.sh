#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAL_BIN="${KRILLIN_FASTERWHISPER_REAL_BIN:-$SCRIPT_DIR/whisper-faster-xxl.real}"

if [[ ! -x "$REAL_BIN" ]]; then
  echo "KrillinAI Faster Whisper executable is unavailable" >&2
  exit 2
fi

ARGS=()
while (($#)); do
  if [[ "$1" == "--language" && "${2:-}" == "auto" ]]; then
    shift 2
    continue
  fi
  ARGS+=("$1")
  shift
done

exec "$REAL_BIN" "${ARGS[@]}"
