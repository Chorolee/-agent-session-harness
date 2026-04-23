"""Compatibility facade for harness handoff modules.

Canonical data lives in per-session JSONL journals.
Per-project resume.json files are derived (rebuildable) caches.
These caches are metadata-first context hints. They may carry authoritative
task identity when hook input provides it, but they are not full per-task
resume packets.
"""

from __future__ import annotations

from . import session_identity
from .handoff_events import (
    EVENT_SCHEMA_VERSION,
    SESSION_START_PRODUCER_SCHEMA_VERSION,
    _affected_projects,
    _extract_file_paths,
    _extract_identity_facts,
    _find_repo_root,
    _scope_key_from_path,
    _truncate,
    normalize_event,
)
from .handoff_identity import (
    EXECUTABLE_RESUME_MIN_PRODUCER_SCHEMA_VERSION,
    _ALLOWED_DOCS_SOURCE_VALUES,
    _ALLOWED_DOC_MODE_VALUES,
    _coerce_schema_version,
    _collect_session_identity,
    _DOC_BASIS_FIELDS,
    _DOC_BASIS_META_FIELDS,
    _empty_identity,
    _execution_gate,
    _explicit_identity_from_facts,
    _has_selection_identity,
    _identity_facts_match,
    _identity_matches_clue,
    _identity_status,
    _IDENTITY_FACT_FIELDS,
    _IDENTITY_METADATA_FIELDS,
    _is_worktree_repo_root,
    _latest_session_start_event,
    _normalize_selection_clue,
    _REQUIRED_RESUME_GATE_FIELDS,
    _resume_explicit_identity,
    _resume_has_current_execution_metadata,
    _resume_identity,
    _resume_mode,
    _SELECTION_IDENTITY_FIELDS,
    _session_binding_launch_mode,
    _session_explicit_identity,
    _session_has_acknowledged_identity,
    _session_has_validated_identity,
    _session_identity,
    _session_identity_status,
    _session_requires_explicit_identity,
    _session_uses_binding_identity,
    resume_cacheable,
    selection_clue_from_event,
    session_start_requires_identity_block,
    stamp_session_start_identity_validation,
)
from .handoff_journal import (
    SessionHeader,
    _event_id_exists,
    _load_all_sessions,
    _load_source_session_records,
    _parse_jsonl,
    append_journal,
    iter_project_session_headers,
    load_session_header,
    load_source_session_headers,
)
from .handoff_liveness import (
    CODEX_SESSION_TTL_HOURS,
    _candidate_paths_for_project_scope,
    _candidate_state,
    _CLAUDE_TERMINAL_EVENTS,
    _CODEX_TERMINAL_EVENTS,
    _last_prompt_excerpt,
    _normalize_repo_path,
    _project_scoped_dirty_paths,
    _session_dirty_baseline,
    _session_looks_in_progress,
    _session_touched_paths,
    _terminal_dirty_is_session_owned,
    session_liveness_policy,
)
from .handoff_reduce import (
    SCHEMA_VERSION,
    _build_ambiguous_resume,
    _build_candidate,
    _build_resume,
    _cached_explicit_identity_still_proven,
    _empty_resume,
    _group_session_records,
    _session_matches_project_selection,
    _session_relevant,
    _task_group_key,
    newer_matching_journal_exists,
    reduce_project,
    validate_resume,
)
from .handoff_render import (
    _resume_candidate_from_item,
    _resume_decision_from_resume,
    emit_response,
    render_context,
)
from .handoff_types import (
    IdentityStatus,
    ResumeCandidate,
    ResumeDecision,
    ResumeMode,
    ResumeValidity,
)


_REDUCE_EVENTS = {"Stop", "StopFailure", "PostCompact"}
