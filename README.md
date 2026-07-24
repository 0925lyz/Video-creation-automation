# JaguarTV Content Factory

Local batch pipeline for discovering non-Portuguese video candidates and producing pt-BR review-ready vertical videos. The final render removes the complete source soundtrack and mixes only pt-BR narration with ducked background music.

## First-time setup

```bash
./scripts/bootstrap.sh
.venv/bin/workbuddy doctor
```

Every team member points the shared skill at this repository:

```bash
export JAGUARTV_FACTORY_ROOT="/Users/allen/Documents/视频二创"
```

## Daily workflow

```bash
# Source scout
.venv/bin/workbuddy discover --platform youtube --limit 3
.venv/bin/workbuddy list --status DISCOVERED

# Localization editor; pass the candidate ID from the previous command
.venv/bin/workbuddy download --candidate <id>
.venv/bin/workbuddy produce --candidate <id>

# Quality reviewer
.venv/bin/workbuddy review
```

The review page is generated at `workspace/ready_for_review/index.html`. Install the portable agent skill with `./scripts/install-agent-skill.sh`.

## Local Content OS UI

Run the standalone dashboard; WorkBuddy is not required:

```bash
.venv/bin/workbuddy ui --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787/`. The dashboard provides real inventory, task controls, publishing queues, performance snapshots, JaguarTV conversion funnels, keyword feedback, and worker-node status. See `本地UI与双机部署方案.md` for the production topology.

The complete operating, platform-support, capability-boundary, optimization, and JaguarTV growth plan is in `JaguarTV内容工厂完整使用与营销增长方案.md`.

## Tencent Cloud / server deployment

For Tencent Cloud Lighthouse deployment, use:

- `scripts/server-install.sh` for first-time server setup.
- `scripts/server-sync.sh` for pulling later GitHub updates and restarting the service.
- `腾讯云轻量服务器部署指南.md` for the full private-repo, Deploy Key, security-group, and systemd workflow.

## BGM library

Put team-approved `.mp3`, `.m4a`, `.aac`, `.wav`, or `.flac` tracks in `assets/bgm/`. A track is selected deterministically for each candidate and automatically ducked under the pt-BR voice. When the directory is empty, the factory generates and caches an original 150 BPM Brazilian funk-inspired demo beat.

To pin one campaign track, set `audio.bgm_path` in `config/pipeline.yaml`. Start around `0.35-0.45` for `audio.bgm_volume`; the side-chain compressor lowers it while narration is active. The source video's audio is never included in the final mix.

## Burned-in source subtitles

The default `edit.source_subtitle_cleanup: crop` removes the lower 18% of the foreground source frame before it is placed on the vertical canvas. This removes typical burned-in Chinese captions without relying on a subtitle track. Adjust `source_subtitle_crop_bottom_ratio` per source template, or set cleanup to `off` for videos without burned-in captions. OCR/inpainting is not used in this fast pipeline, so captions outside the configured lower band require a source-specific crop value.

## Calling from an agent

In WorkBuddy, CodyBuddy, or Codex, invoke the installed `jaguartv-content-factory` skill and state the stage plus any candidate ID. Examples:

- `使用 jaguartv-content-factory，发现 10 条 YouTube 足球候选，只输出候选 ID 和推荐理由。`
- `使用 jaguartv-content-factory，下载并制作候选 53e37cd26b1b965f，确保移除原音轨并使用 Funk BGM。`
- `使用 jaguartv-content-factory，质检候选 53e37cd26b1b965f，并刷新人工审核页。`

Do not let two agents produce the same candidate ID concurrently. Use `workspace/ready_for_review/<id>/` as the handoff boundary.
