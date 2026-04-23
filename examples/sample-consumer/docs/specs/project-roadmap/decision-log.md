# Decision Log

## 2026-04-24

- Adopted `agent-session-harness` as the worker-launch and resume runtime.
- Head sessions remain lightweight; executable continuity must go through the binding-first worker path.
- Task-bound worker launches should point at this decision log plus a task spec.
