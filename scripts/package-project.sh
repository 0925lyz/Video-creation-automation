#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT="$ROOT/dist/jaguartv-content-os-demo.zip"

cd "$ROOT"
mkdir -p dist
zip -r -FS "$OUTPUT" \
  pyproject.toml README.md AGENTS.md \
  "团队使用指南.md" "本地UI与双机部署方案.md" "JaguarTV内容工厂完整使用与营销增长方案.md" \
  src config scripts assets tests .agents \
  -x '*.DS_Store' '*/__pycache__/*' '*.pyc' 'src/*.egg-info/*'

echo "Packaged: $OUTPUT"
