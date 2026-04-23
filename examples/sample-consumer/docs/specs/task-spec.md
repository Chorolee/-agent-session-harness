# Task Spec

## Scope

Show the smallest consumer workspace shape that can support a spec-driven worker launch.

## Decision

Use:
- `docs/specs/project-roadmap/decision-log.md` as the default decision-log basis
- this file as the task-specific doc basis
- `scripts/ai_worker` as the preferred ergonomic launch path

## Implementation Order

1. Keep the root workspace contract minimal.
2. Keep the routing index lightweight.
3. Launch task-bound workers from this spec through `scripts/ai_worker`.

## Decision Log

- This sample favors clarity over completeness.
- Team-specific routing, aliases, and trigger maps are intentionally omitted.
