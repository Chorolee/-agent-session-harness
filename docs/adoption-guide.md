# Adoption Guide

This guide shows the minimum path for adopting `agent-session-harness` into another repository.

The goal is not to recreate the full author workspace.
The goal is to establish a small, explicit contract for:
- where canonical docs live
- how worker sessions launch
- what is allowed to count as executable continuity

## 1. Start With A Small Consumer Shape

At minimum, create a workspace with:

```text
<repo-root>/
├── AGENTS.md
├── AI_INDEX.md
├── docs/
│   └── specs/
│       ├── project-roadmap/
│       │   └── decision-log.md
│       └── task-spec.md
├── scripts/
│   ├── ai_worker
│   └── start_worker_session
└── tools/
    └── harness/
```

You can use `examples/sample-consumer/` as the smallest reference layout.

## 2. Copy Or Vendor The Runtime

Bring these paths into your target repository:
- `tools/harness/`
- `scripts/start_worker_session`
- `scripts/ai_worker`
- `AGENTS.md`
- `AI_INDEX.md`
- `docs/public-contract.md`
- minimal `docs/specs/` and `docs/ops/` scaffolding as needed

The harness is designed to be portable.
It does not require a large external service, but it does assume repo-local docs and worker-launch wrappers exist.

If you want a single bootstrap step, use:

```bash
scripts/bootstrap_consumer --target ../my-repo --copy-runtime
```

That path copies:
- the minimal consumer scaffolding
- the portable runtime
- the public contract docs
- the optional Claude worker-launch skill directory

## 3. Establish Canonical Docs

For a basic adoption, define at least:
- a decision log at `docs/specs/project-roadmap/decision-log.md`
- one task spec such as `docs/specs/task-spec.md`

Those documents are not just reference material.
They become part of the worker launch basis.

## 4. Keep Head And Worker Paths Separate

Use the head session for:
- planning
- review
- routing
- deciding when a worker is needed

Use a worker session when you need:
- explicit task identity
- explicit doc basis
- executable continuity for implementation work

This separation is the core operating model.

## 5. Use The Ergonomic Launch Path First

The preferred launch path is:

```bash
scripts/ai_worker codex docs/specs/task-spec.md -- --model gpt-5.4
```

That wrapper automatically:
- derives a default task id from the spec filename
- includes the default decision log
- computes the current approved `docs_revision`
- calls the lower-level binding-first launcher

If you need the lower-level path directly, use:

```bash
scripts/start_worker_session codex task-slug \
  --docs-revision <approved-token> \
  --doc-basis-path docs/specs/project-roadmap/decision-log.md \
  --doc-basis-path docs/specs/task-spec.md \
  -- --model gpt-5.4
```

## 6. Decide Your Approval Policy

This repository does not force your team's document approval process.

You still need to define:
- what makes a `docs_revision` approved in your workspace
- how decision logs and task specs are updated
- any extra routing rules or task naming conventions

The harness gives you the runtime core.
It does not replace your workspace governance.

## 7. Verify The Minimal Paths

After adoption, verify at least:
- `scripts/bootstrap_consumer --help`
- `scripts/start_worker_session --help`
- `scripts/ai_worker --help`
- `scripts/ai_worker codex docs/specs/task-spec.md --print-command -- --model gpt-5.4`

Those checks confirm:
- the scripts are executable
- the repository shape is valid
- doc-basis-driven worker launch can be resolved

## 8. Add Team-Specific Layers Later

Only after the base harness works should you add:
- trigger maps
- team aliases
- custom wrappers
- richer docs scaffolding
- repository-specific policy layers

Start thin.
Add policy after the runtime path is proven.
