# agent-session-harness

Language: English | [한국어](README.ko.md)

Portable session harness for `claude` and `codex`.

## Why This Exists

Most agent workflows have two failure modes:
- a head session leaves useful context, but that context is too loose to trust as executable continuity
- worker sessions get resumed from "latest project state" guesses instead of from a validated task binding

This harness exists to keep those two paths separate:
- head sessions can stay thin and conversational
- task-bound worker sessions can require stronger proof before they are treated as executable resume state

The goal is not to fully automate agent orchestration. The goal is to make resume and worker launch safer, more explicit, and easier to inspect.

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

## What Is Automatic

Automatic once the harness is installed:
- hook events can be normalized and appended to the journal
- resume state stays bounded and metadata-first
- `start_worker_session` fixes `--session-cwd` from the caller cwd
- `start_worker_session` chooses the canonical handoff store automatically
- cross-checkout `--worker-cwd` is rejected instead of guessed

Still explicit or manual by design:
- choosing when to continue in the head session vs launch a new worker
- choosing the task id for a task-bound worker session
- approving `--docs-revision`
- choosing the `--doc-basis-path` inputs that define the worker's document basis
- adding a higher-level UX wrapper such as `scripts/ai_worker`

In short:
- head session continuation is lightweight
- executable worker continuity is stricter and intentionally not "magic"

## Operational Benefits

Besides safer worker launch, the harness makes agent runs easier to operate and inspect:
- sessions are easier to trace across head and worker boundaries
- resume decisions are tied to explicit task and session identity instead of loose "latest project state" guesses
- document basis stays grouped with the worker launch, which makes review and audit trails clearer
- worktree and cwd mistakes are reduced because the launcher rejects unsafe cross-checkout assumptions
- the same runtime model works for both `claude` and `codex`, so the policy is not locked to one vendor CLI
- head sessions can stay conversational while worker sessions carry stricter execution proof

## Quick Start

Head session continuation:

```bash
claude
# or
codex
```

Task-bound worker session via the binding-first wrapper:

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
