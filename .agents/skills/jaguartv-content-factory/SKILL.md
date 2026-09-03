---
name: jaguartv-content-factory
description: Run and inspect the JaguarTV local video factory for batch discovery, platform video download, non-Portuguese filtering, pt-BR localization, vertical rendering, QA, and review-package generation. Use when asked to crawl video candidates, produce Brazilian Portuguese short videos, inspect pipeline status, retry media jobs, or prepare content for human review.
---

# JaguarTV Content Factory

Operate the shared project through `scripts/factory.sh`. Keep source discovery, downloads, renders, metadata, and review state in the project workspace; do not create a second task database inside the skill directory.

## Workflow

1. Run `scripts/factory.sh doctor` before a new environment or after dependency changes.
   Use `scripts/factory.sh ytdlp-status` when diagnosing extractor, JS runtime, or browser-impersonation support.
   Use `scripts/factory.sh f2-status` to inspect f2 version and separate Douyin/TikTok module health.
2. Run `scripts/factory.sh discover --platform youtube --limit 3` to collect candidates. Add other implemented adapters only after `doctor` confirms availability.
3. Inspect candidates with `scripts/factory.sh list --status DISCOVERED --limit 20`.
4. Download a selected candidate with `scripts/factory.sh download --candidate <id>`.
5. Produce a review package with `scripts/factory.sh produce --candidate <id>`.
6. Generate the review index with `scripts/factory.sh review`.
7. Treat `workspace/ready_for_review/<id>/` as the handoff boundary for human review.
8. Run `scripts/factory.sh ui --host 127.0.0.1 --port 8787` for the standalone inventory, publishing, analytics, and worker dashboard. This UI does not depend on WorkBuddy.

Discovery accepts only sources with a known duration of 15 minutes or less. Download validates the metadata again and probes the resulting file; unknown or longer media is rejected before production. Production analyzes the original source without promotional-tail detection, cropping, or subtitle blur. Tesseract OCR only reports existing caption regions so new pt-BR captions can avoid them. `edit.segment_overlap_sec` is the maximum permitted overlap between selected highlight windows and is applied by both analysis entry points. Every selected slice is hard-capped at 30 seconds across config, CLI, Dashboard, and analysis callers.

Remotion produces exactly one `通用版` per selected slice. During source content it always renders `assets/brand/jaguartv_download_banner.jpg` at the source video's top edge; the banner is absent from the CTA sequence. It then randomly appends one active CTA whose orientation matches the source. CTA videos play for their full duration; CTA images display for two seconds. The review gate requires both the fixed brand-banner record and CTA record, and checks black, green, corrupted/glitch, and frozen content frames. Do not request, render, expose, or publish an FB-specific variant; old FB files are historical records only.

CTA assets are a separate import-only inventory. Crawlers and candidate imports cannot write to it. Dashboard operators may import, preview, or delete CTA assets. Imports receive a shared orientation sequence (`横版1`, `横版2`, or `竖版1`, `竖版2`) across images and videos while retaining the original upload name as provenance. Manual middle-cut editing and freeform text/image design are available only while an output is `READY_FOR_REVIEW`; both replace the existing generic output in place. Design layers are limited to the content sequence and are never rendered over the CTA.

Dashboard URL imports and completed-video uploads require an explicit source category. The shared `素材` category is available in both the factory discovery inventory and the imported-video inventory; the selected category is persisted in import, candidate, review, copywriting, and publishing metadata rather than inferred from the title.

Dashboard uploads may target either the `pending_production` inventory (so a file can be re-edited into a new generic render) or `approved` (for an already-finished asset); no administrator flag is required to choose either target for an uploaded file. URL imports that target `approved` still require an operator with direct-approval permission. Publishing copy generation accepts an optional operator `hint`. When that hint is present it is treated as the primary factual basis, so imported videos with sparse provenance can still produce title/description/tags. All copy generation first calls `gpt-5.2`; when GPT is not callable, fails, or returns invalid copy JSON, the same prompt is retried with `deepseek-v4-flash`. If both models fail, publishing falls back to deterministic provenance rules rather than blocking task creation.

The default localization path delegates source transcription, pt-BR sentence translation, subtitle timing, and per-segment TTS to the pinned `krillinai/KrillinAI` CLI. KrillinAI provider selection lives in its ignored `config/config.toml`; the server default is local `fasterwhisper/tiny`. With no explicit override, each production segment automatically selects a stable male or female pt-BR voice from `voice_pool`; retries keep the same voice. A production request may still override it with `--krillinai-voice`. When `responses_bridge` is enabled, the adapter starts an ephemeral localhost-only compatibility bridge so KrillinAI can use the server's existing Responses API without modifying the pinned upstream checkout. The installed Edge provider is invoked only through KrillinAI and uses the pinned official `edge-tts` package. When the source transcript contains Chinese and Tesseract also detects Chinese screen captions, Demucs removes the vocal stem and preserves its `no_vocals` backing track under the pt-BR voice. Detection or separation failures stop that Chinese production path. Never invent generic narration, reuse untranslated text, or fall back to pyvideotrans, system voices, or metadata-derived scripts.

## Coordination

- Let the source scout own discovery and candidate recommendations.
- Let the localization editor own `produce` and pt-BR script quality.
- Let the quality reviewer inspect video dimensions, duration, the recorded audio mode/reason, captions, voice/BGM balance, branding, and metadata.
- Share candidate IDs and exact output paths between agents.
- Do not run two production commands for the same candidate concurrently.
- Preserve failed events and intermediate assets for diagnosis.

## Agent Skill Routing

This skill is the only factory entry point. The skills below provide agent guidance; they do not replace the Python pipeline, create another database, or prove that a production service called them.

- Use `content-strategy` for topic lanes and campaign planning.
- Use `jaguartv-copywriter`, `copywriting`, and `copy-editing` for pt-BR titles, captions, tags, and Chinese review copy.
- Use `captions-overlay` and `embedded-captions` when planning subtitle layout; the actual render still runs through Remotion.
- Use `motion-doctrine` and `motion-graphics` for edit decisions that are then expressed through the factory production options and Remotion template.
- Use `talking-head-recut` only for footage that actually contains a speaking presenter.
- Use `media-use` for audio and media handling guidance; actual files remain under the candidate workspace.
- Use `social` for channel-specific positioning after the candidate has passed review.
- Use `analytics` and `attribution` when interpreting publication metrics and feedback proposals.

Do not route to removed Hyperframes, WorkBuddy, SEO, sales, email, paywall, or unrelated marketing skills. Hyperframes was never a production renderer in this repository; Remotion is the single supported render engine.

Check pinned external repositories with `scripts/factory.sh integrations`. MediaCrawler and Agent Reach feed normalized discovery records; `yt-dlp` performs YouTube/TikTok/Facebook/X/Instagram/Kwai downloads, `f2` is the exclusive Douyin downloader, and KrillinAI is the required localization subprocess. Synchronization is explicit and may access the network: `scripts/factory.sh integrations --sync --name krillinai`.

The complete pinned f2 CLI is installed editable in `workspace/tool_venvs/f2`, so its implemented profile, collection, playlist, live, and single-item modes remain available for an explicit operator task. Factory candidate downloads still go through `download`; never call f2 directly and then register its output manually. Managed Douyin login sessions are converted to a temporary mode-0600 f2 config and removed after the subprocess exits. TikTok remains on yt-dlp unless `f2-status` reports its TikTok module healthy and a future tested adapter explicitly enables it.

## Environment

Set `JAGUARTV_FACTORY_ROOT` when the skill is installed outside the repository:

```bash
export JAGUARTV_FACTORY_ROOT="/absolute/path/to/视频二创"
```

The factory resolves yt-dlp from the active project virtualenv and automatically supplies managed cookies, a supported JavaScript runtime, and configured platform impersonation. Use `JAGUARTV_YTDLP_BINARY=/absolute/path/to/yt-dlp` only for a verified executable override; put platform session files in the existing session manager rather than command-line arguments. Do not call yt-dlp directly for candidate downloads because that bypasses shared metadata, validation, and database state.

Read `references/pipeline.md` when diagnosing states, adapters, or output files.
