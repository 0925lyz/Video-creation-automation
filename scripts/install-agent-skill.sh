#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="$ROOT/.agents/skills/jaguartv-content-factory"

install_skill() {
  local base="$1"
  local target="$base/jaguartv-content-factory"
  mkdir -p "$base"
  if [[ -d "$target" ]]; then
    cp -R "$SOURCE/." "$target/"
  else
    cp -R "$SOURCE" "$target"
  fi
  echo "Installed: $target"
}

install_skill "$HOME/.codex/skills"

echo "Set this in each agent environment:"
echo "export JAGUARTV_FACTORY_ROOT='$ROOT'"
