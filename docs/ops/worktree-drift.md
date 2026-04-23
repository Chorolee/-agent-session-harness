# Worktree Drift

- If a worktree intentionally diverges, run workers inside that worktree.
- If drift is accidental, sync before delegating.
- Record `docs_source`, `docs_revision`, and `doc_mode` for worktree execution.
