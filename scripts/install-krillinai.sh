#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON_BIN="${PYTHON_BIN:-$APP_DIR/.venv/bin/python}"
KRILLIN_DIR="$APP_DIR/workspace/external_tools/KrillinAI"
KRILLIN_CONFIG="$KRILLIN_DIR/config/config.toml"

if [[ ! -d "$KRILLIN_DIR/.git" ]]; then
  echo "KrillinAI checkout is missing: $KRILLIN_DIR" >&2
  exit 2
fi

if ! command -v go >/dev/null 2>&1; then
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Go 1.22 or newer is required to build KrillinAI." >&2
    exit 2
  fi
  sudo apt-get update
  sudo apt-get install -y golang-go
fi

GO_VERSION="$(go env GOVERSION | sed 's/^go//')"
GO_MAJOR="${GO_VERSION%%.*}"
GO_MINOR="${GO_VERSION#*.}"
GO_MINOR="${GO_MINOR%%.*}"
if (( GO_MAJOR < 1 || (GO_MAJOR == 1 && GO_MINOR < 22) )); then
  echo "KrillinAI requires Go 1.22 or newer; found go$GO_VERSION" >&2
  exit 2
fi

mkdir -p "$KRILLIN_DIR/build"
(
  cd "$KRILLIN_DIR"
  GOPROXY="${KRILLINAI_GOPROXY:-https://proxy.golang.org,direct}" \
    go build -o build/krillinai-cli ./cmd/cli
  ./build/krillinai-cli help >/dev/null
)

if [[ ! -f "$KRILLIN_CONFIG" ]]; then
  "$PYTHON_BIN" - "$APP_DIR/.env" "$KRILLIN_CONFIG" <<'PY'
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
destination = Path(sys.argv[2])
values = dict(os.environ)
if env_path.is_file():
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values.setdefault(key.strip(), value.strip().strip('"').strip("'"))

api_key = (
    values.get("KRILLINAI_API_KEY")
    or values.get("JAGUARTV_PUBLISHING_AI_API_KEY")
    or values.get("OPENAI_API_KEY")
    or ""
)
if not api_key:
    raise SystemExit(
        "KrillinAI config is missing and no KRILLINAI/OpenAI API key is available"
    )

base_url = (
    values.get("KRILLINAI_BASE_URL")
    or values.get("JAGUARTV_OPENAI_BASE_URL")
    or values.get("OPENAI_BASE_URL")
    or ""
)
if base_url.rstrip("/").endswith("/responses"):
    base_url = base_url.rstrip("/")[:-len("/responses")]
llm_model = values.get("KRILLINAI_LLM_MODEL") or values.get("JAGUARTV_PUBLISHING_AI_MODEL") or "gpt-4o-mini"
transcribe_provider = values.get("KRILLINAI_TRANSCRIBE_PROVIDER") or "fasterwhisper"
transcribe_model = values.get("KRILLINAI_TRANSCRIBE_MODEL") or (
    "tiny" if transcribe_provider == "fasterwhisper" else "whisper-1"
)
tts_provider = values.get("KRILLINAI_TTS_PROVIDER") or "edge-tts"
tts_model = values.get("KRILLINAI_TTS_MODEL") or "gpt-4o-mini-tts"

quote = lambda value: json.dumps(str(value), ensure_ascii=False)
content = f'''[app]
segment_duration = 5
transcribe_parallel_num = 1
translate_parallel_num = 3
transcribe_max_attempts = 3
translate_max_attempts = 5
max_sentence_length = 70
target_language_first = true
short_subtitle_max_chars = 20

[server]
host = "127.0.0.1"
port = 8888

[llm]
base_url = {quote(base_url)}
api_key = {quote(api_key)}
model = {quote(llm_model)}
json = false

[transcribe]
provider = {quote(transcribe_provider)}
enable_gpu_acceleration = false

[transcribe.openai]
base_url = {quote(base_url)}
api_key = {quote(api_key)}
model = {quote(transcribe_model)}

[transcribe.fasterwhisper]
model = {quote(transcribe_model)}

[transcribe.whisperkit]
model = "large-v2"

[transcribe.whispercpp]
model = "large-v2"

[tts]
provider = {quote(tts_provider)}

[tts.openai]
base_url = {quote(base_url)}
api_key = {quote(api_key)}
model = {quote(tts_model)}

[tts.minimax]
base_url = ""
api_key = ""
model = ""

[dubbing]
min_subtitle_duration = 2.5
max_chunk_size = 5
gap_tolerance = 1.5
speed_min = 0.95
speed_accept = 1.15
speed_max = 1.30
enable_text_rewrite = true
rewrite_max_attempts = 2
estimator = "statistical"
'''
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(content, encoding="utf-8")
destination.chmod(0o600)
PY
fi

chmod 600 "$KRILLIN_CONFIG"
IFS=$'\t' read -r TRANSCRIBE_PROVIDER TRANSCRIBE_MODEL < <(
  "$PYTHON_BIN" - "$KRILLIN_CONFIG" <<'PY'
import sys
import tomllib
from pathlib import Path

config = tomllib.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
transcribe = config.get("transcribe") or {}
provider = str(transcribe.get("provider") or "")
model = str((transcribe.get("fasterwhisper") or {}).get("model") or "")
print(f"{provider}\t{model}")
PY
)

if [[ "$TRANSCRIBE_PROVIDER" == "fasterwhisper" ]]; then
  if [[ "$TRANSCRIBE_MODEL" != "tiny" ]]; then
    echo "Automatic verified Faster Whisper install currently supports the tiny model only." >&2
    exit 2
  fi
  FASTER_ROOT="$KRILLIN_DIR/bin/faster-whisper/Whisper-Faster-XXL"
  FASTER_BIN="$FASTER_ROOT/whisper-faster-xxl"
  FASTER_REAL="$FASTER_ROOT/whisper-faster-xxl.real"
  FASTER_ARCHIVE="$KRILLIN_DIR/bin/faster-whisper.zip"
  FASTER_URL="https://modelscope.cn/models/Maranello/KrillinAI_dependency_cn/resolve/master/Faster-Whisper-XXL_r192.3.1_linux.zip"
  FASTER_SHA256="f01cf0cb30593e5b47e746dae1b536440f374fd6074fe108b1d407cc7db2f139"
  MODEL_ARCHIVE="$KRILLIN_DIR/models/faster-whisper-tiny.zip"
  MODEL_FILE="$KRILLIN_DIR/models/faster-whisper-tiny/model.bin"
  MODEL_URL="https://modelscope.cn/models/Maranello/KrillinAI_dependency_cn/resolve/master/faster-whisper-tiny.zip"
  MODEL_SHA256="8870b31777025e654120c605b7f4520d91efaf25dddf69221e52a5b43d9c628e"

  if [[ ! -x "$FASTER_REAL" ]]; then
    if [[ ! -x "$FASTER_BIN" ]]; then
      mkdir -p "$KRILLIN_DIR/bin"
      curl -fL --retry 3 --output "$FASTER_ARCHIVE" "$FASTER_URL"
      echo "$FASTER_SHA256  $FASTER_ARCHIVE" | sha256sum --check --status
      unzip -oq "$FASTER_ARCHIVE" -d "$KRILLIN_DIR/bin/faster-whisper"
      chmod 755 "$FASTER_BIN"
    fi
    mv "$FASTER_BIN" "$FASTER_REAL"
  fi
  if [[ ! -s "$MODEL_FILE" ]]; then
    mkdir -p "$KRILLIN_DIR/models"
    curl -fL --retry 3 --output "$MODEL_ARCHIVE" "$MODEL_URL"
    echo "$MODEL_SHA256  $MODEL_ARCHIVE" | sha256sum --check --status
    unzip -oq "$MODEL_ARCHIVE" -d "$KRILLIN_DIR/models/faster-whisper-tiny"
  fi
  install -m 755 "$APP_DIR/scripts/krillin-fasterwhisper-wrapper.sh" "$FASTER_BIN"
fi

EDGE_TTS_VENV="$APP_DIR/workspace/tool_venvs/krillin-edge-tts"
if [[ ! -x "$EDGE_TTS_VENV/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$EDGE_TTS_VENV"
fi
"$EDGE_TTS_VENV/bin/python" -m pip install --disable-pip-version-check "edge-tts==7.2.8"
mkdir -p "$KRILLIN_DIR/bin"
install -m 755 "$APP_DIR/scripts/krillin-edge-tts-wrapper.sh" "$KRILLIN_DIR/bin/edge-tts"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
printf '1\n00:00:00,000 --> 00:00:02,000\nTeste seguro do JaguarTV.\n' > "$TMP_DIR/test.srt"
(
  cd "$KRILLIN_DIR"
  ./build/krillinai-cli tts \
    --input-srt "$TMP_DIR/test.srt" \
    --workdir "$TMP_DIR/tts" \
    --task-id install-check \
    --line-mode target-only \
    --dry-run >/dev/null
)

echo "KrillinAI built and configured at pinned checkout."
