#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="$ROOT/src/jaguartv_factory/remotion_template"
OUT_DIR="$ROOT/workspace/remotion_smoke"
PUBLIC_SMOKE="$RUNTIME/public/smoke"

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
cp "$ROOT/assets/brand/generic_bottom_banner.jpg" "$PUBLIC_SMOKE/banner.jpg"
cp "$ROOT/assets/brand/endcard_landscape_blue_v2.png" "$PUBLIC_SMOKE/endcard.png"

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
    imgBottomBanner: "smoke/banner.jpg",
    imgEndcard: "smoke/endcard.png",
    bottomBannerAspectRatio: 992 / 136,
    width: 360,
    height: 640,
    fps: 30,
    contentSeconds: 1,
    promoSeconds: 1.5,
    durationSeconds: 2.5,
    overlayMaxWidthRatio: 0.18,
    overlayMarginHRatio: 0.03,
    overlayMarginVRatio: 0.05,
    sourceFit: "cover",
    endcardFit: "contain",
    overlayPlacement: "none",
    sourceAspectRatio: 16 / 9,
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

for output in "$OUT_DIR/通用版.mp4"; do
  video_probe="$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_type,width,height,r_frame_rate -of json "$output")"
  audio_probe="$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_type -of csv=p=0 "$output")"
  grep -q '"width": 360' <<<"$video_probe"
  grep -q audio <<<"$audio_probe"
done

ffmpeg -y -v error -ss 0.2 -i "$OUT_DIR/通用版.mp4" -frames:v 1 "$OUT_DIR/generic-start.png"
ffmpeg -y -v error -ss 0.8 -i "$OUT_DIR/通用版.mp4" -frames:v 1 "$OUT_DIR/generic-content-end.png"
ffmpeg -y -v error -ss 1.3 -i "$OUT_DIR/通用版.mp4" -frames:v 1 "$OUT_DIR/generic-endcard.png"

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
# The 16:9 source occupies y=194..396; the banner begins immediately below it.
assert nonblack_ratio(generic, (0, 194, 360, 397)) > 0.55, "generic source frame is blank"
assert nonblack_ratio(generic, (0, 397, 360, 447)) > 0.20, "bottom banner is missing or detached"
assert nonblack_ratio(generic, (0, 0, 360, 170)) < 0.08, "unexpected generic top-corner overlay"
assert nonblack_ratio(root / "generic-content-end.png", (0, 397, 360, 447)) > 0.20, "banner did not persist"
assert nonblack_ratio(root / "generic-endcard.png") > 0.10, "generic endcard is blank"
PY

echo "Remotion generic smoke output: $OUT_DIR/通用版.mp4"
