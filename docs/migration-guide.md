# Migration Guide

Language: English | [한국어](migration-guide.ko.md)

This guide covers the public migration path from alpha tags to the first beta tag.

## Scope

This guide is for downstream consumers who already copied or vendored the public harness into another repository.

It assumes you are upgrading from one of:
- `v0.1.0-alpha1`
- `v0.1.0-alpha2`
- `v0.1.0-alpha3`

## Moving To Beta

The main beta shift is not a new runtime engine.
It is a stronger public contract around installation, adoption, and release expectations.

You should treat the following as first-class public artifacts:
- `CHANGELOG.md`
- `docs/public-contract.md`
- `docs/adoption-guide.md`
- `docs/compatibility.md`
- `docs/release-model.md`

## Recommended Upgrade Path

1. Vendor or copy the newer runtime paths.
2. Re-run `scripts/bootstrap_consumer --copy-runtime --force` in a disposable clone of your consumer repo.
3. Review your canonical docs:
   - `docs/specs/project-roadmap/decision-log.md`
   - `docs/specs/task-spec.md`
4. Verify the public launch paths:
   - `scripts/ai_worker --help`
   - `scripts/start_worker_session --help`
   - `scripts/bootstrap_consumer --help`
5. Run a print-only worker launch:
   - `scripts/ai_worker codex docs/specs/task-spec.md --print-command -- --model gpt-5.4`

## Alpha-Specific Notes

### From alpha1

You are missing:
- `scripts/bootstrap_consumer`
- `docs/adoption-guide.md`
- `docs/compatibility.md`
- sample consumer workspace

The easiest path is to re-vendor the public runtime from a beta tag instead of manually cherry-picking pieces.

### From alpha2

You already have the basic adoption path.
Review:
- `scripts/bootstrap_consumer --force`
- nested-directory `scripts/ai_worker` launches

If you had local patches around those areas, reconcile them carefully before upgrading.

### From alpha3

Your runtime shape is already close to beta.
The main additions are:
- release model documentation
- migration guidance
- changelog-backed public release expectations

## What Should Stay The Same

The beta migration should not change these operating ideas:
- head sessions stay thin
- worker continuity stays stricter than conversational continuity
- `ai_worker` is still the preferred ergonomic path
- `start_worker_session` remains the low-level authority boundary

## What To Recheck In Your Repo

After upgrading, recheck:
- executable bits on copied scripts
- canonical doc locations
- any local aliases or wrappers that call `ai_worker`
- any downstream docs that still describe alpha-only behavior
