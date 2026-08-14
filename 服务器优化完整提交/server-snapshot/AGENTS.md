# JaguarTV Content Factory

Use the project-local `jaguartv-content-factory` skill for all discovery, download, localization, rendering, and review tasks.

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
