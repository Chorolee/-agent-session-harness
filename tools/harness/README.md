# tools/harness

Portable runtime modules for:
- hook dispatch
- journal append/load
- resume reduction
- binding-first worker launch
- doc-basis validation

These modules are shared runtime code for both `claude` and `codex`.
Only adapter files outside this folder are vendor-specific.

Key entrypoints:
- `dispatch.py`
- `session_launcher.py`
- `agent_session.py`
- `session_identity.py`
- `handoff.py`

This copy is intentionally lighter than the original monorepo version and is meant as a publishable code bundle.
