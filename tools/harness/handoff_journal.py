"""Journal I/O helpers for harness handoff state."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class SessionHeader:
    source_tool: str
    session_id: str
    scope_key: str | None
    affected_projects: tuple[str, ...]
    last_event_id: str | None
    last_ts: str | None
    state: str | None
    task_id: str | None
    route_id: str | None
    worker_cwd: str | None
    worktree_cwd: str | None
    doc_basis_id: str | None
    docs_source: str | None
    docs_revision: str | None
    doc_mode: str | None
    doc_basis_paths: tuple[str, ...]
    identity_validation_status: str | None
    identity_acknowledged: bool | None
    identity_binding_id: str | None
    binding_launch_mode: str | None
    last_prompt_excerpt: str | None
    last_assistant_excerpt: str | None
    last_failure: str | None
    git_head: str | None
    git_status_fingerprint: str | None
    dirty_files: tuple[str, ...]


def append_journal(
    session_dir: str | Path,
    session_id: str,
    event: dict[str, Any],
) -> bool:
    """Append *event* as a single JSONL line to the session journal."""
    try:
        session_dir = Path(session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        journal_path = session_dir / f"{session_id}.jsonl"
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        fd = os.open(str(journal_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        return True
    except Exception:
        return False


def load_session_header(journal_path: str | Path) -> SessionHeader | None:
    """Load bounded metadata for one session journal."""
    path = Path(journal_path)
    events = _parse_jsonl(path)
    if not events:
        return None
    return _session_header_from_events(path.parent.name, path.stem, events)


def load_source_session_headers(
    journal_dir: str | Path,
    source_sessions: list[str] | None,
) -> list[SessionHeader]:
    """Load bounded metadata for explicit source session identifiers."""
    headers: list[SessionHeader] = []
    if not source_sessions:
        return headers

    sessions_root = Path(journal_dir) / "sessions"
    for source_session in source_sessions:
        if ":" not in source_session:
            continue
        tool, session_id = source_session.split(":", 1)
        header = load_session_header(sessions_root / tool / f"{session_id}.jsonl")
        if header is not None:
            headers.append(header)
    return headers


def iter_project_session_headers(
    journal_dir: str | Path,
    project_key: str,
    *,
    selection_clue: dict[str, Any] | None = None,
    exclude_session: tuple[str, str] | None = None,
) -> Iterator[SessionHeader]:
    """Yield bounded session metadata relevant to a project key."""
    sessions_root = Path(journal_dir) / "sessions"
    if not sessions_root.is_dir():
        return

    for tool_dir in sessions_root.iterdir():
        if not tool_dir.is_dir():
            continue
        source_tool = tool_dir.name
        for journal_path in tool_dir.glob("*.jsonl"):
            session_id = journal_path.stem
            if exclude_session == (source_tool, session_id):
                continue
            header = load_session_header(journal_path)
            if header is None:
                continue
            if not _header_matches_project(header, project_key):
                continue
            if selection_clue and not _header_matches_selection_clue(
                header,
                selection_clue,
            ):
                continue
            yield header


def _load_all_sessions(
    journal_dir: Path,
) -> dict[tuple[str, str], list[dict]]:
    """Load all session journals under journal_dir/sessions/<tool>/<id>.jsonl."""
    result: dict[tuple[str, str], list[dict]] = {}
    sessions_root = journal_dir / "sessions"
    if not sessions_root.is_dir():
        return result

    for tool_dir in sessions_root.iterdir():
        if not tool_dir.is_dir():
            continue
        source_tool = tool_dir.name
        for jfile in tool_dir.glob("*.jsonl"):
            session_id = jfile.stem
            events = _parse_jsonl(jfile)
            if events:
                result[(source_tool, session_id)] = events

    return result


def _parse_jsonl(path: Path) -> list[dict]:
    """Parse JSONL, discarding the last line if malformed."""
    events: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return events

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                continue
            print(
                f"harness: malformed journal line {i+1} in {path} (skipped)",
                file=sys.stderr,
            )
            continue
    return events


def _event_id_exists(
    journal_dir: Path,
    event_id: str,
    source_sessions: list[str] | None = None,
) -> bool:
    """Check if an event_id exists in journal files."""
    sessions_root = journal_dir / "sessions"
    if not sessions_root.is_dir():
        return False

    if source_sessions:
        for ss in source_sessions:
            if ":" in ss:
                tool, sid = ss.split(":", 1)
                jfile = sessions_root / tool / f"{sid}.jsonl"
                if jfile.is_file():
                    for e in _parse_jsonl(jfile):
                        if e.get("event_id") == event_id:
                            return True
        return False

    for tool_dir in sessions_root.iterdir():
        if not tool_dir.is_dir():
            continue
        for jfile in tool_dir.glob("*.jsonl"):
            for e in _parse_jsonl(jfile):
                if e.get("event_id") == event_id:
                    return True
    return False


def _load_source_session_records(
    journal_dir: Path,
    source_sessions: list[str] | None,
) -> list[tuple[str, str, list[dict[str, Any]]]]:
    if not source_sessions:
        return []

    records: list[tuple[str, str, list[dict[str, Any]]]] = []
    sessions_root = journal_dir / "sessions"
    for source_session in source_sessions:
        if ":" not in source_session:
            continue
        tool, session_id = source_session.split(":", 1)
        journal_path = sessions_root / tool / f"{session_id}.jsonl"
        if not journal_path.is_file():
            continue
        events = _parse_jsonl(journal_path)
        if events:
            records.append((tool, session_id, events))
    return records


def _session_header_from_events(
    source_tool: str,
    session_id: str,
    events: list[dict[str, Any]],
) -> SessionHeader:
    last_event = events[-1]
    latest_facts: dict[str, Any] = {}
    latest_text: dict[str, Any] = {}
    latest_scope = None
    affected_projects: set[str] = set()
    last_prompt = None
    last_assistant = None
    last_failure = None
    durable_identity_acknowledged = False
    durable_ack_binding_id = None

    for event in events:
        facts = event.get("facts") or {}
        text = event.get("text") or {}
        if event.get("scope_key"):
            latest_scope = event.get("scope_key")
        affected_projects.update(str(p) for p in event.get("affected_projects") or [])
        if event.get("hook_event") == "SessionStart":
            latest_facts = dict(facts)
        elif event.get("hook_event") == "IdentityAcknowledged":
            if facts.get("identity_acknowledged") is True:
                durable_identity_acknowledged = True
                durable_ack_binding_id = facts.get("identity_binding_id")
            latest_facts.update(
                {
                    k: v
                    for k, v in facts.items()
                    if v not in (None, "") and k != "identity_acknowledged"
                }
            )
        else:
            latest_facts.update(
                {
                    k: v
                    for k, v in facts.items()
                    if v not in (None, "") and k != "identity_acknowledged"
                }
            )
        latest_text = dict(text)
        if text.get("prompt_excerpt"):
            last_prompt = text.get("prompt_excerpt")
        if text.get("assistant_excerpt"):
            last_assistant = text.get("assistant_excerpt")
        if facts.get("error_kind") or facts.get("error_message"):
            last_failure = facts.get("error_kind") or facts.get("error_message")

    doc_basis_paths = latest_facts.get("doc_basis_paths") or []
    if isinstance(doc_basis_paths, str):
        doc_basis_paths = [doc_basis_paths]
    dirty_files = latest_facts.get("dirty_files") or []

    return SessionHeader(
        source_tool=source_tool,
        session_id=session_id,
        scope_key=latest_scope,
        affected_projects=tuple(sorted(affected_projects)),
        last_event_id=last_event.get("event_id"),
        last_ts=last_event.get("ts"),
        state=None,
        task_id=latest_facts.get("task_id"),
        route_id=latest_facts.get("route_id"),
        worker_cwd=latest_facts.get("worker_cwd"),
        worktree_cwd=latest_facts.get("worktree_cwd"),
        doc_basis_id=latest_facts.get("doc_basis_id"),
        docs_source=latest_facts.get("docs_source"),
        docs_revision=latest_facts.get("docs_revision"),
        doc_mode=latest_facts.get("doc_mode"),
        doc_basis_paths=tuple(str(p) for p in doc_basis_paths),
        identity_validation_status=latest_facts.get("identity_validation_status"),
        identity_acknowledged=(
            True
            if durable_identity_acknowledged
            else latest_facts.get("identity_acknowledged")
        ),
        identity_binding_id=durable_ack_binding_id or latest_facts.get("identity_binding_id"),
        binding_launch_mode=latest_facts.get("binding_launch_mode"),
        last_prompt_excerpt=last_prompt or latest_text.get("prompt_excerpt"),
        last_assistant_excerpt=last_assistant or latest_text.get("assistant_excerpt"),
        last_failure=last_failure,
        git_head=latest_facts.get("git_head"),
        git_status_fingerprint=latest_facts.get("git_status_fingerprint"),
        dirty_files=tuple(str(p) for p in dirty_files),
    )


def _header_matches_project(header: SessionHeader, project_key: str) -> bool:
    return header.scope_key == project_key or project_key in header.affected_projects


def _header_matches_selection_clue(
    header: SessionHeader,
    selection_clue: dict[str, Any],
) -> bool:
    for field in (
        "task_id",
        "route_id",
        "worker_cwd",
        "worktree_cwd",
        "doc_basis_id",
    ):
        expected = selection_clue.get(field)
        if expected not in (None, "") and getattr(header, field) != expected:
            return False
    return True
