#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-/opt/homebrew/bin/python3.12}"

if ! command -v ffmpeg >/dev/null 2>&1; then
  brew install ffmpeg
fi
if [[ ! -x "$PYTHON" ]]; then
  brew install python@3.12
fi

"$PYTHON" -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install --upgrade pip
"$ROOT/.venv/bin/pip" install -e "$ROOT[test]"

echo "Ready: $ROOT/.venv/bin/workbuddy doctor"
