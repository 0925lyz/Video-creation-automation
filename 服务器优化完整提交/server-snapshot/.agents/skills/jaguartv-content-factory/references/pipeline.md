# Pipeline Reference

## Audio Policy

- `audio.source_mode: auto` localizes only when downloaded subtitles or enabled ASR provide speech evidence.
- Speech clips remove source audio and use pt-BR voice/subtitles plus Funk BGM.
- Clips without speech evidence preserve source music and do not generate voice or subtitles.
- Silent clips receive Funk BGM without generated narration/subtitles.
- BGM comes from `assets/bgm/`; if empty, the factory generates an original 150 BPM funk-inspired instrumental. Adjust `audio.bgm_volume` in `config/pipeline.yaml`.

## Burned-in Caption Cleanup

`edit.source_subtitle_cleanup: crop` removes the configured lower portion of the source foreground before vertical placement. The default `source_subtitle_crop_bottom_ratio: 0.18` handles common bottom-positioned Chinese captions. Raise it only when frame inspection shows captions remaining; set the mode to `off` for clean sources.

## States

`DISCOVERED -> DOWNLOADED -> READY_FOR_REVIEW`

Failure states include `LANGUAGE_REJECTED`, `DOWNLOAD_FAILED`, `PRODUCTION_FAILED`, and `QA_FAILED`. Inspect `workspace/factory.db` and `workspace/jobs/<candidate-id>/` before retrying.

## Outputs

- `workspace/factory.db`: shared candidate and event state.
- `workspace/jobs/<id>/source.mp4`: downloaded source.
- `workspace/jobs/<id>/script_ptbr.json`: audio decision plus localized narration when required.
- `workspace/jobs/<id>/subtitles_ptbr.srt`: subtitle timing, only for localized speech clips.
- `workspace/jobs/<id>/master_9x16.mp4`: rendered master.
- `workspace/ready_for_review/<id>/video.mp4`: review-ready video.
- `workspace/ready_for_review/<id>/metadata.json`: Facebook, YouTube, TikTok, and Kwai copy package.
- `workspace/ready_for_review/index.html`: local review page.

## Adapter Behavior

Platform adapters fail independently. Continue healthy platforms when one adapter is degraded. Use `ingest <url>` when search is unavailable but a direct URL is known.

## Local UI

Run `factory.sh ui --host 127.0.0.1 --port 8787`. Use `0.0.0.0` only on a trusted LAN. The current SQLite database must have one writer host; production multi-machine operation should use the central API with PostgreSQL and object storage rather than a shared SQLite file.

## ASR

Set `localization.asr_enabled: true` in `config/pipeline.yaml` after a Whisper model is cached. Keep it false for the no-model demo path, which localizes platform title and description metadata.
