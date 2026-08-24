#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON_BIN="${PYTHON_BIN:-$APP_DIR/.venv/bin/python}"
VENV_ROOT="$APP_DIR/workspace/tool_venvs"

cd "$APP_DIR"
mkdir -p "$VENV_ROOT" "$APP_DIR/workspace/runtime/integration-status"

"$PYTHON_BIN" -m jaguartv_factory.cli integrations \
  --name mediacrawler --name scrapling --name f2 --name agent_reach --sync

install_tool() {
  local name="$1"
  local source="$2"
  local env_dir="$VENV_ROOT/$name"
  "$PYTHON_BIN" -m venv "$env_dir"
  "$env_dir/bin/python" -m pip install --upgrade pip setuptools wheel
  "$env_dir/bin/pip" install "$source"
}

install_tool f2 "$APP_DIR/workspace/external_tools/f2"
install_tool scrapling "$APP_DIR/workspace/external_tools/Scrapling[fetchers]"
install_tool agent-reach "$APP_DIR/workspace/external_tools/Agent-Reach"

MEDIACRAWLER_ENV="$VENV_ROOT/mediacrawler"
"$PYTHON_BIN" -m venv "$MEDIACRAWLER_ENV"
"$MEDIACRAWLER_ENV/bin/python" -m pip install --upgrade pip setuptools wheel
"$MEDIACRAWLER_ENV/bin/pip" install -r "$APP_DIR/workspace/external_tools/MediaCrawler/requirements.txt"

"$VENV_ROOT/f2/bin/f2" --help >/dev/null
"$VENV_ROOT/agent-reach/bin/agent-reach" install --env=auto || true
"$VENV_ROOT/agent-reach/bin/agent-reach" doctor --json \
  > "$APP_DIR/workspace/runtime/integration-status/agent-reach-doctor.json" || true
"$VENV_ROOT/scrapling/bin/python" -c 'import scrapling; print(scrapling.__version__)' \
  > "$APP_DIR/workspace/runtime/integration-status/scrapling-version.txt"
(
  cd "$APP_DIR/workspace/external_tools/MediaCrawler"
  "$MEDIACRAWLER_ENV/bin/python" main.py --help >/dev/null
)
"$MEDIACRAWLER_ENV/bin/python" -c 'import playwright; print("cli-and-playwright-ready")' \
  > "$APP_DIR/workspace/runtime/integration-status/mediacrawler-runtime.txt"
