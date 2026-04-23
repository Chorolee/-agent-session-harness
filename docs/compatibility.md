# Compatibility Matrix

This document describes the intended compatibility surface of the public alpha.

It is not a guarantee that every downstream environment behaves identically.
It is the current support contract for the runtime model exposed by this repository.

## Session / Launch Matrix

| Path | Intended use | Continuity level |
| --- | --- | --- |
| `claude` head session | planning, review, routing | thin / conversational |
| `codex` head session | planning, review, routing | thin / conversational |
| `scripts/ai_worker claude <spec>` | ergonomic worker launch | executable continuity path |
| `scripts/ai_worker codex <spec>` | ergonomic worker launch | executable continuity path |
| `scripts/start_worker_session claude ...` | low-level worker launch | executable continuity path |
| `scripts/start_worker_session codex ...` | low-level worker launch | executable continuity path |
| one-shot invocations by themselves | quick inspect / preflight / ad hoc use | preflight-only |

## Authority Matrix

| Signal | Can authorize executable continuity by itself? |
| --- | --- |
| validated `SessionStart` only | no |
| `IdentityAcknowledged` only | no |
| latest project recency | no |
| prompt / assistant excerpts | no |
| rendered context alone | no |
| selection clue alone | no |
| validated `SessionStart` + matching durable ack + binding proof + current git/doc basis | yes |

## Worktree / CWD Expectations

| Scenario | Expected result |
| --- | --- |
| launch from target checkout/worktree | supported |
| unsafe cross-checkout `--worker-cwd` | rejected |
| sibling worktree executable continuity bleed | not supported |
| head session continuation in repo root | supported as thin path |
| worker launch with explicit doc basis | supported |

## Public Alpha Notes

Current public alpha assumptions:
- runtime supports both `claude` and `codex`
- the ergonomic wrapper is `scripts/ai_worker`
- the lower-level binding-first launcher is `scripts/start_worker_session`
- executable continuity remains narrower than conversational continuity

What is intentionally outside the current compatibility promise:
- full install/bootstrap automation
- vendor-agnostic support for arbitrary third-party CLIs
- a dashboard or UI session browser
- workspace-specific trigger packs
