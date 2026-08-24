#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  if command -v npm >/dev/null 2>&1; then
    npm --prefix "$ROOT" ci --no-audit --no-fund
  elif command -v brew >/dev/null 2>&1; then
    brew install ffmpeg
  else
    echo "Missing ffmpeg/ffprobe and neither brew nor npm is available." >&2
    exit 1
  fi
fi
if ! command -v ffmpeg >/dev/null 2>&1 && [[ ! -x "$ROOT/node_modules/ffmpeg-static/ffmpeg" ]]; then
  echo "ffmpeg is still missing after dependency installation." >&2
  exit 1
fi
if ! command -v ffprobe >/dev/null 2>&1 && [[ ! -x "$ROOT/node_modules/ffprobe-static/bin/darwin/arm64/ffprobe" && ! -x "$ROOT/node_modules/ffprobe-static/bin/darwin/x64/ffprobe" && ! -x "$ROOT/node_modules/ffprobe-static/bin/linux/x64/ffprobe" ]]; then
  echo "ffprobe is still missing after dependency installation." >&2
  exit 1
fi
if ! command -v tesseract >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    brew install tesseract tesseract-lang
  else
    echo "Warning: tesseract is missing; OCR cleanup will be degraded until it is installed." >&2
  fi
fi

if command -v uv >/dev/null 2>&1; then
  uv python install 3.12
  uv venv --clear --python 3.12 "$ROOT/.venv"
  uv pip install --python "$ROOT/.venv/bin/python" -e "$ROOT[test]"
else
  PYTHON_BIN="${PYTHON_BIN:-python3.12}"
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "Missing uv and $PYTHON_BIN. Install Python 3.12 or uv, then rerun bootstrap." >&2
    exit 1
  fi
  "$PYTHON_BIN" -m venv --clear "$ROOT/.venv"
  "$ROOT/.venv/bin/python" -m pip install --upgrade pip
  "$ROOT/.venv/bin/python" -m pip install -e "$ROOT[test]"
fi

echo "Ready: $ROOT/.venv/bin/jaguartv doctor"
