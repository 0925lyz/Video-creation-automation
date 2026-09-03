#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="$ROOT/src/jaguartv_factory/remotion_template"
OUT_DIR="$ROOT/workspace/remotion_smoke"
PUBLIC_SMOKE="$RUNTIME/public/smoke"

cleanup() {
  unlink "$PUBLIC_SMOKE/brand-banner.jpg" 2>/dev/null || true
}
trap cleanup EXIT

if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="$PYTHON"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi
"$PYTHON_BIN" -c "from PIL import Image"

mkdir -p "$OUT_DIR" "$PUBLIC_SMOKE"
ffmpeg -y -v error \
  -f lavfi -i "testsrc2=size=640x360:rate=30:duration=1" \
  -f lavfi -i "sine=frequency=880:sample_rate=48000:duration=1" \
  -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac "$PUBLIC_SMOKE/source.mp4"
ffmpeg -y -v error \
  -f lavfi -i "testsrc2=size=360x640:rate=30:duration=1" \
  -f lavfi -i "sine=frequency=660:sample_rate=48000:duration=1" \
  -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac "$PUBLIC_SMOKE/source-portrait.mp4"
cp "$ROOT/assets/brand/dashboard_favicon.png" "$PUBLIC_SMOKE/cta.png"
cp "$ROOT/assets/brand/jaguartv_download_banner.jpg" "$PUBLIC_SMOKE/brand-banner.jpg"

render_generic() {
  local payload="$OUT_DIR/通用版.json"
  local output="$OUT_DIR/通用版.mp4"
  node - "$payload" "$output" <<'NODE'
const fs = require("node:fs");
const path = require("node:path");

const payloadPath = process.argv[2];
const outputPath = process.argv[3];
const payload = {
  entryPoint: "src/index.tsx",
  compositionId: "JaguarTVGeneric",
  outputLocation: path.resolve(outputPath),
  timeoutMs: 120000,
  props: {
    sourceVideo: "smoke/source.mp4",
    ctaSrc: "smoke/cta.png",
    ctaType: "image",
    brandBannerSrc: "smoke/brand-banner.jpg",
    brandBannerAspectRatio: 928 / 129,
    width: 360,
    height: 640,
    fps: 30,
    contentSeconds: 1,
    ctaSeconds: 2,
    durationSeconds: 3,
    overlayMaxWidthRatio: 0.18,
    overlayMarginHRatio: 0.03,
    overlayMarginVRatio: 0.05,
    sourceFit: "contain",
    overlayPlacement: "none",
    sourceAspectRatio: 16 / 9,
    captionAvoidRegions: [[0.15, 0.70, 0.85, 0.82]],
    captions: [{startSeconds: 0.05, endSeconds: 0.95, text: "Um golaço mudou o jogo."}],
    captionStyle: {
      position: "bottom",
      maxWidthRatio: 0.90,
      fontSizeRatio: 0.028,
      safeInsetRatio: 0.05,
      backgroundOpacity: 0,
      maxLines: 2,
      textColor: "#ffffff",
      backgroundColor: "#050505"
    }
  }
};
fs.writeFileSync(payloadPath, JSON.stringify(payload, null, 2));
NODE

  node "$RUNTIME/scripts/render.mjs" "$payload"
  test -s "$output"
}

render_generic

sed \
  -e 's#smoke/source.mp4#smoke/source-portrait.mp4#' \
  "$OUT_DIR/通用版.json" > "$OUT_DIR/竖屏通用版.json"
"$PYTHON_BIN" - "$OUT_DIR/竖屏通用版.json" <<'PY'
from pathlib import Path
import json
import sys

path = Path(sys.argv[1])
payload = json.loads(path.read_text())
payload["outputLocation"] = str(path.with_suffix(".mp4").resolve())
payload["props"]["sourceAspectRatio"] = 9 / 16
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
PY
node "$RUNTIME/scripts/render.mjs" "$OUT_DIR/竖屏通用版.json"

for output in "$OUT_DIR/通用版.mp4" "$OUT_DIR/竖屏通用版.mp4"; do
  video_probe="$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_type,width,height,r_frame_rate -of json "$output")"
  audio_probe="$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_type -of csv=p=0 "$output")"
  grep -q '"width": 360' <<<"$video_probe"
  grep -q audio <<<"$audio_probe"
done

ffmpeg -y -v error -ss 0.2 -i "$OUT_DIR/通用版.mp4" -frames:v 1 "$OUT_DIR/generic-start.png"
ffmpeg -y -v error -ss 0.2 -i "$OUT_DIR/竖屏通用版.mp4" -frames:v 1 "$OUT_DIR/generic-portrait-start.png"
ffmpeg -y -v error -ss 0.8 -i "$OUT_DIR/通用版.mp4" -frames:v 1 "$OUT_DIR/generic-content-end.png"
ffmpeg -y -v error -ss 1.3 -i "$OUT_DIR/通用版.mp4" -frames:v 1 "$OUT_DIR/generic-cta.png"

"$PYTHON_BIN" - "$OUT_DIR" <<'PY'
from pathlib import Path
import sys
from PIL import Image

root = Path(sys.argv[1])

def nonblack_ratio(path: Path, box=None) -> float:
    image = Image.open(path).convert("RGB")
    if box:
        image = image.crop(box)
    pixels = list(image.getdata())
    return sum(1 for red, green, blue in pixels if max(red, green, blue) > 24) / max(1, len(pixels))

generic = root / "generic-start.png"
# The 16:9 source is centered in the 9:16 canvas during the content sequence.
assert nonblack_ratio(generic, (0, 219, 360, 421)) > 0.55, "generic source frame is blank"
assert nonblack_ratio(generic, (0, 168, 360, 219)) > 0.18, "brand banner is missing above source"
assert nonblack_ratio(generic, (0, 0, 360, 150)) < 0.08, "unexpected generic top-corner overlay"
assert nonblack_ratio(root / "generic-portrait-start.png", (0, 0, 360, 50)) > 0.18, "portrait brand banner is missing at the top"
assert nonblack_ratio(root / "generic-content-end.png", (0, 219, 360, 421)) > 0.55, "content did not persist"
assert nonblack_ratio(root / "generic-cta.png") > 0.45, "generic CTA is blank"
PY

echo "Remotion generic smoke output: $OUT_DIR/通用版.mp4"
