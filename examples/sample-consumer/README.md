# sample-consumer

Minimal consumer workspace example for `agent-session-harness`.

This is not a second harness implementation.
It shows the smallest workspace shape you would usually want after adopting the harness into your own repository.

## What This Example Shows

- a minimal root contract (`AGENTS.md`)
- a routing-only index (`AI_INDEX.md`)
- a decision log
- a task spec
- the expected worker-launch path through `scripts/ai_worker`

## Expected Layout

```text
sample-consumer/
├── AGENTS.md
├── AI_INDEX.md
└── docs/
    └── specs/
        ├── project-roadmap/
        │   └── decision-log.md
        └── task-spec.md
```

## Adoption Notes

In a real consumer repository, you would vendor or copy the harness runtime to the repo root, then use:

```bash
scripts/ai_worker codex docs/specs/task-spec.md -- --model gpt-5.4
```

That command will:
- derive `task-spec` as the default task id
- include `docs/specs/project-roadmap/decision-log.md`
- compute the current approved `docs_revision`
- call the lower-level `scripts/start_worker_session` wrapper

## Why This Example Is Small

The harness is intentionally opinionated about runtime safety, but not about your full product structure.

This example keeps only the minimum pieces needed to show:
- where canonical docs live
- what a worker launch points at
- what a thin consumer workspace looks like before adding team-specific rules
