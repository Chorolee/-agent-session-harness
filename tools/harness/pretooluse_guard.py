"""PreToolUse live guard — deny new markdown outside allowlist.

Called by a PreToolUse hook for Write|Edit|NotebookEdit.
Reads tool_input from stdin JSON, checks allowlist, denies if needed.

Exit codes:
  0 = allow (or error → fail-open)
  2 = deny (prints reason to stdout for Claude to show user)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import artifacts


def main() -> None:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)  # fail-open

    try:
        tool_input = data.get("tool_input", {})
        if not isinstance(tool_input, dict):
            sys.exit(0)

        file_path = tool_input.get("file_path")
        if not file_path:
            sys.exit(0)

        # Find repo root
        repo_root = _find_repo_root(file_path)

        if artifacts.check_allowlist(file_path, repo_root):
            sys.exit(0)  # allowed
        else:
            # Deny: print reason for Claude to display
            reason = (
                f"Blocked: new markdown '{file_path}' is outside the allowlist. "
                "Use docs/specs/, docs/_archive/, root docs/ops/, AI_INDEX.md, or request an exception."
            )
            print(json.dumps({"decision": "deny", "reason": reason}))
            sys.exit(2)
    except Exception:
        sys.exit(0)  # fail-open


def _find_repo_root(file_path: str | None = None) -> str:
    if file_path:
        p = Path(file_path).resolve()
        if not p.is_dir():
            p = p.parent
    else:
        p = Path.cwd().resolve()
    for parent in [p, *p.parents]:
        if (parent / ".git").exists():
            return str(parent)
    return str(p)


if __name__ == "__main__":
    main()
