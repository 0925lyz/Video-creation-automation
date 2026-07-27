#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if ! command -v ffmpeg >/dev/null 2>&1; then
  brew install ffmpeg
fi
if ! command -v tesseract >/dev/null 2>&1; then
  brew install tesseract tesseract-lang
fi
if ! command -v uv >/dev/null 2>&1; then
  brew install uv
fi

uv python install 3.12
uv venv --clear --python 3.12 "$ROOT/.venv"
uv pip install --python "$ROOT/.venv/bin/python" -e "$ROOT[test]"

echo "Ready: $ROOT/.venv/bin/jaguartv doctor"
