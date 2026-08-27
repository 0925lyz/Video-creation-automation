#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(realpath "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
APP_DIR="${JAGUARTV_APP_DIR:-$(cd "$SCRIPT_DIR/../../../.." && pwd)}"
EDGE_TTS_BIN="${KRILLIN_EDGE_TTS_BIN:-$APP_DIR/workspace/tool_venvs/krillin-edge-tts/bin/edge-tts}"
FFMPEG_BIN="${KRILLIN_FFMPEG_BIN:-$(command -v ffmpeg || true)}"

TEXT_FILE=""
VOICE=""
OUTPUT=""
SAMPLE_RATE="44100"

while (($#)); do
  case "$1" in
    --text-file)
      TEXT_FILE="${2:-}"
      shift 2
      ;;
    --voice)
      VOICE="${2:-}"
      shift 2
      ;;
    --output)
      OUTPUT="${2:-}"
      shift 2
      ;;
    --format)
      shift 2
      ;;
    --sample_rate)
      SAMPLE_RATE="${2:-}"
      shift 2
      ;;
    *)
      echo "Unsupported KrillinAI edge-tts argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "$TEXT_FILE" || -z "$OUTPUT" ]]; then
  echo "Both --text-file and --output are required" >&2
  exit 2
fi
if [[ ! "$SAMPLE_RATE" =~ ^[0-9]+$ ]] || ((SAMPLE_RATE < 8000 || SAMPLE_RATE > 192000)); then
  echo "Invalid sample rate" >&2
  exit 2
fi
if [[ ! -x "$EDGE_TTS_BIN" ]]; then
  echo "Official edge-tts executable is unavailable" >&2
  exit 2
fi
if [[ -z "$FFMPEG_BIN" || ! -x "$FFMPEG_BIN" ]]; then
  echo "ffmpeg executable is unavailable" >&2
  exit 2
fi

VOICE="${VOICE:-${KRILLIN_EDGE_TTS_DEFAULT_VOICE:-pt-BR-AntonioNeural}}"
TEMP_AUDIO="$(mktemp "${TMPDIR:-/tmp}/krillin-edge-tts.XXXXXX.mp3")"
trap 'rm -f "$TEMP_AUDIO"' EXIT

mkdir -p "$(dirname "$OUTPUT")"
"$EDGE_TTS_BIN" --file "$TEXT_FILE" --voice "$VOICE" --write-media "$TEMP_AUDIO"
"$FFMPEG_BIN" -hide_banner -loglevel error -y -i "$TEMP_AUDIO" \
  -ar "$SAMPLE_RATE" -ac 1 -c:a pcm_s16le "$OUTPUT"
test -s "$OUTPUT"
