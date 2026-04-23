# Public Contract

Language: English | [한국어](public-contract.ko.md)

This document describes the external contract for the portable harness.

It is intentionally narrower than the internal implementation notes in `docs/ops/`.
Use this document when you need to understand:
- what this harness treats as executable worker continuity
- what stays metadata-only or preflight-only
- what a consumer workspace still needs to define for itself

## Purpose

The harness separates two different kinds of continuity:
- head-session continuity: conversational and lightweight
- worker-session continuity: task-bound and executable

The contract is designed so that "resume" does not mean "replay as much transcript as possible."
Instead, executable continuity is only granted when task identity and launch proof are strong enough.

## Scope

This repository is a portable reference harness, not a full drop-in product.

It provides:
- hook and journal runtime code
- bounded, metadata-first resume handling
- binding-first worker launch
- minimal document scaffolding for doc-basis validation

It does not provide:
- your team's canonical docs or approval workflow
- your task naming conventions
- your full trigger map or fixture set
- a final high-level UX wrapper for worker launch

## Launch Model

### Head session

Head sessions are expected to stay thin and conversational.

They are appropriate for:
- planning
- review
- routing
- deciding whether a new worker is needed

Head-session continuity may carry useful metadata and context, but by itself it does not prove executable worker continuity.

### Worker session

Worker sessions are task-bound sessions launched through `scripts/start_worker_session`.

They are appropriate when you need:
- explicit task identity
- explicit document basis
- stronger resume correctness
- executable continuity for real implementation work

## Executable Continuity Authority

Executable worker continuity is intentionally narrow.

The harness treats executable continuity as valid only when the current session state is backed by all of the following:
- a latest validated `SessionStart`
- a matching durable `IdentityAcknowledged`
- binding proof for the worker launch
- current git and doc-basis freshness checks

If one of those pieces is missing or stale, the session may still produce metadata or preflight context, but it must not be treated as executable continuity.

## Non-Authoritative Inputs

The following inputs may help routing, display, or selection, but they do not authorize executable continuity on their own:
- latest project recency
- prompt or assistant excerpts
- rendered context alone
- project scope alone
- selection clues alone
- thin session starts
- one-shot invocations by themselves

In practical terms:
- metadata can help you inspect a prior run
- metadata alone must not silently become executable worker resume

## Resume States

At a high level, consumers should expect three outcomes:
- executable continuity: safe to continue task-bound worker execution
- chooser or preflight path: enough signal to inspect or select, but not enough to execute
- unavailable or stale: not safe to continue as a worker session

The exact internal shapes may evolve, but that authority boundary should remain stable.

## Launch Invariants

The public launch contract assumes:
- the worker is launched from inside the target checkout or worktree
- `start_worker_session` controls `--session-cwd`
- `start_worker_session` chooses the canonical handoff store
- unsafe cross-checkout `--worker-cwd` is rejected instead of guessed
- the worker launch declares an approved document basis

This contract is intentionally conservative.
It is better to fall back to preflight-only behavior than to accidentally grant executable continuity.

## Worktree and CWD Safety

The harness is designed to reduce ambiguity in multi-repo and worktree-heavy environments.

Public expectations:
- sibling worktrees should not silently share executable worker continuity
- caller cwd and worker cwd should not drift without explicit handling
- launch-time worktree hints may help routing, but they are not enough to authorize execution on their own

## Consumer Responsibilities

If you adopt this harness in your own workspace, you are still responsible for defining:
- canonical docs and document approval policy
- task naming rules
- any high-level UX wrapper you want on top of `start_worker_session`
- installation/bootstrap flow
- workspace-specific trigger maps and policy layers

## Stability Expectations

Treat this document as the public contract for the repository's runtime model.

Stable ideas:
- head vs worker separation
- binding-first worker launch
- bounded, metadata-first resume
- explicit task identity and lineage
- doc-basis-aware executable continuity

Less stable implementation details:
- internal helper/module names
- private reducer/helper shapes
- the exact organization of `docs/ops/`
