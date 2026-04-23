"""Ergonomic agent/worker session launcher for Claude/Codex passthrough."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import session_identity
from . import session_launcher


_DISCOVERY_FLAGS = {"-h", "--help", "-V", "--version"}
_CODEX_DISCOVERY_COMMAND_PATHS = {
    ("completion",),
    ("completion", "bash"),
    ("completion", "elvish"),
    ("completion", "fish"),
    ("completion", "powershell"),
    ("completion", "zsh"),
    ("debug",),
    ("e",),
    ("exec",),
    ("exec", "mcp"),
    ("exec", "resume"),
    ("exec", "review"),
    ("features",),
    ("login",),
    ("login", "status"),
    ("logout",),
    ("mcp",),
    ("mcp", "add"),
    ("mcp", "get"),
    ("mcp", "list"),
    ("mcp", "login"),
    ("mcp", "logout"),
    ("mcp", "remove"),
    ("plugin",),
    ("plugin", "marketplace"),
    ("plugin", "marketplace", "add"),
    ("plugin", "marketplace", "remove"),
    ("plugin", "marketplace", "upgrade"),
    ("review",),
    ("resume",),
    ("sandbox",),
    ("sandbox", "linux"),
}


def _default_session_cwd() -> str:
    return str(Path.cwd().resolve())


def _normalized_path(path: str | Path) -> str:
    return str(Path(path).resolve())


def _normalized_segment(value: str, *, default: str | None = None) -> str:
    normalized = "".join(
        ch if ch.isalnum() or ch in {"-", "_", "."} else "-"
        for ch in value.strip()
    ).strip("-")
    if normalized:
        return normalized
    if default is not None:
        return default
    raise ValueError("task_id must contain at least one non-separator character")


def _approved_projects_from_doc_basis_paths(
    session_cwd: str,
    doc_basis_paths: list[str] | None,
) -> set[str]:
    session_path = Path(session_cwd).resolve()
    repo_root = Path(session_identity._find_repo_root(str(session_path))).resolve()
    if not doc_basis_paths:
        return set()

    candidates: set[str] = set()
    for basis_path in doc_basis_paths:
        basis_candidate = Path(basis_path)
        if basis_candidate.is_absolute():
            try:
                parts = basis_candidate.resolve().relative_to(repo_root).parts
            except ValueError as exc:
                raise ValueError(
                    "doc_basis_paths must stay within the worker repo"
                ) from exc
        else:
            parts = basis_candidate.parts
        if not parts:
            continue
        candidate = repo_root / parts[0]
        if (candidate / "AGENTS.md").exists():
            candidates.add(parts[0])
    return candidates


def _project_name_for_worker_cwd(worker_cwd: str) -> str | None:
    worker_path = Path(worker_cwd).resolve()
    repo_root = Path(session_identity._find_repo_root(str(worker_path))).resolve()
    try:
        rel = worker_path.relative_to(repo_root)
    except ValueError:
        return None
    if not rel.parts:
        return None
    candidate = repo_root / rel.parts[0]
    if (candidate / "AGENTS.md").exists():
        return rel.parts[0]
    return None


def _default_worker_cwd(
    session_cwd: str,
    doc_basis_paths: list[str] | None,
) -> str:
    session_path = Path(session_cwd).resolve()
    repo_root = Path(session_identity._find_repo_root(str(session_path))).resolve()
    try:
        rel = session_path.relative_to(repo_root)
    except ValueError:
        return str(session_path)

    approved_projects = _approved_projects_from_doc_basis_paths(
        session_cwd,
        doc_basis_paths,
    )
    if len(approved_projects) > 1:
        raise ValueError(
            "doc_basis_paths span multiple projects; pass explicit --worker-cwd"
        )
    if approved_projects:
        return str(repo_root / next(iter(approved_projects)))
    if not rel.parts:
        return str(repo_root)

    candidate = repo_root / rel.parts[0]
    if (candidate / "AGENTS.md").exists():
        return str(candidate)
    return str(repo_root)


def _default_route_id(task_id: str, worker_cwd: str) -> str:
    scope = _normalized_segment(Path(worker_cwd).resolve().name, default="session")
    normalized_task_id = _normalized_segment(task_id)
    return f"route-{scope}-{normalized_task_id}"


def _emit_error(message: str) -> int:
    print(json.dumps({"status": "error", "error": message}, ensure_ascii=False))
    return 1


def _emit_file_error(message: str) -> int:
    print(json.dumps({"status": "error", "error": message}, ensure_ascii=False))
    return 127


def _discovery_passthrough_command(tool: str, tool_args: list[str]) -> list[str] | None:
    if not tool_args:
        return None
    args = list(tool_args)
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        return None
    classify_args = list(args)
    if args and args[0] == "env":
        classify_args = session_identity._env_wrapped_command(args[1:])
        if not classify_args:
            return None
        if classify_args and classify_args[0] == tool:
            classify_args = classify_args[1:]
        return args if _tool_args_are_discovery(classify_args, tool=tool) else None
    if classify_args and classify_args[0] == tool:
        classify_args = classify_args[1:]
        return args if _tool_args_are_discovery(classify_args, tool=tool) else None
    return [tool, *args] if _tool_args_are_discovery(classify_args, tool=tool) else None


def _tool_args_are_discovery(args: list[str], *, tool: str | None = None) -> bool:
    if not args:
        return False
    if tool == "codex" and _codex_subcommand_args_are_discovery(args):
        return True
    return _option_only_args_are_discovery(args)


def _option_only_args_are_discovery(args: list[str]) -> bool:
    if not args:
        return False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in _DISCOVERY_FLAGS:
            return index == len(args) - 1
        if arg == "--" or not arg.startswith("-"):
            return False
        if "=" in arg:
            index += 1
            continue
        if (
            index + 1 < len(args)
            and args[index + 1] not in _DISCOVERY_FLAGS
            and not args[index + 1].startswith("-")
        ):
            index += 2
            continue
        index += 1
    return False


def _codex_subcommand_args_are_discovery(args: list[str]) -> bool:
    if not args or "--" in args:
        return False
    if args[0] == "help":
        return True
    if args[0].startswith("-"):
        return _option_only_args_are_discovery(args)
    for path in sorted(
        _CODEX_DISCOVERY_COMMAND_PATHS,
        key=len,
        reverse=True,
    ):
        if tuple(args[: len(path)]) == path:
            return _option_only_args_are_discovery(args[len(path):])
    return False


def _run_passthrough_discovery(tool: str, session_cwd: str, tool_args: list[str]) -> int:
    args = _discovery_passthrough_command(tool, tool_args)
    if args is None:
        raise ValueError("passthrough discovery command is not recognized")
    session_identity._validate_supported_shell_wrappers(args)
    session_identity._validate_downstream_cwd_flags(args)
    session_identity._validate_binding_env_unsets(args)
    session_identity._validate_final_launch_target(args)
    direct_args = session_identity._final_launch_target(args)
    if not direct_args:
        raise ValueError("passthrough discovery command is not recognized")
    resolved_executable = session_identity._resolved_trusted_vendor_binary(direct_args[0])
    result = subprocess.run(
        [resolved_executable, *direct_args[1:]],
        cwd=_normalized_path(session_cwd),
        check=False,
    )
    return result.returncode


def _build_launcher_argv(args: argparse.Namespace) -> list[str]:
    if args.handoff_dir:
        raise ValueError("agent_session manages handoff storage automatically")
    session_cwd = _normalized_path(args.session_cwd)
    approved_projects = _approved_projects_from_doc_basis_paths(
        session_cwd,
        args.doc_basis_paths,
    )
    worker_cwd = _normalized_path(
        args.worker_cwd or _default_worker_cwd(session_cwd, args.doc_basis_paths)
    )
    if (
        approved_projects
        and _project_name_for_worker_cwd(worker_cwd) not in approved_projects
    ):
        raise ValueError("worker_cwd must match approved doc-basis project")
    worker_repo_root = _normalized_path(session_identity._find_repo_root(worker_cwd))
    explicit_worktree_cwd = _normalized_path(args.worktree_cwd) if args.worktree_cwd else None
    worktree_cwd = explicit_worktree_cwd or worker_repo_root
    route_id = args.route_id or _default_route_id(args.task_id, worker_cwd)
    session_identity.validate_worktree_doc_mode(
        worker_cwd=worker_cwd,
        worktree_cwd=worktree_cwd,
        docs_source=args.docs_source,
        docs_revision=args.docs_revision,
        doc_mode=args.doc_mode,
        doc_basis_paths=args.doc_basis_paths,
    )
    if not args.docs_revision:
        raise ValueError("binding-first launch requires explicit --docs-revision approval token")
    normalized_doc_basis_paths = session_identity._validated_doc_basis_paths(
        worker_cwd,
        args.doc_basis_paths,
        require_explicit=True,
    )
    docs_revision = args.docs_revision
    doc_basis_id = _build_doc_basis_id(
        route_id=route_id,
        worker_cwd=worker_cwd,
        docs_source=args.docs_source,
        docs_revision=docs_revision,
        doc_mode=args.doc_mode,
        doc_basis_paths=normalized_doc_basis_paths,
    )

    launcher_argv = [args.tool]
    launcher_argv.extend(
        [
            "--session-cwd",
            session_cwd,
            "--worker-cwd",
            worker_cwd,
            "--worktree-cwd",
            worktree_cwd,
            "--task-id",
            args.task_id,
            "--route-id",
            route_id,
            "--doc-basis-id",
            doc_basis_id,
            "--docs-source",
            args.docs_source,
            "--docs-revision",
            docs_revision,
            "--doc-mode",
            args.doc_mode,
            *[
                value
                for rel_path in normalized_doc_basis_paths
                for value in ("--doc-basis-path", rel_path)
            ],
            *list(args.tool_args),
        ]
    )
    return launcher_argv


def _build_doc_basis_id(
    *,
    route_id: str,
    worker_cwd: str,
    docs_source: str,
    docs_revision: str,
    doc_mode: str,
    doc_basis_paths: tuple[str, ...] | list[str] | None,
) -> str:
    return session_identity.compute_doc_basis_id(
        route_id=route_id,
        worker_cwd=worker_cwd,
        docs_source=docs_source,
        docs_revision=docs_revision,
        doc_mode=doc_mode,
        doc_basis_paths=doc_basis_paths,
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    passthrough_start = argv.index("--") if "--" in argv else len(argv)
    launcher_argv = argv[:passthrough_start]
    tool_argv = argv[passthrough_start:]

    parser = argparse.ArgumentParser(
        description=(
            "Ergonomic agent/worker session launcher for Claude/Codex passthrough. "
            "Binding-first launch requires explicit --docs-revision approval and "
            "one or more approved --doc-basis-path entries."
        ),
        usage=(
            "%(prog)s {claude,codex} task_id "
            "[--route-id ROUTE_ID] "
            "[--worker-cwd WORKER_CWD] "
            "[--worktree-cwd WORKTREE_CWD] "
            "[--docs-source {root-canonical,branch-docs-approved}] "
            "[--doc-mode {root-canonical,branch-docs-approved}] "
            "--docs-revision DOCS_REVISION "
            "--doc-basis-path DOC_BASIS_PATH "
            "[--doc-basis-path DOC_BASIS_PATH ...] "
            "[-- <tool args...>]"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Notes:\n"
            "  - start_worker_session manages --session-cwd and handoff storage automatically.\n"
            "  - Pass repo-root-relative --doc-basis-path values for approved specs.\n"
            "  - Drifted worktrees must use --docs-source/--doc-mode branch-docs-approved.\n"
            "  - One-shot downstream commands such as claude -p or codex exec stay on the preflight-only path; they may leave context, but do not count as executable continuity proof."
            "\n"
            "  - Downstream codex -C/--cd is unsupported here because the launcher already fixes worker_cwd."
        ),
    )
    parser.add_argument("tool", choices=("claude", "codex"))
    parser.add_argument("task_id", help="Stable task slug/id for this agent or worker session")
    parser.add_argument("--handoff-dir", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--route-id", default=None)
    parser.add_argument(
        "--session-cwd",
        default=_default_session_cwd(),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--worker-cwd",
        default=None,
        help="Worker/project cwd. Defaults to the canonical project inferred from doc basis.",
    )
    parser.add_argument(
        "--worktree-cwd",
        default=None,
        help="Verified worktree root override. Defaults to worker_cwd git top-level.",
    )
    parser.add_argument(
        "--docs-source",
        default="root-canonical",
        choices=("root-canonical", "branch-docs-approved"),
        help="Approved doc source. Drifted worktrees must use branch-docs-approved.",
    )
    parser.add_argument(
        "--docs-revision",
        default=None,
        help="Required approval token from resolve_verified_docs_revision(...).",
    )
    parser.add_argument(
        "--doc-mode",
        default="root-canonical",
        choices=("root-canonical", "branch-docs-approved"),
        help="Approved doc mode paired with --docs-source.",
    )
    parser.add_argument(
        "--doc-basis-path",
        dest="doc_basis_paths",
        action="append",
        default=None,
        help=(
            "Required approved doc-basis path. Repeat for decision-log plus the task spec; "
            "prefer repo-root-relative paths."
        ),
    )
    args = parser.parse_args(launcher_argv)
    args.tool_args = tool_argv
    return args


def main(argv: Optional[list[str]] = None) -> int:
    try:
        args = _parse_args(sys.argv[1:] if argv is None else argv)
        if _discovery_passthrough_command(args.tool, list(args.tool_args)) is not None:
            return _run_passthrough_discovery(
                args.tool,
                args.session_cwd,
                list(args.tool_args),
            )
        launcher_argv = _build_launcher_argv(args)
        return session_launcher.main(launcher_argv)
    except ValueError as exc:
        return _emit_error(str(exc))
    except FileNotFoundError as exc:
        return _emit_file_error(str(exc))
    except OSError as exc:
        return _emit_error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
