"""Event normalization helpers for harness handoff journals."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


EVENT_SCHEMA_VERSION = 1
SESSION_START_PRODUCER_SCHEMA_VERSION = 2

_SELECTION_IDENTITY_FIELDS = (
    "task_id",
    "route_id",
    "worker_cwd",
    "worktree_cwd",
    "doc_basis_id",
)
_DOC_BASIS_FIELDS = (
    "docs_source",
    "docs_revision",
    "doc_mode",
)
_DOC_BASIS_META_FIELDS = ("doc_basis_paths",)


def normalize_event(
    hook_event: str,
    stdin_json: dict[str, Any],
    source_tool: str,
    session_id: str,
) -> dict[str, Any]:
    """Normalize raw hook stdin into the Session Journal schema."""
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    event_id = f"{ts}-{source_tool}-{session_id}-{secrets.token_hex(2)}"

    cwd = stdin_json.get("cwd") or os.getcwd()
    repo_root = _find_repo_root(cwd)
    scope_key = _scope_key_from_path(cwd, repo_root)

    file_paths = _extract_file_paths(hook_event, stdin_json)
    affected = _affected_projects(file_paths, repo_root, scope_key)
    identity_facts = _extract_identity_facts(stdin_json)

    facts: dict[str, Any] = {
        "git_head": stdin_json.get("git_head"),
        "git_status_fingerprint": stdin_json.get("git_status_fingerprint"),
        "dirty_files": stdin_json.get("dirty_files", []),
        "file_paths": file_paths,
        "tool_name": stdin_json.get("tool_name"),
        "producer_schema_version": None,
        "identity_acknowledged": False,
        "identity_source": None,
        "identity_binding_id": None,
        "binding_state": None,
        "identity_validation_status": None,
        "session_start_source": None,
        "session_end_reason": None,
        "error_kind": None,
        "error_message": None,
    }
    facts.update(identity_facts)

    if hook_event == "SessionStart":
        facts["producer_schema_version"] = SESSION_START_PRODUCER_SCHEMA_VERSION
        facts["session_start_source"] = stdin_json.get("source")
    elif hook_event == "SessionEnd":
        facts["session_end_reason"] = stdin_json.get("reason")
    elif hook_event in ("StopFailure", "PostToolUseFailure"):
        facts["error_kind"] = stdin_json.get("error_kind") or stdin_json.get("error")
        facts["error_message"] = stdin_json.get("error_message") or stdin_json.get(
            "error_details"
        )
    elif hook_event == "PostToolUse":
        facts["tool_name"] = stdin_json.get("tool_name")

    text: dict[str, Optional[str]] = {
        "prompt_excerpt": None,
        "assistant_excerpt": None,
        "compact_summary_excerpt": None,
    }
    if hook_event == "UserPromptSubmit":
        text["prompt_excerpt"] = _truncate(stdin_json.get("prompt"), 200)
    elif hook_event == "Stop":
        text["assistant_excerpt"] = _truncate(
            stdin_json.get("last_assistant_message"), 200
        )
    elif hook_event == "StopFailure":
        text["assistant_excerpt"] = _truncate(
            stdin_json.get("last_assistant_message"), 200
        )
    elif hook_event == "PostCompact":
        text["compact_summary_excerpt"] = _truncate(
            stdin_json.get("compact_summary"), 500
        )

    return {
        "v": EVENT_SCHEMA_VERSION,
        "event_id": event_id,
        "ts": ts,
        "source_tool": source_tool,
        "hook_event": hook_event,
        "session_id": session_id,
        "turn_id": stdin_json.get("turn_id"),
        "repo_root": repo_root,
        "cwd": cwd,
        "scope_key": scope_key,
        "affected_projects": affected,
        "transcript_path": stdin_json.get("transcript_path"),
        "facts": facts,
        "text": text,
    }


def _truncate(text: Optional[str], max_len: int) -> Optional[str]:
    if text is None:
        return None
    if len(text) <= max_len:
        return text
    return text[:max_len]


def _find_repo_root(cwd: str) -> str:
    """Walk up from cwd to find a .git directory."""
    p = Path(cwd).resolve()
    for parent in [p, *p.parents]:
        if (parent / ".git").exists():
            return str(parent)
    return str(p)


def _scope_key_from_path(path: str, repo_root: str) -> str:
    """Longest-prefix match against top-level project dirs containing AGENTS.md."""
    try:
        rel = Path(path).resolve().relative_to(Path(repo_root).resolve())
    except ValueError:
        return "_repo"

    parts = rel.parts
    if not parts:
        return "_repo"

    candidate = Path(repo_root) / parts[0]
    if (candidate / "AGENTS.md").exists():
        return parts[0]

    return "_repo"


def _extract_file_paths(hook_event: str, stdin_json: dict) -> list[str]:
    """Extract file paths from hook input."""
    paths: list[str] = []
    if hook_event == "PostToolUse":
        tool_input = stdin_json.get("tool_input", {})
        if isinstance(tool_input, dict):
            fp = tool_input.get("file_path")
            if fp:
                paths.append(fp)
        tool_response = stdin_json.get("tool_response", {})
        if isinstance(tool_response, dict):
            fp = tool_response.get("filePath")
            if fp and fp not in paths:
                paths.append(fp)
    elif hook_event in ("Stop", "StopFailure"):
        paths.extend(stdin_json.get("dirty_files", []))
    return paths


def _extract_identity_facts(stdin_json: dict[str, Any]) -> dict[str, Any]:
    """Pull authoritative task identity/doc-basis fields from hook stdin."""
    doc_basis = stdin_json.get("doc_basis", {})
    if not isinstance(doc_basis, dict):
        doc_basis = {}

    facts: dict[str, Any] = {}
    for field in _SELECTION_IDENTITY_FIELDS:
        facts[field] = stdin_json.get(field)
    for field in _DOC_BASIS_FIELDS:
        value = stdin_json.get(field)
        if value is None:
            value = doc_basis.get(field)
        facts[field] = value
    for field in _DOC_BASIS_META_FIELDS:
        value = stdin_json.get(field)
        if value is None:
            value = doc_basis.get(field)
        facts[field] = value
    return facts


def _affected_projects(
    file_paths: list[str],
    repo_root: str,
    primary_scope: str,
) -> list[str]:
    """Derive affected_projects from file paths and primary scope."""
    projects = set()
    if primary_scope != "_repo":
        projects.add(primary_scope)

    for fp in file_paths:
        sk = _scope_key_from_path(fp, repo_root)
        if sk != "_repo":
            projects.add(sk)

    if not projects:
        return [primary_scope]

    return sorted(projects)
