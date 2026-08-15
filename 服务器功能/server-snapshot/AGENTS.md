# JaguarTV Content Factory

Use the project-local `jaguartv-content-factory` skill for all discovery, download, localization, rendering, and review tasks.

## ECC Project Mode

Apply the ECC workflow style to every Codex conversation in this repository.
ECC is used here as a project-level operating pattern, not as a full vendored
plugin copy. Keep this layer compact so it improves judgment without bloating
context.

- Plan before complex implementation, especially for scraping, rendering,
  dashboard, deployment, database migration, and security work.
- Prefer test-driven changes: add or update focused tests before changing
  production code when fixing bugs or adding behavior.
- Run a verification loop before finishing meaningful code changes: relevant
  tests, syntax/build checks, `git diff --check`, and a short risk scan.
- Treat security-sensitive areas as high priority: dashboard APIs, uploads,
  SSH/server scripts, crawler inputs, shell commands, SQLite writes, public
  media paths, cookies, and platform session files.
- Never hardcode secrets, tokens, cookies, private keys, or server credentials.
  Use environment variables, local ignored files, or existing config hooks.
- Validate all external input at boundaries: HTTP payloads, filenames, URLs,
  metadata JSON, crawler records, subtitles, config values, and shell args.
- Preserve user work. Do not revert unrelated changes or rewrite branches unless
  the user explicitly asks.
- Keep context lean. Do not copy large external frameworks, generated assets, or
  full upstream repositories into this project unless they are intentionally
  vendored and reviewed.
- Capture durable project knowledge in existing docs, config comments, tests, or
  this file. Do not create top-level process docs for temporary observations.
- For Remotion work, follow ECC's Remotion guidance: use dynamic metadata,
  sequence-based composition, explicit assets, readable props, caption-aware
  layouts, and render verification.

## Shared Workflow

- Source agents run discovery and report candidate IDs.
- Localization agents download and produce selected candidates.
- QA agents inspect `workspace/ready_for_review/` and update review decisions.
- All agents share `workspace/factory.db`; do not create separate databases.
- Do not process the same candidate concurrently.
- Keep automatic publishing out of Phase 1.

## Commands

```bash
./.agents/skills/jaguartv-content-factory/scripts/factory.sh doctor
./.agents/skills/jaguartv-content-factory/scripts/factory.sh discover --platform youtube --limit 3
./.agents/skills/jaguartv-content-factory/scripts/factory.sh list --status DISCOVERED
./.agents/skills/jaguartv-content-factory/scripts/factory.sh download --candidate <id>
./.agents/skills/jaguartv-content-factory/scripts/factory.sh produce --candidate <id>
./.agents/skills/jaguartv-content-factory/scripts/factory.sh review
```
