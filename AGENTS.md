# Workspace Contract

This repository hosts a portable AI session harness.

Core rules:
- use the smallest diff that solves the request
- keep head sessions thin unless a task-bound worker is required
- use `scripts/start_worker_session` for executable worker continuity
- treat `AI_INDEX.md` as routing-only
- keep durable policy in `AGENTS.md`, `docs/public-contract.md`, `docs/specs/`, and `docs/ops/`
