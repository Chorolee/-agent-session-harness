# agent-session-harness

Portable session harness for `claude` and `codex`.

What it does:
- records hook/journal metadata
- keeps resume state bounded and metadata-first
- launches task-bound worker sessions through a binding-first wrapper
- separates thin head sessions from executable worker continuity

This export is intentionally genericized for publishing:
- workspace-specific project names were removed from the top-level docs
- minimal canonical docs are included so the launcher can validate doc basis
- monorepo-specific tests and trigger maps were excluded

## Supports Both Claude And Codex

The runtime code supports both vendor CLIs:
- `claude`
- `codex`

The included `.claude/skills/worker-launch/SKILL.md` is only an optional Claude adapter.
The runtime itself is not Claude-only: Codex uses the same Python and shell entrypoints directly.

## Quick Start

Head session:

```bash
claude
```

Task-bound worker session:

```bash
"$(git rev-parse --show-toplevel)/scripts/start_worker_session" codex task-slug \
  --docs-revision <approved-token> \
  --doc-basis-path docs/specs/project-roadmap/decision-log.md \
  --doc-basis-path docs/specs/task-spec.md \
  -- --model gpt-5.4
```

Claude worker session:

```bash
"$(git rev-parse --show-toplevel)/scripts/start_worker_session" claude task-slug \
  --docs-revision <approved-token> \
  --doc-basis-path docs/specs/project-roadmap/decision-log.md \
  --doc-basis-path docs/specs/task-spec.md \
  -- --model claude-sonnet-4-6
```

## Included

- `tools/harness/`
- `scripts/start_worker_session`
- `.claude/skills/worker-launch/SKILL.md`
- minimal generic `AGENTS.md`, `CLAUDE.md`, `AI_INDEX.md`
- minimal `docs/specs/` and `docs/ops/` scaffolding used by doc-basis validation

## Excluded

- project-specific trigger maps
- monorepo-specific tests and fixtures
- evidence, design assets, and unrelated workspace files

## Notes

- `start_worker_session` is the low-level safe entrypoint for both Claude and Codex worker sessions.
- A higher-level UX wrapper such as `scripts/ai_worker` can be added later on top of it.
- Review and adjust the generic docs before publishing as your own canonical contract.
