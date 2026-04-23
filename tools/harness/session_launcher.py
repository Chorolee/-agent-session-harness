"""Canonical binding-first launcher for Claude/Codex sessions."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from . import session_identity


def _default_session_cwd() -> str:
    return str(Path(os.getcwd()).resolve())


def _normalized_path(path: str | Path) -> str:
    return str(Path(path).resolve())


def _default_worktree_cwd(worker_cwd: str) -> str:
    return session_identity._find_repo_root(worker_cwd)


def _build_tool_command(tool: str, passthrough: list[str]) -> list[str]:
    command = list(passthrough)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        return [tool]
    return [tool, *command]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="tool", required=True)

    for tool in ("claude", "codex"):
        sub = subparsers.add_parser(tool, help=f"Launch a bound {tool} session")
        sub.add_argument("--handoff-dir", default=None)
        sub.add_argument("--session-cwd", default=_default_session_cwd())
        sub.add_argument("--worker-cwd", default=None)
        sub.add_argument("--worktree-cwd", default=None)
        sub.add_argument("--task-id", required=True)
        sub.add_argument("--route-id", required=True)
        sub.add_argument("--doc-basis-id", required=True)
        sub.add_argument(
            "--docs-source",
            default="root-canonical",
            choices=("root-canonical", "branch-docs-approved"),
        )
        sub.add_argument("--docs-revision", required=True)
        sub.add_argument(
            "--doc-mode",
            default="root-canonical",
            choices=("root-canonical", "branch-docs-approved"),
        )
        sub.add_argument(
            "--doc-basis-path",
            dest="doc_basis_paths",
            action="append",
            default=None,
        )
        sub.add_argument("tool_args", nargs=argparse.REMAINDER)

    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    session_cwd = _normalized_path(args.session_cwd)
    worker_cwd = _normalized_path(args.worker_cwd or session_cwd)
    worktree_cwd = _normalized_path(args.worktree_cwd or _default_worktree_cwd(worker_cwd))
    if args.handoff_dir:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": "session_launcher manages handoff storage automatically",
                },
                ensure_ascii=False,
            )
        )
        return 1
    handoff_dir = session_identity.binding_handoff_dir_for_worker_cwd(worker_cwd)
    command = _build_tool_command(args.tool, list(args.tool_args))

    try:
        return session_identity.launch_bound_command(
            handoff_dir,
            command=command,
            session_cwd=session_cwd,
            task_id=args.task_id,
            route_id=args.route_id,
            worker_cwd=worker_cwd,
            worktree_cwd=worktree_cwd,
            doc_basis_id=args.doc_basis_id,
            docs_source=args.docs_source,
            docs_revision=args.docs_revision,
            doc_mode=args.doc_mode,
            doc_basis_paths=args.doc_basis_paths,
        )
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1
    except FileNotFoundError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 127
    except OSError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
