"""Identity extraction and execution gate helpers for harness handoff."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from . import session_identity


EXECUTABLE_RESUME_MIN_PRODUCER_SCHEMA_VERSION = 2

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
_IDENTITY_FACT_FIELDS = _SELECTION_IDENTITY_FIELDS + _DOC_BASIS_FIELDS
_IDENTITY_METADATA_FIELDS = _IDENTITY_FACT_FIELDS + _DOC_BASIS_META_FIELDS
_ALLOWED_DOCS_SOURCE_VALUES = {"root-canonical", "branch-docs-approved"}
_ALLOWED_DOC_MODE_VALUES = {"root-canonical", "branch-docs-approved"}
_REQUIRED_RESUME_GATE_FIELDS = (
    "identity_status",
    "resume_mode",
    "can_auto_resume",
    "can_execute_worker",
    "identity_explicit",
)


def selection_clue_from_event(
    event: dict[str, Any],
    *,
    worktree_root_checker: Callable[[str], bool] | None = None,
) -> Optional[dict[str, Any]]:
    """Extract explicit task-selection clues from a normalized event."""
    is_worktree_repo_root = worktree_root_checker or _is_worktree_repo_root
    facts = event.get("facts") or {}
    clue: dict[str, Any] = {}
    if (
        event.get("hook_event") == "SessionStart"
        and facts.get("identity_validation_status") == "validated"
    ):
        clue = _normalize_selection_clue(facts) or {}
    repo_root = event.get("repo_root")
    if repo_root and is_worktree_repo_root(repo_root) and not clue.get("worktree_cwd"):
        clue["worktree_cwd"] = repo_root
    return _normalize_selection_clue(clue)


def _explicit_identity_from_facts(
    facts: dict[str, Any],
) -> dict[str, Any]:
    identity = _empty_identity()
    for field in _IDENTITY_METADATA_FIELDS:
        value = facts.get(field)
        if value not in (None, ""):
            identity[field] = value
    return identity


def _is_worktree_repo_root(path_value: str) -> bool:
    git_path = Path(path_value) / ".git"
    if not git_path.is_file():
        return False
    try:
        git_ref = git_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return git_ref.startswith("gitdir:") and "/worktrees/" in git_ref.replace("\\", "/")


def _empty_identity() -> dict[str, Any]:
    return {field: None for field in _IDENTITY_METADATA_FIELDS}


def _collect_session_identity(
    session_events: list[dict[str, Any]],
    *,
    allow_inferred_worktree: bool,
    worktree_root_checker: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    is_worktree_repo_root = worktree_root_checker or _is_worktree_repo_root
    identity = _empty_identity()
    fallback_worktree_cwd: Optional[str] = None
    for event in session_events:
        facts = event.get("facts") or {}
        if event.get("hook_event") == "SessionStart":
            identity = _empty_identity()
            for field in _IDENTITY_METADATA_FIELDS:
                value = facts.get(field)
                if value not in (None, ""):
                    identity[field] = value
        repo_root = event.get("repo_root")
        if repo_root and is_worktree_repo_root(repo_root):
            fallback_worktree_cwd = repo_root
    if allow_inferred_worktree and not identity.get("worktree_cwd") and fallback_worktree_cwd:
        identity["worktree_cwd"] = fallback_worktree_cwd
    return identity


def _session_identity(
    session_events: list[dict[str, Any]],
    *,
    worktree_root_checker: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    return _collect_session_identity(
        session_events,
        allow_inferred_worktree=True,
        worktree_root_checker=worktree_root_checker,
    )


def _session_explicit_identity(
    session_events: list[dict[str, Any]],
    *,
    worktree_root_checker: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    return _collect_session_identity(
        session_events,
        allow_inferred_worktree=False,
        worktree_root_checker=worktree_root_checker,
    )


def _resume_identity(resume: dict[str, Any]) -> dict[str, Any]:
    identity = _empty_identity()
    task_identity = resume.get("task_identity") or {}
    doc_basis = resume.get("doc_basis") or {}

    for field in ("task_id", "route_id", "worker_cwd", "worktree_cwd"):
        value = task_identity.get(field)
        if value not in (None, ""):
            identity[field] = value
    for field in ("doc_basis_id", "docs_source", "docs_revision", "doc_mode"):
        value = doc_basis.get(field)
        if value not in (None, ""):
            identity[field] = value
    if doc_basis.get("doc_basis_paths") not in (None, ""):
        identity["doc_basis_paths"] = doc_basis.get("doc_basis_paths")
    return identity


def _resume_explicit_identity(resume: dict[str, Any]) -> dict[str, Any]:
    explicit = resume.get("identity_explicit")
    if not isinstance(explicit, dict):
        return _resume_identity(resume)

    identity = _empty_identity()
    for field in _IDENTITY_METADATA_FIELDS:
        value = explicit.get(field)
        if value not in (None, ""):
            identity[field] = value
    return identity


def _has_selection_identity(identity: dict[str, Any]) -> bool:
    return any(identity.get(field) for field in _SELECTION_IDENTITY_FIELDS)


def _identity_status(identity: dict[str, Any]) -> str:
    if all(identity.get(field) for field in _IDENTITY_FACT_FIELDS):
        return "explicit_valid"
    if any(identity.get(field) for field in _IDENTITY_FACT_FIELDS):
        return "explicit_invalid"
    return "legacy_untrusted"


def _coerce_schema_version(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _session_requires_explicit_identity(session_events: list[dict[str, Any]]) -> bool:
    for event in session_events:
        if event.get("hook_event") != "SessionStart":
            continue
        facts = event.get("facts") or {}
        schema_version = _coerce_schema_version(
            facts.get("producer_schema_version")
        )
        if (
            schema_version is not None
            and schema_version >= EXECUTABLE_RESUME_MIN_PRODUCER_SCHEMA_VERSION
        ):
            return True
    return False


def _latest_session_start_event(session_events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(session_events):
        if event.get("hook_event") == "SessionStart":
            return event
    return None


def _session_has_acknowledged_identity(
    session_events: list[dict[str, Any]],
    journal_dir: str | Path,
    explicit_identity: dict[str, Any],
) -> bool:
    _ = journal_dir
    latest_start = _latest_session_start_event(session_events)
    if not latest_start:
        return False
    start_facts = latest_start.get("facts") or {}
    start_event_id = latest_start.get("event_id")
    start_binding_id = start_facts.get("identity_binding_id")
    if not (
        start_event_id
        and start_binding_id
        and start_facts.get("identity_source") == "binding"
        and start_facts.get("identity_validation_status") == "validated"
        and _identity_facts_match(start_facts, explicit_identity)
    ):
        return False

    for event in session_events:
        if event.get("hook_event") == "IdentityAcknowledged":
            facts = event.get("facts") or {}
            ack_for_event_id = facts.get("ack_for_event_id")
            binding_id = facts.get("identity_binding_id")
            if (
                facts.get("identity_acknowledged") is True
                and str(ack_for_event_id) == str(start_event_id)
                and binding_id
                and str(binding_id) == str(start_binding_id)
                and _identity_facts_match(facts, explicit_identity)
            ):
                return True
    return False


def _identity_facts_match(
    facts: dict[str, Any],
    explicit_identity: dict[str, Any],
) -> bool:
    for field in _IDENTITY_FACT_FIELDS:
        expected = explicit_identity.get(field)
        if expected in (None, "") or facts.get(field) != expected:
            return False
    expected_paths = explicit_identity.get("doc_basis_paths")
    if expected_paths not in (None, ""):
        if list(facts.get("doc_basis_paths") or []) != list(expected_paths or []):
            return False
    return True


def _session_has_validated_identity(session_events: list[dict[str, Any]]) -> bool:
    latest_start = _latest_session_start_event(session_events)
    if not latest_start:
        return False
    facts = latest_start.get("facts") or {}
    return facts.get("identity_validation_status") == "validated"


def _session_uses_binding_identity(session_events: list[dict[str, Any]]) -> bool:
    latest_start = _latest_session_start_event(session_events)
    if not latest_start:
        return False
    facts = latest_start.get("facts") or {}
    return facts.get("identity_source") == "binding"


def _session_binding_launch_mode(session_events: list[dict[str, Any]]) -> str:
    latest_start = _latest_session_start_event(session_events)
    if latest_start:
        facts = latest_start.get("facts") or {}
        if facts.get("identity_source") == "binding":
            mode = facts.get("binding_launch_mode")
            if mode in {"interactive", "one-shot", "manual"}:
                return str(mode)
    return "interactive"


def _session_identity_status(
    session_events: list[dict[str, Any]],
    explicit_identity: dict[str, Any],
    journal_dir: str | Path,
) -> str:
    status = _identity_status(explicit_identity)
    requires_explicit_identity = _session_requires_explicit_identity(session_events)
    if not requires_explicit_identity:
        if status != "explicit_valid":
            return status
        if not _session_has_validated_identity(session_events):
            return "legacy_untrusted"
        if not _session_has_acknowledged_identity(
            session_events,
            journal_dir,
            explicit_identity,
        ):
            return "legacy_untrusted"
        return status
    if not explicit_identity.get("doc_basis_paths"):
        return "producer_invalid"
    if status != "explicit_valid":
        return "producer_invalid"
    if not _session_has_validated_identity(session_events):
        return "producer_invalid"
    if not _session_has_acknowledged_identity(
        session_events,
        journal_dir,
        explicit_identity,
    ):
        return "producer_invalid"
    return status


def _resume_mode(
    validity: str,
    identity_status: str,
    *,
    launch_mode: str = "interactive",
) -> str:
    if validity == "ambiguous":
        return "resume-chooser"
    if (
        validity == "valid"
        and identity_status == "explicit_valid"
        and launch_mode == "interactive"
    ):
        return "resume-fast"
    return "resume-preflight"


def _execution_gate(
    validity: str,
    identity_status: str,
    *,
    launch_mode: str = "interactive",
    git_basis_head: str | None = None,
    git_status_fingerprint: str | None = None,
) -> tuple[bool, bool]:
    executable = (
        validity == "valid"
        and identity_status == "explicit_valid"
        and launch_mode == "interactive"
        and bool(git_basis_head)
        and bool(git_status_fingerprint)
    )
    return executable, executable


def session_start_requires_identity_block(event: dict[str, Any]) -> bool:
    """New executable SessionStart records require validated identity plus binding ack."""
    if event.get("hook_event") != "SessionStart":
        return False
    facts = event.get("facts") or {}
    schema_version = _coerce_schema_version(facts.get("producer_schema_version"))
    if (
        schema_version is None
        or schema_version < EXECUTABLE_RESUME_MIN_PRODUCER_SCHEMA_VERSION
    ):
        return False
    if not _has_any_identity_metadata(facts):
        return False
    if facts.get("identity_validation_status") != "validated":
        return True
    if facts.get("binding_launch_mode") in {"one-shot", "manual"}:
        return False
    return facts.get("identity_acknowledged") is not True


def _has_any_identity_metadata(facts: dict[str, Any]) -> bool:
    for field in _IDENTITY_METADATA_FIELDS:
        value = facts.get(field)
        if value not in (None, "", []):
            return True
    return False


def stamp_session_start_identity_validation(event: dict[str, Any]) -> None:
    """Validate live SessionStart identity facts before journal append/cache writes."""
    if event.get("hook_event") != "SessionStart":
        return
    facts = event.get("facts") or {}
    explicit_identity = _explicit_identity_from_facts(facts)

    validation_error = _identity_shape_validation_error(facts, explicit_identity)
    if validation_error:
        facts["identity_validation_status"] = validation_error
        event["facts"] = facts
        return

    path_error, normalized_paths = _normalized_identity_paths(event, explicit_identity)
    if path_error:
        facts["identity_validation_status"] = path_error
        event["facts"] = facts
        return
    normalized_cwd, normalized_repo_root, normalized_worker_cwd, normalized_worktree_cwd = (
        normalized_paths
    )

    validation_error = _identity_path_alignment_error(
        normalized_cwd,
        normalized_repo_root,
        normalized_worker_cwd,
        normalized_worktree_cwd,
    )
    if validation_error:
        facts["identity_validation_status"] = validation_error
        event["facts"] = facts
        return

    validation_error = _doc_basis_config_validation_error(facts)
    if validation_error:
        facts["identity_validation_status"] = validation_error
        event["facts"] = facts
        return

    if not _doc_basis_matches_identity(
        facts,
        explicit_identity,
        normalized_worker_cwd,
        normalized_worktree_cwd,
    ):
        facts["identity_validation_status"] = "doc-basis-mismatch"
        event["facts"] = facts
        return

    facts["identity_validation_status"] = _git_basis_validation_status(facts)
    event["facts"] = facts


def _identity_shape_validation_error(
    facts: dict[str, Any],
    explicit_identity: dict[str, Any],
) -> str | None:
    if _identity_status(explicit_identity) != "explicit_valid":
        return "incomplete"
    schema_version = _coerce_schema_version(facts.get("producer_schema_version"))
    if (
        schema_version is not None
        and schema_version >= EXECUTABLE_RESUME_MIN_PRODUCER_SCHEMA_VERSION
        and not facts.get("doc_basis_paths")
    ):
        return "incomplete"
    return None


def _normalized_identity_paths(
    event: dict[str, Any],
    explicit_identity: dict[str, Any],
) -> tuple[str | None, tuple[str, str, str, str]]:
    cwd = event.get("cwd")
    repo_root = event.get("repo_root")
    if not cwd or not repo_root:
        return "missing-path-context", ("", "", "", "")
    try:
        return None, (
            str(Path(cwd).resolve()),
            str(Path(repo_root).resolve()),
            str(Path(str(explicit_identity.get("worker_cwd"))).resolve()),
            str(Path(str(explicit_identity.get("worktree_cwd"))).resolve()),
        )
    except Exception:
        return "path-normalization-failed", ("", "", "", "")


def _identity_path_alignment_error(
    normalized_cwd: str,
    normalized_repo_root: str,
    normalized_worker_cwd: str,
    normalized_worktree_cwd: str,
) -> str | None:
    if normalized_worker_cwd != normalized_cwd:
        return "worker-cwd-mismatch"
    if normalized_worktree_cwd != normalized_repo_root:
        return "worktree-cwd-mismatch"
    return None


def _doc_basis_config_validation_error(facts: dict[str, Any]) -> str | None:
    if facts.get("docs_source") not in _ALLOWED_DOCS_SOURCE_VALUES:
        return "unsupported-docs-source"
    if facts.get("doc_mode") not in _ALLOWED_DOC_MODE_VALUES:
        return "unsupported-doc-mode"
    return None


def _doc_basis_matches_identity(
    facts: dict[str, Any],
    explicit_identity: dict[str, Any],
    normalized_worker_cwd: str,
    normalized_worktree_cwd: str,
) -> bool:
    doc_basis_paths = facts.get("doc_basis_paths")
    try:
        session_identity.validate_worktree_doc_mode(
            worker_cwd=normalized_worker_cwd,
            worktree_cwd=normalized_worktree_cwd,
            docs_source=str(facts.get("docs_source")),
            docs_revision=str(facts.get("docs_revision")),
            doc_mode=str(facts.get("doc_mode")),
            doc_basis_paths=doc_basis_paths,
        )
        doc_basis_valid, _expected_doc_basis_id = session_identity.validate_doc_basis(
            route_id=str(explicit_identity.get("route_id")),
            worker_cwd=normalized_worker_cwd,
            doc_basis_id=str(explicit_identity.get("doc_basis_id")),
            docs_source=str(facts.get("docs_source")),
            docs_revision=str(facts.get("docs_revision")),
            doc_mode=str(facts.get("doc_mode")),
            doc_basis_paths=doc_basis_paths,
        )
    except (ValueError, OSError):
        return False
    return doc_basis_valid


def _git_basis_validation_status(facts: dict[str, Any]) -> str:
    if not facts.get("git_head"):
        return "missing-git-head"
    if not facts.get("git_status_fingerprint"):
        return "missing-git-status"
    return "validated"


def resume_cacheable(resume: dict[str, Any]) -> bool:
    """Shared project caches should not persist blocked producer-invalid resumes."""
    return resume.get("identity_status") != "producer_invalid"


def _resume_has_current_execution_metadata(resume: dict[str, Any]) -> bool:
    if any(field not in resume for field in _REQUIRED_RESUME_GATE_FIELDS):
        return False
    return isinstance(resume.get("identity_explicit"), dict)


def _normalize_selection_clue(
    selection_clue: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    if not selection_clue:
        return None

    normalized: dict[str, Any] = {}
    for field in _IDENTITY_FACT_FIELDS:
        value = selection_clue.get(field)
        if value not in (None, ""):
            normalized[field] = value
    return normalized or None


def _identity_matches_clue(
    identity: dict[str, Any],
    selection_clue: Optional[dict[str, Any]],
) -> bool:
    if not selection_clue:
        return True

    for field, expected in selection_clue.items():
        actual = identity.get(field)
        if actual in (None, ""):
            return False
        if actual != expected:
            return False
    return True
