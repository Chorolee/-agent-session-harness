# Changelog

All notable changes to this repository will be documented in this file.

The public release tags are the source of truth for runtime milestones.

## [0.1.0-beta1] - 2026-04-24

### Added
- public release model documentation in `docs/release-model.md`
- alpha-to-beta migration guidance in `docs/migration-guide.md`
- a repository-level changelog for public releases

### Stabilized
- `scripts/ai_worker` as the preferred ergonomic worker-launch path
- `scripts/bootstrap_consumer --copy-runtime` as the minimum public install/bootstrap path
- public contract, compatibility, and adoption docs as the supported public beta surface

### Notes
- this is still a prerelease beta, not a stable 1.0 contract
- internal module boundaries may continue to change, but the public runtime model should remain aligned with `docs/public-contract.md`

## [0.1.0-alpha3] - 2026-04-24

### Fixed
- hardened real adoption path after bootstrap verification against an external repo clone
- fixed `scripts/ai_worker` path resolution from nested directories
- stabilized `scripts/bootstrap_consumer --force`
- added CI coverage for bootstrap adoption regressions

## [0.1.0-alpha2] - 2026-04-24

### Added
- `scripts/bootstrap_consumer`
- `docs/adoption-guide.md`
- `docs/compatibility.md`
- sample consumer workspace under `examples/sample-consumer/`

## [0.1.0-alpha1] - 2026-04-24

### Added
- public standalone export
- bilingual README support
- `scripts/ai_worker`
- public contract docs
- minimal CI smoke workflow
- MIT license
