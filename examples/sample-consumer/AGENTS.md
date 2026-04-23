# Workspace Contract

This sample workspace uses `agent-session-harness`.

Core rules:
- keep head sessions thin and conversational
- use `scripts/ai_worker` or `scripts/start_worker_session` for task-bound worker continuity
- treat `AI_INDEX.md` as routing-only
- keep durable product decisions in `docs/specs/`
