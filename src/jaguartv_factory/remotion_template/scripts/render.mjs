import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {bundle} from "@remotion/bundler";
import {makeCancelSignal, renderMedia, selectComposition} from "@remotion/renderer";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const runtimeDir = path.resolve(scriptDir, "..");

const emit = (payload) => {
  process.stdout.write(`${JSON.stringify({...payload, at: new Date().toISOString()})}\n`);
};

const readJson = (file) => JSON.parse(fs.readFileSync(file, "utf8"));

const payloadPath = process.argv[2];
if (!payloadPath) {
  emit({event: "error", message: "Missing renderer payload path"});
  process.exit(2);
}

const payload = readJson(payloadPath);
const props = payload.props || {};
const outputLocation = path.resolve(String(payload.outputLocation || ""));
const entryPoint = path.resolve(runtimeDir, String(payload.entryPoint || "src/index.tsx"));
const publicDir = path.resolve(runtimeDir, "public");
const compositionId = String(payload.compositionId || "JaguarTVGeneric");
const timeoutInMilliseconds = Math.max(30_000, Number(payload.timeoutMs || 360_000));
const browserExecutable = String(process.env.REMOTION_BROWSER_EXECUTABLE || payload.browserExecutable || "").trim() || undefined;
const {cancelSignal, cancel} = makeCancelSignal();

let cancelTimer = null;
if (payload.cancelFile) {
  const cancelFile = path.resolve(String(payload.cancelFile));
  cancelTimer = setInterval(() => {
    if (fs.existsSync(cancelFile)) {
      emit({event: "cancel_requested", cancelFile});
      cancel();
    }
  }, 500);
}

try {
  emit({event: "bundle_started"});
  const serveUrl = await bundle({
    entryPoint,
    publicDir,
    onProgress: (progress) => emit({event: "bundle_progress", progress}),
  });
  emit({event: "bundle_completed", serveUrl});

  const composition = await selectComposition({
    serveUrl,
    id: compositionId,
    inputProps: props,
    logLevel: "error",
    timeoutInMilliseconds,
    browserExecutable,
  });
  emit({
    event: "composition_selected",
    width: composition.width,
    height: composition.height,
    fps: composition.fps,
    durationInFrames: composition.durationInFrames,
  });

  await renderMedia({
    serveUrl,
    composition,
    inputProps: props,
    codec: payload.codec || "h264",
    outputLocation,
    overwrite: true,
    pixelFormat: payload.pixelFormat || "yuv420p",
    x264Preset: payload.x264Preset || "veryfast",
    crf: Number.isFinite(Number(payload.crf)) ? Number(payload.crf) : 22,
    logLevel: "error",
    timeoutInMilliseconds,
    browserExecutable,
    cancelSignal,
    onProgress: (progress) => emit({event: "render_progress", ...progress}),
  });
  const stats = fs.statSync(outputLocation);
  emit({event: "completed", outputLocation, size: stats.size});
} catch (error) {
  emit({
    event: "error",
    name: error?.name || "Error",
    message: error?.message || String(error),
    stack: error?.stack || "",
  });
  process.exit(1);
} finally {
  if (cancelTimer) {
    clearInterval(cancelTimer);
  }
}
