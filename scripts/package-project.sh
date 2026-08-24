#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT="$ROOT/dist/jaguartv-content-os-demo.zip"

cd "$ROOT"
mkdir -p dist
zip -r -FS "$OUTPUT" \
  pyproject.toml uv.lock package.json package-lock.json README.md AGENTS.md \
  docs \
  src config scripts assets tests .agents \
  -x '*.DS_Store' '*/__pycache__/*' '*.pyc' 'src/*.egg-info/*'

echo "Packaged: $OUTPUT"
