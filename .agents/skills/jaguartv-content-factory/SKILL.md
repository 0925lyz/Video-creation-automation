---
name: jaguartv-content-factory
description: Run and inspect the JaguarTV local video factory for batch discovery, platform video download, non-Portuguese filtering, pt-BR localization, vertical rendering, QA, and review-package generation. Use when asked to crawl video candidates, produce Brazilian Portuguese short videos, inspect pipeline status, retry media jobs, or prepare content for human review.
---

# JaguarTV Content Factory

Operate the shared project through `scripts/factory.sh`. Keep source discovery, downloads, renders, metadata, and review state in the project workspace; do not create a second task database inside the skill directory.

## Workflow

1. Run `scripts/factory.sh doctor` before a new environment or after dependency changes.
2. Run `scripts/factory.sh discover --platform youtube --limit 3` to collect candidates. Add other implemented adapters only after `doctor` confirms availability.
3. Inspect candidates with `scripts/factory.sh list --status DISCOVERED --limit 20`.
4. Download a selected candidate with `scripts/factory.sh download --candidate <id>`.
5. Produce a review package with `scripts/factory.sh produce --candidate <id>`.
6. Generate the review index with `scripts/factory.sh review`.
7. Treat `workspace/ready_for_review/<id>/` as the handoff boundary for human review.
8. Run `scripts/factory.sh ui --host 127.0.0.1 --port 8787` for the standalone inventory, publishing, analytics, and worker dashboard. This UI does not depend on WorkBuddy.

The default `audio.source_mode: auto` preserves original audio for non-localized clips and never adds fixed BGM. Only Bilibili/Douyin clips with Chinese narration evidence should remove the source track and receive Brazilian Portuguese narration/subtitles. Clips without that platform-specific Chinese speech evidence preserve their original music/audio and receive no generated narration or subtitles. Use `audio.source_mode: localize` or `preserve` only for an explicit operator override. The default foreground crop removes the lower burned-in source-caption band; inspect a frame and adjust the crop ratio when captions remain.

## Coordination

- Let the source scout own discovery and candidate recommendations.
- Let the localization editor own `produce` and pt-BR script quality.
- Let the quality reviewer inspect video dimensions, duration, the recorded audio mode/reason, captions, voice/BGM balance, branding, and metadata.
- Share candidate IDs and exact output paths between agents.
- Do not run two production commands for the same candidate concurrently.
- Preserve failed events and intermediate assets for diagnosis.

## Environment

Set `JAGUARTV_FACTORY_ROOT` when the skill is installed outside the repository:

```bash
export JAGUARTV_FACTORY_ROOT="/absolute/path/to/视频二创"
```

Read `references/pipeline.md` when diagnosing states, adapters, or output files.
