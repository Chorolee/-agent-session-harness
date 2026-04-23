"""Reducer and validator helpers for harness handoff resumes."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import session_identity
from .handoff_identity import (
    _empty_identity,
    _execution_gate,
    _has_selection_identity,
    _identity_matches_clue,
    _normalize_selection_clue,
    _resume_explicit_identity,
    _resume_has_current_execution_metadata,
    _resume_identity,
    _resume_mode,
    _session_binding_launch_mode,
    _session_explicit_identity,
    _session_identity,
    _session_identity_status,
)
from .handoff_journal import (
    _event_id_exists,
    _load_all_sessions,
    _load_source_session_records,
)
from .handoff_liveness import (
    _candidate_state,
    _last_prompt_excerpt,
    _session_looks_in_progress,
    session_liveness_policy,
)
from .handoff_render import render_context
from .handoff_types import ResumeCandidate, ResumeDecision


SCHEMA_VERSION = 2


def reduce_project(
    project_key: str,
    journal_dir: str | Path,
    selection_clue: Optional[dict[str, Any]] = None,
    exclude_session: Optional[tuple[str, str]] = None,
) -> dict[str, Any]:
    """Build a per-project Resume dict from journal files."""
    journal_dir = Path(journal_dir)
    sessions = _load_all_sessions(journal_dir)
    selection_clue = _normalize_selection_clue(selection_clue)
    relevant = _collect_relevant_records(
        sessions,
        project_key,
        selection_clue=selection_clue,
        exclude_session=exclude_session,
    )

    if not relevant:
        return _empty_resume(project_key, "unavailable")

    return _resume_from_relevant_records(
        project_key,
        journal_dir,
        relevant,
        selection_clue=selection_clue,
    )


def _collect_relevant_records(
    sessions: dict[tuple[str, str], list[dict]],
    project_key: str,
    *,
    selection_clue: Optional[dict[str, Any]],
    exclude_session: Optional[tuple[str, str]],
) -> list[dict[str, Any]]:
    relevant: list[dict[str, Any]] = []
    for (source_tool, session_id), events in sessions.items():
        if exclude_session == (source_tool, session_id):
            continue
        if not _session_matches_project_selection(
            events,
            project_key,
            selection_clue=selection_clue,
        ):
            continue
        identity = _session_identity(events)
        explicit_identity = _session_explicit_identity(events)
        if selection_clue and not _identity_matches_clue(identity, selection_clue):
            continue
        state = session_liveness_policy(source_tool, events)
        relevant.append(
            {
                "source_tool": source_tool,
                "session_id": session_id,
                "events": events,
                "state": state,
                "identity": identity,
                "selection_identity": explicit_identity,
                "explicit_identity": explicit_identity,
                "has_selection_identity": _has_selection_identity(explicit_identity),
                "unfinished": (
                    state == "open"
                    or _session_looks_in_progress(source_tool, state, events)
                ),
                "last_ts": events[-1].get("ts", ""),
            }
        )
    return relevant


def _resume_from_relevant_records(
    project_key: str,
    journal_dir: Path,
    relevant: list[dict[str, Any]],
    *,
    selection_clue: Optional[dict[str, Any]],
) -> dict[str, Any]:
    grouped = _group_session_records(relevant)
    unfinished_groups = [group for group in grouped if group.get("unfinished")]
    if len(unfinished_groups) >= 2:
        return _build_ambiguous_resume(
            project_key,
            unfinished_groups,
            "multiple_unfinished_candidates",
            journal_dir,
            selection_clue,
        )
    if len(unfinished_groups) == 1:
        best_record = unfinished_groups[0]["best_record"]
        return _build_resume(
            project_key,
            journal_dir,
            best_record["source_tool"],
            best_record["session_id"],
            best_record["events"],
        )

    identity_groups = [
        group for group in grouped if group.get("has_selection_identity")
    ]
    thin_groups = [
        group for group in grouped if not group.get("has_selection_identity")
    ]

    if selection_clue:
        if len(grouped) >= 2:
            return _build_ambiguous_resume(
                project_key,
                grouped,
                "multiple_matches_for_selection_clue",
                journal_dir,
                selection_clue,
            )
        best_record = grouped[0]["best_record"]
        return _build_resume(
            project_key,
            journal_dir,
            best_record["source_tool"],
            best_record["session_id"],
            best_record["events"],
        )

    if len(identity_groups) >= 2:
        return _build_ambiguous_resume(
            project_key,
            identity_groups,
            "multiple_identity_candidates",
            journal_dir,
        )

    if identity_groups and thin_groups:
        best_record = identity_groups[0]["best_record"]
        resume = _build_resume(
            project_key,
            journal_dir,
            best_record["source_tool"],
            best_record["session_id"],
            best_record["events"],
        )
        resume["warnings"].append("mixed_thin_history_preferred_explicit_identity")
        resume["rendered_context"] = render_context(resume)
        return resume
    elif len(identity_groups) == 1:
        best_record = identity_groups[0]["best_record"]
    else:
        best_record = max(relevant, key=lambda record: record["last_ts"])
    return _build_resume(
        project_key,
        journal_dir,
        best_record["source_tool"],
        best_record["session_id"],
        best_record["events"],
    )


def validate_resume(
    resume: dict[str, Any],
    journal_dir: str | Path,
    git_fingerprint: Optional[str],
    git_head: Optional[str] = None,
    selection_clue: Optional[dict[str, Any]] = None,
) -> str:
    """Validate a resume cache against current state."""
    if not resume or resume.get("validity") == "unavailable":
        return "unavailable"

    if resume.get("validity") == "ambiguous":
        return "ambiguous"

    journal_dir = Path(journal_dir)
    if not _validate_resume_provenance(resume, journal_dir):
        return "unavailable"
    if not _validate_resume_execution_metadata(resume):
        return "stale"
    if _resume_git_basis_stale(resume, git_fingerprint, git_head):
        return "stale"
    if _resume_selection_clue_stale(resume, selection_clue):
        return "stale"
    if (
        resume.get("identity_status") == "explicit_valid"
        and not _cached_explicit_identity_still_proven(resume, journal_dir)
    ):
        return "stale"
    if not _validate_resume_doc_basis(resume):
        return "stale"
    return "valid"


def _validate_resume_provenance(resume: dict[str, Any], journal_dir: Path) -> bool:
    provenance = resume.get("provenance", {})
    last_event_id = provenance.get("last_event_id")
    source_sessions = provenance.get("source_sessions")
    if not last_event_id:
        return True
    return _event_id_exists(journal_dir, last_event_id, source_sessions)


def _validate_resume_execution_metadata(resume: dict[str, Any]) -> bool:
    if resume.get("v") != SCHEMA_VERSION:
        return False
    if not _resume_has_current_execution_metadata(resume):
        return False
    return resume.get("identity_status") != "producer_invalid"


def _resume_git_basis_stale(
    resume: dict[str, Any],
    git_fingerprint: Optional[str],
    git_head: Optional[str],
) -> bool:
    git_basis = resume.get("git_basis") or {}
    cached_head = git_basis.get("head")
    if git_head and cached_head and git_head != cached_head:
        return True
    cached_fp = git_basis.get("status_fingerprint")
    return bool(git_fingerprint and cached_fp and git_fingerprint != cached_fp)


def _resume_selection_clue_stale(
    resume: dict[str, Any],
    selection_clue: Optional[dict[str, Any]],
) -> bool:
    selection_clue = _normalize_selection_clue(selection_clue)
    return bool(
        selection_clue
        and not _identity_matches_clue(_resume_identity(resume), selection_clue)
    )


def _validate_resume_doc_basis(resume: dict[str, Any]) -> bool:
    task_identity = resume.get("task_identity") or {}
    doc_basis = resume.get("doc_basis") or {}
    doc_basis_paths = doc_basis.get("doc_basis_paths")
    if resume.get("identity_status") == "explicit_valid" and not doc_basis_paths:
        return False
    if doc_basis_paths:
        try:
            session_identity.validate_worktree_doc_mode(
                worker_cwd=str(task_identity.get("worker_cwd") or ""),
                worktree_cwd=str(task_identity.get("worktree_cwd") or ""),
                docs_source=str(doc_basis.get("docs_source") or ""),
                docs_revision=str(doc_basis.get("docs_revision") or ""),
                doc_mode=str(doc_basis.get("doc_mode") or ""),
                doc_basis_paths=doc_basis_paths,
            )
            doc_basis_valid, _expected_doc_basis_id = session_identity.validate_doc_basis(
                route_id=str(task_identity.get("route_id") or ""),
                worker_cwd=str(task_identity.get("worker_cwd") or ""),
                doc_basis_id=str(doc_basis.get("doc_basis_id") or ""),
                docs_source=str(doc_basis.get("docs_source") or ""),
                docs_revision=str(doc_basis.get("docs_revision") or ""),
                doc_mode=str(doc_basis.get("doc_mode") or ""),
                doc_basis_paths=doc_basis_paths,
            )
        except (ValueError, OSError):
            return False
        if not doc_basis_valid:
            return False
    return True


def _task_group_key(identity: dict[str, Any]) -> Optional[tuple[str, ...]]:
    task_id = identity.get("task_id")
    if task_id:
        return (
            "task_id",
            task_id,
            identity.get("route_id") or "",
            identity.get("worker_cwd") or "",
            identity.get("worktree_cwd") or "",
            identity.get("doc_basis_id") or "",
        )

    if not _has_selection_identity(identity):
        return None

    return (
        "composite",
        identity.get("route_id") or "",
        identity.get("worker_cwd") or "",
        identity.get("worktree_cwd") or "",
        identity.get("doc_basis_id") or "",
    )


def _group_session_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for record in records:
        key = _task_group_key(record["selection_identity"])
        if key is None:
            key = ("session", record["source_tool"], record["session_id"])
        grouped.setdefault(key, []).append(record)

    groups: list[dict[str, Any]] = []
    for key, members in grouped.items():
        unfinished_members = [member for member in members if member.get("unfinished")]
        finished_members = [member for member in members if not member.get("unfinished")]
        if len(unfinished_members) > 1:
            for member in unfinished_members:
                groups.append(
                    {
                        "key": (
                            *key,
                            "session",
                            member["source_tool"],
                            member["session_id"],
                        ),
                        "records": [member],
                        "best_record": member,
                        "unfinished": True,
                        "has_selection_identity": member["has_selection_identity"],
                    }
                )
            if finished_members:
                best_finished = max(
                    finished_members,
                    key=lambda record: record["last_ts"],
                )
                groups.append(
                    {
                        "key": key,
                        "records": finished_members,
                        "best_record": best_finished,
                        "unfinished": False,
                        "has_selection_identity": best_finished["has_selection_identity"],
                    }
                )
            continue
        best_record = (
            unfinished_members[0]
            if len(unfinished_members) == 1
            else max(members, key=lambda record: record["last_ts"])
        )
        groups.append(
            {
                "key": key,
                "records": members,
                "best_record": best_record,
                "unfinished": bool(unfinished_members),
                "has_selection_identity": best_record["has_selection_identity"],
            }
        )
    return sorted(groups, key=lambda group: group["best_record"]["last_ts"], reverse=True)


def _build_candidate(group: dict[str, Any], journal_dir: str | Path) -> ResumeCandidate:
    record = group["best_record"]
    identity = record["identity"]
    explicit_identity = record.get("explicit_identity") or _empty_identity()
    return ResumeCandidate(
        task_id=identity.get("task_id"),
        route_id=identity.get("route_id"),
        worktree_cwd=identity.get("worktree_cwd") or identity.get("worker_cwd"),
        doc_basis_id=identity.get("doc_basis_id"),
        identity_status=_session_identity_status(
            record["events"], explicit_identity, journal_dir
        ),
        state=_candidate_state(record),
        prompt_hint=_last_prompt_excerpt(record["events"]),
        source_session=f"{record['source_tool']}:{record['session_id']}",
    )


def _session_relevant(events: list[dict], project_key: str) -> bool:
    """Check if any event in a session relates to the given project."""
    for e in events:
        if e.get("scope_key") == project_key:
            return True
        if project_key in (e.get("affected_projects") or []):
            return True
    return False


def _session_matches_project_selection(
    events: list[dict[str, Any]],
    project_key: str,
    *,
    selection_clue: Optional[dict[str, Any]] = None,
) -> bool:
    if _session_relevant(events, project_key):
        return True

    selection_clue = _normalize_selection_clue(selection_clue)
    if project_key != "_repo" or not selection_clue:
        return False

    expected_worktree = selection_clue.get("worktree_cwd")
    if not expected_worktree:
        return False

    explicit_identity = _session_explicit_identity(events)
    if not _has_selection_identity(explicit_identity):
        return False
    return _identity_matches_clue(explicit_identity, selection_clue)


def _empty_resume(project_key: str, validity: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    decision = ResumeDecision(
        validity=validity,
        coverage="none",
        identity_status="legacy_untrusted",
        resume_mode=_resume_mode(validity, "legacy_untrusted"),
        can_auto_resume=_execution_gate(validity, "legacy_untrusted")[0],
        can_execute_worker=_execution_gate(validity, "legacy_untrusted")[1],
        task_identity={
            "task_id": None,
            "route_id": None,
            "worker_cwd": None,
            "worktree_cwd": None,
        },
        doc_basis={
            "doc_basis_id": None,
            "docs_source": None,
            "docs_revision": None,
            "doc_mode": None,
            "doc_basis_paths": None,
        },
    )
    r = {
        "v": SCHEMA_VERSION,
        "project_key": project_key,
        "validity": decision.validity,
        "coverage": decision.coverage,
        "identity_status": decision.identity_status,
        "resume_mode": decision.resume_mode,
        "can_auto_resume": decision.can_auto_resume,
        "can_execute_worker": decision.can_execute_worker,
        "generated_at": now,
        "provenance": {
            "source_sessions": [],
            "last_event_id": None,
            "last_event_at": None,
        },
        "git_basis": {
            "head": None,
            "status_fingerprint": None,
            "dirty_files": [],
        },
        "task_identity": dict(decision.task_identity),
        "doc_basis": dict(decision.doc_basis),
        "identity_explicit": _empty_identity(),
        "recent": {
            "last_user_prompt_excerpt": None,
            "last_assistant_excerpt": None,
            "last_compact_summary_excerpt": None,
            "last_failure": None,
            "files_touched": [],
        },
        "candidate_items": [candidate.to_dict() for candidate in decision.candidate_items],
        "warnings": list(decision.warnings),
        "rendered_context": "",
    }
    return r


def _build_ambiguous_resume(
    project_key: str,
    groups: list[dict[str, Any]],
    warning: str,
    journal_dir: str | Path,
    selection_clue: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    resume = _empty_resume(project_key, "ambiguous")
    identity_status = (
        "explicit_invalid"
        if any(
            _session_identity_status(
                group["best_record"]["events"],
                group["best_record"]["explicit_identity"],
                journal_dir,
            )
            != "legacy_untrusted"
            for group in groups
        )
        else "legacy_untrusted"
    )
    decision = ResumeDecision(
        validity="ambiguous",
        coverage=resume["coverage"],
        identity_status=identity_status,
        resume_mode=_resume_mode("ambiguous", identity_status),
        can_auto_resume=False,
        can_execute_worker=False,
        task_identity=dict(resume["task_identity"]),
        doc_basis=dict(resume["doc_basis"]),
    )
    resume["identity_status"] = decision.identity_status
    resume["resume_mode"] = decision.resume_mode
    resume["can_auto_resume"] = decision.can_auto_resume
    resume["can_execute_worker"] = decision.can_execute_worker
    if groups:
        best_records = [group["best_record"] for group in groups]
        latest_record = max(best_records, key=lambda record: record["last_ts"])
        resume["coverage"] = (
            "full"
            if all(record["source_tool"] == "claude" for record in best_records)
            else "partial"
        )
        resume["provenance"] = {
            "source_sessions": [
                f"{record['source_tool']}:{record['session_id']}"
                for record in best_records
            ],
            "last_event_id": latest_record["events"][-1].get("event_id"),
            "last_event_at": latest_record["events"][-1].get("ts"),
        }
        resume["candidate_items"] = [
            _build_candidate(group, journal_dir).to_dict()
            for group in groups
            if group.get("has_selection_identity")
        ][:3]

    resume["warnings"].append(warning)
    if selection_clue:
        resume["warnings"].append("selection_clue_requires_disambiguation")
    if not resume["candidate_items"]:
        resume["warnings"].append("explicit_repo_worktree_task_clue_required")
    resume["rendered_context"] = render_context(resume)
    return resume


def _build_resume(
    project_key: str,
    journal_dir: str | Path,
    source_tool: str,
    session_id: str,
    events: list[dict],
) -> dict[str, Any]:
    """Build a Resume from a single session's events."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    last_event = events[-1]

    # Gather fields from events
    last_prompt: Optional[str] = None
    last_assistant: Optional[str] = None
    last_compact: Optional[str] = None
    last_failure: Optional[str] = None
    files_touched: list[str] = []
    git_head: Optional[str] = None
    git_fp: Optional[str] = None
    dirty_files: list[str] = []
    identity = _session_identity(events)
    explicit_identity = _session_explicit_identity(events)

    for e in events:
        text = e.get("text", {})
        facts = e.get("facts", {})

        if text.get("prompt_excerpt"):
            last_prompt = text["prompt_excerpt"]
        if text.get("assistant_excerpt"):
            last_assistant = text["assistant_excerpt"]
        if text.get("compact_summary_excerpt"):
            last_compact = text["compact_summary_excerpt"]
        if facts.get("error_kind") or facts.get("error_message"):
            last_failure = facts.get("error_message") or facts.get("error_kind")
        for fp in facts.get("file_paths", []):
            if fp not in files_touched:
                files_touched.append(fp)
        if facts.get("git_head"):
            git_head = facts["git_head"]
        if facts.get("git_status_fingerprint"):
            git_fp = facts["git_status_fingerprint"]
        if facts.get("dirty_files"):
            dirty_files = facts["dirty_files"]

    # Determine coverage
    coverage = "full" if source_tool == "claude" else "partial"
    identity_status = _session_identity_status(events, explicit_identity, journal_dir)
    launch_mode = _session_binding_launch_mode(events)
    can_auto_resume, can_execute_worker = _execution_gate(
        "valid",
        identity_status,
        launch_mode=launch_mode,
        git_basis_head=git_head,
        git_status_fingerprint=git_fp,
    )

    # Check if mid-session checkpoint (PostCompact reduce)
    state = session_liveness_policy(source_tool, events)
    warnings: list[str] = []
    if state == "open":
        warnings.append("mid_session_checkpoint")
    if identity_status == "legacy_untrusted":
        warnings.append("legacy_untrusted_resume")
    elif identity_status == "explicit_invalid":
        warnings.append("explicit_identity_incomplete")
    elif identity_status == "producer_invalid":
        warnings.append("new_session_missing_required_identity")
    if launch_mode == "one-shot":
        warnings.append("one_shot_preflight_only")
    elif launch_mode == "manual":
        warnings.append("manual_bind_preflight_only")

    decision = ResumeDecision(
        validity="valid",
        coverage=coverage,
        identity_status=identity_status,
        resume_mode=_resume_mode("valid", identity_status, launch_mode=launch_mode),
        can_auto_resume=can_auto_resume,
        can_execute_worker=can_execute_worker,
        task_identity={
            "task_id": identity.get("task_id"),
            "route_id": identity.get("route_id"),
            "worker_cwd": identity.get("worker_cwd"),
            "worktree_cwd": identity.get("worktree_cwd"),
        },
        doc_basis={
            "doc_basis_id": identity.get("doc_basis_id"),
            "docs_source": identity.get("docs_source"),
            "docs_revision": identity.get("docs_revision"),
            "doc_mode": identity.get("doc_mode"),
            "doc_basis_paths": identity.get("doc_basis_paths"),
        },
        warnings=tuple(warnings),
    )

    resume = {
        "v": SCHEMA_VERSION,
        "project_key": project_key,
        "validity": decision.validity,
        "coverage": decision.coverage,
        "identity_status": decision.identity_status,
        "resume_mode": decision.resume_mode,
        "can_auto_resume": decision.can_auto_resume,
        "can_execute_worker": decision.can_execute_worker,
        "generated_at": now,
        "provenance": {
            "source_sessions": [f"{source_tool}:{session_id}"],
            "last_event_id": last_event.get("event_id"),
            "last_event_at": last_event.get("ts"),
        },
        "git_basis": {
            "head": git_head,
            "status_fingerprint": git_fp,
            "dirty_files": dirty_files,
        },
        "task_identity": dict(decision.task_identity),
        "doc_basis": dict(decision.doc_basis),
        "identity_explicit": explicit_identity,
        "recent": {
            "last_user_prompt_excerpt": last_prompt,
            "last_assistant_excerpt": last_assistant,
            "last_compact_summary_excerpt": last_compact,
            "last_failure": last_failure,
            "files_touched": files_touched,
        },
        "candidate_items": [],
        "warnings": list(decision.warnings),
        "rendered_context": "",
    }
    # Pre-render context
    resume["rendered_context"] = render_context(resume)
    return resume


def _cached_explicit_identity_still_proven(
    resume: dict[str, Any],
    journal_dir: Path,
) -> bool:
    provenance = resume.get("provenance") or {}
    expected_identity = _normalize_selection_clue(_resume_explicit_identity(resume))
    if not expected_identity:
        return False

    records = _load_source_session_records(journal_dir, provenance.get("source_sessions"))
    if not records:
        return False

    last_event_id = provenance.get("last_event_id")
    if last_event_id:
        records = [
            record
            for record in records
            if any(event.get("event_id") == last_event_id for event in record[2])
        ]
        if not records:
            return False

    for _tool, _session_id, events in records:
        explicit_identity = _session_explicit_identity(events)
        if not _identity_matches_clue(explicit_identity, expected_identity):
            continue
        if (
            _session_identity_status(
                events,
                explicit_identity,
                journal_dir,
            )
            == "explicit_valid"
        ):
            return True
    return False


def newer_matching_journal_exists(
    project_key: str,
    journal_dir: str | Path,
    resume: dict[str, Any],
    *,
    selection_clue: Optional[dict[str, Any]] = None,
    exclude_session: Optional[tuple[str, str]] = None,
) -> bool:
    provenance = resume.get("provenance") or {}
    cached_last_ts = provenance.get("last_event_at")
    cached_last_event_id = provenance.get("last_event_id")
    if not cached_last_ts:
        return True

    selection_clue = _normalize_selection_clue(selection_clue)
    sessions = _load_all_sessions(Path(journal_dir))
    for (source_tool, session_id), events in sessions.items():
        if exclude_session == (source_tool, session_id):
            continue
        if not _session_matches_project_selection(
            events,
            project_key,
            selection_clue=selection_clue,
        ):
            continue
        identity = _session_identity(events)
        if selection_clue and not _identity_matches_clue(identity, selection_clue):
            continue
        last_event = events[-1]
        last_ts = last_event.get("ts") or ""
        last_event_id = last_event.get("event_id")
        if last_ts > cached_last_ts:
            return True
        if last_ts == cached_last_ts and last_event_id != cached_last_event_id:
            return True
    return False
