#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT="$ROOT/dist/jaguartv-content-os-demo.zip"

cd "$ROOT"
mkdir -p dist
zip -r -FS "$OUTPUT" \
  pyproject.toml package.json manifest.json README.md AGENTS.md .gitignore \
  src config scripts assets data tests docs prompts .agents \
  -x '*.DS_Store' '*/__pycache__/*' '*.pyc' 'src/*.egg-info/*' \
     '*/node_modules/*' '*.db-shm' '*.db-wal'

echo "Packaged: $OUTPUT"
