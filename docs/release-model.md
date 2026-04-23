# Release Model

Language: English | [한국어](release-model.ko.md)

This document describes what a public release in `agent-session-harness` is intended to mean.

It is narrower than a full product support promise.
It is broader than a loose code snapshot.

## Current Stage

Current target stage:
- public beta

What that means:
- the repository is meant to be adopted by other repos, not just read as a reference
- the public runtime surface should stay coherent across tags
- install/bootstrap, adoption docs, and compatibility notes are part of the supported surface

What it does not mean:
- all internal helper names are stable
- all workspace shapes are supported
- all third-party CLIs are guaranteed compatible

## Public Surface

The beta public surface is:
- `scripts/start_worker_session`
- `scripts/ai_worker`
- `scripts/bootstrap_consumer`
- `docs/public-contract.md`
- `docs/adoption-guide.md`
- `docs/compatibility.md`
- `docs/migration-guide.md`
- `examples/sample-consumer/`

The public surface is what downstream adopters should build against first.

## Stability Levels

### Stable across beta tags

Consumers should expect these ideas to remain stable:
- head session vs worker session separation
- binding-first worker launch
- bounded, metadata-first resume
- explicit task identity and lineage
- doc-basis-aware executable continuity
- `ai_worker` as the preferred ergonomic launch path
- `bootstrap_consumer --copy-runtime` as the minimum install/bootstrap path

### Allowed to change during beta

These may still change without being treated as a breaking public regression:
- internal module names under `tools/harness/`
- reducer/helper organization
- exact internal cache shapes
- examples and scaffolding details that do not change the runtime contract

## Beta Exit Criteria

The repository is ready to leave beta when:
- at least two distinct consumer repo shapes have been validated
- install/bootstrap flow is clear enough for first-time adoption
- migration guidance exists for public tags
- compatibility expectations are explicit
- runtime contract and release model are no longer drifting every tag

## Tagging Guidance

Recommended tag meanings:
- `alpha`: public shape is still moving quickly
- `beta`: adoption path exists and public runtime surface is documented
- `stable`: migration expectations and support boundaries are predictable enough for wider reuse

## Consumer Guidance

If you adopt this repo during beta:
- follow the tagged docs, not random `main` commits
- treat `docs/public-contract.md` as the runtime authority document
- use `scripts/bootstrap_consumer --copy-runtime` for first adoption unless you already have a strong vendoring workflow
- check `CHANGELOG.md` and `docs/migration-guide.md` before upgrading across tags
