#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="$ROOT/src/jaguartv_factory/remotion_template"
OUT_DIR="$ROOT/workspace/remotion_smoke"
PAYLOAD="$OUT_DIR/payload.json"
OUTPUT="$OUT_DIR/out.mp4"

mkdir -p "$OUT_DIR"

node - "$PAYLOAD" "$OUTPUT" <<'NODE'
const fs = require("node:fs");
const path = require("node:path");

const payloadPath = process.argv[2];
const outputPath = process.argv[3];
const payload = {
  entryPoint: "src/index.tsx",
  compositionId: "JaguarTVVariant",
  outputLocation: path.resolve(outputPath),
  timeoutMs: 120000,
  props: {
    variant: "FB版",
    sourceVideo: "",
    width: 320,
    height: 240,
    fps: 30,
    contentSeconds: 1,
    promoSeconds: 0,
    durationSeconds: 1,
    overlayMaxWidthRatio: 0.18,
    overlayMarginHRatio: 0.03,
    overlayMarginVRatio: 0.05,
    sourceFit: "cover",
    endcardFit: "cover",
    overlayPlacement: "video_corners",
    sourceAspectRatio: 4 / 3
  }
};
fs.writeFileSync(payloadPath, JSON.stringify(payload, null, 2));
NODE

node "$RUNTIME/scripts/render.mjs" "$PAYLOAD"
test -s "$OUTPUT"
echo "Remotion smoke output: $OUTPUT"
