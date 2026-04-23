---
name: worker-launch
description: Start a new task-bound Claude/Codex worker session through the canonical binding-first wrapper instead of calling the vendor CLI directly.
disable-model-invocation: true
---

1. Use this only when the head is spawning a new task-bound worker or agent session. Keep normal head-session startup plain.
2. Launch workers with `"$(git rev-parse --show-toplevel)/scripts/start_worker_session" <claude|codex> <task_id> --docs-revision <approved-token> --doc-basis-path <decision-log> --doc-basis-path <approved-spec> -- <tool args ...>`.
3. Run that wrapper from inside the target git checkout/worktree. Absolute-path invocation from a non-git cwd is not supported.
4. Write `--doc-basis-path` values repo-root-relative. The wrapper runs from the git top-level even when the caller starts in a project subdir.
5. If the target worktree intentionally differs from the common checkout in approved basis files, `AGENTS.md`, `docs/ops/*`, or other execution-mirror files, add `--docs-source branch-docs-approved --doc-mode branch-docs-approved --docs-revision <approved-token>`.
6. If the task owns a specific project directory and the current `cwd` is broader than that scope, pass explicit `--worker-cwd <target-project-or-worker-dir>`. If the target lives in a different checkout/worktree, move into that checkout first and run its wrapper there.
7. Keep vendor-specific flags such as `--model ...` after `--` and pass them through unchanged.
8. Use `python3 -m tools.harness.agent_session ...` or `python3 -m tools.harness.session_launcher ...` directly only for debugging or lower-level manual recovery.
9. This skill is a Claude-only workflow adapter. The harness runtime itself supports both Claude and Codex; Codex uses the same shell/Python entrypoints directly.
