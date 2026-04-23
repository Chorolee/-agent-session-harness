"""Hook dispatcher — single entry point for Claude/Codex command hooks.

stdin JSON → normalize → route (journal append, reduce, context inject).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from . import handoff
from . import session_identity


# Default handoff directory (relative to repo root)
_HANDOFF_DIR = ".claude/handoff"


def main(argv: list[str] | None = None) -> None:
    try:
        _main_inner(argv)
    except SystemExit:
        raise  # let argparse --help / errors through
    except Exception as exc:
        # fail-open: all hooks must exit 0
        print(f"harness dispatch error: {exc}", file=sys.stderr)
        sys.exit(0)


def _main_inner(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Harness hook dispatcher"
    )
    parser.add_argument("hook_event", help="Hook event name (e.g. SessionStart, Stop)")
    parser.add_argument("source_tool", help="Source tool: claude or codex")
    parser.add_argument(
        "--handoff-dir",
        default=None,
        help="Override handoff directory (default: <repo_root>/.claude/handoff)",
    )
    args = parser.parse_args(argv)

    # Read stdin JSON
    try:
        raw = sys.stdin.read()
        stdin_json = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, Exception):
        stdin_json = {}

    session_id = stdin_json.get("session_id", "unknown")

    # Normalize event
    event = handoff.normalize_event(
        hook_event=args.hook_event,
        stdin_json=stdin_json,
        source_tool=args.source_tool,
        session_id=session_id,
    )

    repo_root = event.get("repo_root", ".")
    env_handoff_dir = os.environ.get(session_identity.HARNESS_HANDOFF_DIR_ENV)
    handoff_dir = (
        Path(args.handoff_dir)
        if args.handoff_dir
        else Path(repo_root) / _HANDOFF_DIR
    )

    # 0. Enrich event with git snapshot (Stop/StopFailure for journal, SessionStart for validation)
    if args.hook_event in ("Stop", "StopFailure", "SessionStart"):
        _enrich_git_snapshot(event, repo_root)

    facts = event.get("facts") or {}
    if (
        not args.handoff_dir
        and env_handoff_dir
        and session_identity.env_handoff_dir_matches_event(
            Path(env_handoff_dir),
            event,
            git_head=facts.get("git_head"),
        )
    ):
        handoff_dir = Path(env_handoff_dir)

    # Session directory
    session_dir = handoff_dir / "sessions" / args.source_tool

    # 0b. Merge any explicit active session-identity binding for the first
    # SessionStart only. The persisted SessionStart identity is enough for
    # later events in the same journal.
    if args.hook_event == "SessionStart":
        session_identity.enrich_event_identity(
            event,
            handoff_dir,
            git_head=facts.get("git_head"),
            consume=False,
        )
        handoff.stamp_session_start_identity_validation(event)

    # 1. Always append to journal (fail-open)
    appended = handoff.append_journal(session_dir, session_id, event)

    if args.hook_event == "SessionStart" and appended:
        facts = event.get("facts") or {}
        if (
            facts.get("identity_validation_status") == "validated"
            and facts.get("binding_launch_mode") not in {"one-shot", "manual"}
        ):
            acknowledged = session_identity.acknowledge_event_identity(
                event,
                handoff_dir,
                git_head=facts.get("git_head"),
            )
            if acknowledged:
                ack_event = handoff.normalize_event(
                    "IdentityAcknowledged",
                    {
                        "cwd": event.get("cwd"),
                        "git_head": facts.get("git_head"),
                        "git_status_fingerprint": facts.get("git_status_fingerprint"),
                        "dirty_files": facts.get("dirty_files", []),
                        "file_paths": facts.get("file_paths", []),
                        **{
                            field: facts.get(field)
                            for field in (
                                "task_id",
                                "route_id",
                                "worker_cwd",
                                "worktree_cwd",
                                "doc_basis_id",
                                "docs_source",
                                "docs_revision",
                                "doc_mode",
                            )
                        },
                        "doc_basis_paths": facts.get("doc_basis_paths"),
                    },
                    args.source_tool,
                    session_id,
                )
                ack_facts = ack_event.setdefault("facts", {})
                ack_facts["identity_acknowledged"] = True
                ack_facts["identity_source"] = "binding"
                ack_facts["identity_binding_id"] = facts.get("identity_binding_id")
                ack_facts["ack_for_event_id"] = event.get("event_id")
                if not handoff.append_journal(session_dir, session_id, ack_event):
                    session_identity.revoke_acknowledged_binding(
                        handoff_dir,
                        str(facts.get("identity_binding_id")),
                        session_id=session_id,
                        event_id=event.get("event_id"),
                        reason="ack-journal-append-failed",
                    )
                    event_facts = event.setdefault("facts", {})
                    event_facts["identity_acknowledged"] = False
                    event_facts["binding_state"] = "rejected"

    # 2. On reduce-triggering events, update project resume cache
    if args.hook_event in ("Stop", "StopFailure", "PostCompact"):
        _try_reduce(event, handoff_dir)

    # 3. On SessionStart, validate/rebuild and emit context
    if args.hook_event == "SessionStart":
        output = _handle_session_start(event, handoff_dir)
        if output:
            sys.stdout.write(output)


def _enrich_git_snapshot(event: dict, repo_root: str) -> None:
    """Collect git state and inject into event facts for Stop/StopFailure."""
    import subprocess
    facts = event.get("facts", {})
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=5,
        )
        if head.returncode == 0:
            facts["git_head"] = head.stdout.strip()
    except Exception:
        pass

    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root, capture_output=True, text=True, timeout=5,
        )
        if status.returncode == 0:
            import hashlib
            facts["git_status_fingerprint"] = "sha256:" + hashlib.sha256(
                status.stdout.encode()
            ).hexdigest()[:16]
            dirty = [
                line[3:] for line in status.stdout.splitlines() if line.strip()
            ]
            facts["dirty_files"] = dirty
    except Exception:
        pass

    event["facts"] = facts


def _try_reduce(event: dict, handoff_dir: Path) -> None:
    """Best-effort reduce for affected projects."""
    affected = event.get("affected_projects", [])
    scope_key = event.get("scope_key", "_repo")

    projects_to_reduce = set(affected)
    if scope_key != "_repo":
        projects_to_reduce.add(scope_key)
    if not projects_to_reduce:
        projects_to_reduce.add("_repo")

    for project_key in projects_to_reduce:
        try:
            resume = handoff.reduce_project(project_key, handoff_dir)
            if handoff.resume_cacheable(resume):
                _write_resume_cache(handoff_dir, project_key, resume)
        except Exception:
            pass  # fail-open


def _handle_session_start(event: dict, handoff_dir: Path) -> str:
    """Load or rebuild resume, validate, render context."""
    scope_key = event.get("scope_key", "_repo")
    selection_clue = handoff.selection_clue_from_event(event)

    if handoff.session_start_requires_identity_block(event):
        return ""

    # Try to load cached resume
    resume = _load_resume_cache(handoff_dir, scope_key)

    # Get current git fingerprint from event facts
    facts = event.get("facts") or {}
    git_fp = facts.get("git_status_fingerprint")
    git_head = facts.get("git_head")

    if resume:
        validity = handoff.validate_resume(
            resume,
            handoff_dir,
            git_fp,
            git_head,
            selection_clue=selection_clue,
        )
        if validity == "valid" and not handoff.newer_matching_journal_exists(
            scope_key,
            handoff_dir,
            resume,
            selection_clue=selection_clue,
            exclude_session=(event.get("source_tool", ""), event.get("session_id", "")),
        ):
            response_resume = _resume_for_current_launch(event, resume)
            rendered = handoff.render_context(response_resume)
            if (
                response_resume is resume
                and rendered != resume.get("rendered_context")
                and not selection_clue
            ):
                resume["rendered_context"] = rendered
                _write_resume_cache(handoff_dir, scope_key, resume)
            return handoff.emit_response(
                event.get("source_tool", ""),
                "SessionStart",
                {"rendered_context": rendered},
            )

    # Rebuild from journal
    try:
        resume = handoff.reduce_project(
            scope_key,
            handoff_dir,
            selection_clue=selection_clue,
            exclude_session=(event.get("source_tool", ""), event.get("session_id", "")),
        )
        rebuilt_validity = handoff.validate_resume(
            resume,
            handoff_dir,
            git_fp,
            git_head,
            selection_clue=selection_clue,
        )
        if rebuilt_validity == "stale":
            return ""
        if resume.get("validity") not in ("unavailable",):
            if (
                not selection_clue
                and rebuilt_validity == resume.get("validity")
                and handoff.resume_cacheable(resume)
            ):
                _write_resume_cache(handoff_dir, scope_key, resume)
            response_resume = _resume_for_current_launch(event, resume)
            rendered = response_resume.get("rendered_context", "")
            if not rendered:
                rendered = handoff.render_context(response_resume)
            return handoff.emit_response(
                event.get("source_tool", ""),
                "SessionStart",
                {"rendered_context": rendered},
            )
    except Exception:
        pass  # fail-open

    return ""


def _load_resume_cache(handoff_dir: Path, project_key: str) -> dict | None:
    """Load a resume.json from the projects/ cache directory."""
    path = handoff_dir / "projects" / f"{project_key}.resume.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _resume_for_current_launch(event: dict, resume: dict) -> dict:
    facts = event.get("facts") or {}
    if not _current_launch_has_identity_metadata(facts):
        return _preflight_resume(resume, "thin_session_preflight_only")
    if (
        facts.get("identity_source") != "binding"
        or facts.get("binding_launch_mode") not in {"one-shot", "manual"}
    ):
        return resume
    warning = (
        "manual_bind_preflight_only"
        if facts.get("binding_launch_mode") == "manual"
        else "one_shot_preflight_only"
    )
    return _preflight_resume(resume, warning)


def _current_launch_has_identity_metadata(facts: dict) -> bool:
    for field in handoff._IDENTITY_METADATA_FIELDS:
        value = facts.get(field)
        if value not in (None, "", []):
            return True
    return False


def _preflight_resume(resume: dict, warning: str) -> dict:
    adjusted = dict(resume)
    warnings = list(adjusted.get("warnings") or [])
    if warning not in warnings:
        warnings.append(warning)
    adjusted["resume_mode"] = "resume-preflight"
    adjusted["can_auto_resume"] = False
    adjusted["can_execute_worker"] = False
    adjusted["warnings"] = warnings
    adjusted["rendered_context"] = ""
    return adjusted


def _write_resume_cache(handoff_dir: Path, project_key: str, resume: dict) -> None:
    """Atomically write a resume cache: tmp + fsync + rename."""
    projects_dir = handoff_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    target = projects_dir / f"{project_key}.resume.json"

    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(projects_dir), suffix=".tmp", prefix=".resume-"
        )
        try:
            data = json.dumps(resume, ensure_ascii=False, indent=2).encode("utf-8")
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(tmp_path, str(target))
    except Exception:
        # fail-open: clean up temp file if possible
        try:
            os.unlink(tmp_path)  # type: ignore[possibly-undefined]
        except Exception:
            pass


if __name__ == "__main__":
    main()
