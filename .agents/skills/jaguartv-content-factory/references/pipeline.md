# Pipeline Reference

## Source And Localization Policy

- Candidate discovery requires a known duration at or below 900 seconds.
- Download refuses unknown or longer metadata and removes a downloaded file if `ffprobe` reports more than 900 seconds.
- The original frame is preserved. There is no promotional-tail trim, OCR pass, caption crop, or subtitle blur.
- KrillinAI performs transcription, pt-BR translation, line timing, and TTS. Its configured providers may be changed without modifying factory code.
- A task can override the TTS voice with `--krillinai-voice`; an empty Edge voice uses `KRILLIN_EDGE_TTS_DEFAULT_VOICE`, while other providers use their configured default.
- Any transcription, translation, Portuguese language-gate, or TTS failure stops production. There is no generic script or system-voice fallback.
- Remotion places at most two compact subtitle lines inside the source-frame safe area and produces one generic review video with the configured bottom banner and one endcard. Subtitle positions do not follow OCR detections.

## States

`DISCOVERED -> DOWNLOADED -> READY_FOR_REVIEW`

Failure states include `LANGUAGE_REJECTED`, `DOWNLOAD_FAILED`, `PRODUCTION_FAILED`, and `QA_FAILED`. Inspect `workspace/factory.db` and `workspace/jobs/<candidate-id>/` before retrying.

## Outputs

- `workspace/factory.db`: shared candidate and event state.
- `workspace/jobs/<id>/source.mp4`: downloaded source.
- `workspace/jobs/<id>/source.krillinai.srt`: KrillinAI source-language transcription.
- `workspace/jobs/<id>/subtitles_ptbr.srt`: KrillinAI pt-BR subtitle timing.
- `workspace/jobs/<id>/krillinai/`: per-stage manifests, stdout/stderr logs, and per-segment TTS audio.
- `workspace/jobs/<id>/master_9x16.mp4`: rendered master.
- `workspace/ready_for_review/<id>/video.mp4`: review-ready video.
- `workspace/ready_for_review/<id>/metadata.json`: Facebook, YouTube, TikTok, and Kwai copy package.
- `workspace/ready_for_review/index.html`: local review page.

## Adapter Behavior

Platform adapters fail independently. Continue healthy platforms when one adapter is degraded. Use `ingest <url>` when search is unavailable but a direct URL is known.

## Local UI

Run `factory.sh ui --host 127.0.0.1 --port 8787`. Use `0.0.0.0` only on a trusted LAN. The current SQLite database must have one writer host; production multi-machine operation should use the central API with PostgreSQL and object storage rather than a shared SQLite file.

## KrillinAI Providers

The ignored `workspace/external_tools/KrillinAI/config/config.toml` selects transcription, OpenAI-compatible translation, and TTS providers. The server defaults to KrillinAI's local `fasterwhisper/tiny` transcription. The default Edge provider runs through KrillinAI with the pinned official `edge-tts` package and a compatibility wrapper; OpenAI, Aliyun, and MiniMax remain selectable in the private config. Run `scripts/install-krillinai.sh` after synchronization. `doctor` reports the factory unavailable when the pinned binary or private config is missing.
