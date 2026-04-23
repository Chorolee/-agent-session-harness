"""Session liveness and dirty-path heuristics for handoff resumes."""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from .handoff_events import _scope_key_from_path


CODEX_SESSION_TTL_HOURS = 2

_CLAUDE_TERMINAL_EVENTS = {"Stop", "StopFailure", "SessionEnd"}
_CODEX_TERMINAL_EVENTS = {"Stop"}


def session_liveness_policy(
    source_tool: str,
    session_events: list[dict[str, Any]],
) -> str:
    """Determine session state: 'open', 'closed', or 'expired'."""
    if not session_events:
        return "open"

    event_types = {e.get("hook_event") for e in session_events}

    if source_tool == "claude":
        if event_types & _CLAUDE_TERMINAL_EVENTS:
            return "closed"
        return "open"

    if event_types & _CODEX_TERMINAL_EVENTS:
        return "closed"

    last_ts = session_events[-1].get("ts", "")
    if last_ts:
        try:
            last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - last_dt > timedelta(
                hours=CODEX_SESSION_TTL_HOURS
            ):
                return "expired"
        except (ValueError, TypeError):
            pass
    return "open"


def _session_looks_in_progress(
    source_tool: str,
    state: str,
    session_events: list[dict[str, Any]],
) -> bool:
    """Best-effort signal that a closed/expired session may still need resume."""
    meaningful_terminal_events = (
        {"Stop", "StopFailure"} if source_tool == "claude" else _CODEX_TERMINAL_EVENTS
    )
    touched_paths = _session_touched_paths(session_events)
    baseline_known, baseline_dirty_paths = _session_dirty_baseline(session_events)
    saw_session_end = False

    for event in reversed(session_events):
        hook_event = event.get("hook_event")
        if source_tool == "claude" and hook_event == "SessionEnd":
            saw_session_end = True
            continue
        if hook_event == "StopFailure":
            return True
        facts = event.get("facts") or {}
        if facts.get("error_kind") or facts.get("error_message"):
            return True
        if _terminal_dirty_is_session_owned(
            event,
            touched_paths,
            baseline_known,
            baseline_dirty_paths,
        ):
            return True
        if hook_event in meaningful_terminal_events:
            return False

    return saw_session_end


def _last_prompt_excerpt(session_events: list[dict[str, Any]]) -> Optional[str]:
    last_prompt: Optional[str] = None
    for event in session_events:
        text = event.get("text") or {}
        if text.get("prompt_excerpt"):
            last_prompt = text["prompt_excerpt"]
    return last_prompt


def _candidate_state(record: dict[str, Any]) -> str:
    if record["state"] == "open":
        return "open"
    if record["state"] == "expired":
        return "expired"
    if record["unfinished"]:
        return "paused"
    return "closed"


def _normalize_repo_path(path_value: str, repo_root: Optional[str]) -> Optional[str]:
    """Normalize a path into a repo-relative POSIX-style string when possible."""
    if not path_value:
        return None

    if repo_root:
        try:
            root = Path(repo_root).resolve()
            path = Path(path_value)
            if path.is_absolute():
                return path.resolve().relative_to(root).as_posix()
        except Exception:
            pass

    normalized = path_value.replace(os.sep, "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.strip("/")
    return normalized or None


def _session_touched_paths(session_events: list[dict[str, Any]]) -> set[str]:
    touched_paths: set[str] = set()
    for event in session_events:
        repo_root = event.get("repo_root")
        facts = event.get("facts") or {}
        for file_path in facts.get("file_paths", []):
            normalized = _normalize_repo_path(file_path, repo_root)
            if normalized:
                touched_paths.add(normalized)
    return touched_paths


def _session_dirty_baseline(session_events: list[dict[str, Any]]) -> tuple[bool, set[str]]:
    for event in session_events:
        if event.get("hook_event") != "SessionStart":
            continue
        repo_root = event.get("repo_root")
        facts = event.get("facts") or {}
        dirty_paths = {
            normalized
            for file_path in facts.get("dirty_files", [])
            if (normalized := _normalize_repo_path(file_path, repo_root))
        }
        return True, dirty_paths
    return False, set()


def _terminal_dirty_is_session_owned(
    event: dict[str, Any],
    touched_paths: set[str],
    baseline_known: bool,
    baseline_dirty_paths: set[str],
) -> bool:
    repo_root = event.get("repo_root")
    facts = event.get("facts") or {}
    dirty_paths = {
        normalized
        for file_path in facts.get("dirty_files", [])
        if (normalized := _normalize_repo_path(file_path, repo_root))
    }
    project_key = event.get("scope_key") or "_repo"
    dirty_touched_paths = dirty_paths & touched_paths
    if project_key == "_repo":
        if dirty_touched_paths:
            return True
        return False
    if dirty_touched_paths and (not repo_root or not event.get("cwd")):
        return True
    if dirty_touched_paths and _project_scoped_dirty_paths(
        dirty_touched_paths,
        repo_root,
        project_key,
        cwd=event.get("cwd"),
    ):
        return True
    if project_key == "_repo":
        return False
    if baseline_known and _project_scoped_dirty_paths(
        dirty_paths - baseline_dirty_paths,
        repo_root,
        project_key,
        cwd=event.get("cwd"),
    ):
        return True
    return False


def _project_scoped_dirty_paths(
    paths: set[str],
    repo_root: Optional[str],
    project_key: str,
    *,
    cwd: Optional[str] = None,
) -> set[str]:
    if not paths:
        return set()
    if project_key == "_repo":
        return set()

    if not repo_root:
        return set()

    try:
        root = Path(repo_root).resolve()
    except Exception:
        return set()

    cwd_path: Optional[Path] = None
    if cwd:
        try:
            cwd_path = Path(cwd).resolve()
        except Exception:
            cwd_path = None

    scoped_paths: set[str] = set()
    prefix = f"{project_key}/"
    for path_value in paths:
        if path_value == project_key or path_value.startswith(prefix):
            scoped_paths.add(path_value)
            continue
        raw_path = Path(path_value)
        for candidate in _candidate_paths_for_project_scope(raw_path, root, cwd_path):
            if _scope_key_from_path(str(candidate), str(root)) == project_key:
                scoped_paths.add(path_value)
                break
    return scoped_paths


def _candidate_paths_for_project_scope(
    raw_path: Path,
    repo_root: Path,
    cwd_path: Optional[Path],
) -> tuple[Path, ...]:
    if raw_path.is_absolute():
        return (raw_path.resolve(),)

    repo_candidate = (repo_root / raw_path).resolve()
    path_text = raw_path.as_posix()
    first_component = path_text.split("/", 1)[0]
    repo_entry = repo_root / first_component
    prefer_repo_candidate = (
        path_text.startswith("../") is False
        and (
            repo_candidate.exists()
            or repo_entry.exists()
            or (
                first_component.startswith(".")
                and first_component not in (".", "..")
            )
        )
    )
    if prefer_repo_candidate:
        return (repo_candidate,)
    if not cwd_path:
        return (repo_candidate,)
    return ((cwd_path / raw_path).resolve(), repo_candidate)
