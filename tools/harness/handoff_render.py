"""Rendering helpers for harness handoff resume payloads."""

from __future__ import annotations

import json
from typing import Any

from .handoff_identity import (
    _execution_gate,
    _identity_status,
    _resume_explicit_identity,
    _resume_mode,
)
from .handoff_types import ResumeCandidate, ResumeDecision


def render_context(resume: dict[str, Any]) -> str:
    """Render a resume into the SessionStart context injection format."""
    decision = _resume_decision_from_resume(resume)
    validity = decision.validity
    coverage = decision.coverage
    project = resume.get("project_key", "?")
    gen_at = resume.get("generated_at", "?")

    recent = resume.get("recent", {})
    prov = resume.get("provenance", {})
    git_basis = resume.get("git_basis", {})
    task_identity = decision.task_identity
    doc_basis = decision.doc_basis
    candidate_items = decision.candidate_items
    warnings = decision.warnings

    lines = [
        f"[handoff {validity}/{coverage} project={project} at {gen_at}]",
        f"last prompt: {recent.get('last_user_prompt_excerpt') or 'none'}",
        f"last assistant: {recent.get('last_assistant_excerpt') or 'none'}",
        f"dirty files: {', '.join(git_basis.get('dirty_files', [])) or 'none'}",
        f"last failure: {recent.get('last_failure') or 'none'}",
        f"source sessions: {', '.join(prov.get('source_sessions', []))}",
        f"identity status: {decision.identity_status}",
        f"resume mode: {decision.resume_mode}",
        "execution gate: "
        f"auto_resume={'yes' if decision.can_auto_resume else 'no'}, "
        f"execute_worker={'yes' if decision.can_execute_worker else 'no'}",
    ]

    task_bits = [
        f"task_id={task_identity.get('task_id')}",
        f"route_id={task_identity.get('route_id')}",
        f"worker_cwd={task_identity.get('worker_cwd')}",
        f"worktree_cwd={task_identity.get('worktree_cwd')}",
    ]
    task_bits = [bit for bit in task_bits if not bit.endswith("=None")]
    if task_bits:
        lines.append(f"task identity: {', '.join(task_bits)}")

    basis_bits = [
        f"doc_basis_id={doc_basis.get('doc_basis_id')}",
        f"docs_source={doc_basis.get('docs_source')}",
        f"docs_revision={doc_basis.get('docs_revision')}",
        f"doc_mode={doc_basis.get('doc_mode')}",
    ]
    basis_bits = [bit for bit in basis_bits if not bit.endswith("=None")]
    if basis_bits:
        lines.append(f"doc basis: {', '.join(basis_bits)}")

    for index, item in enumerate(candidate_items[:3], start=1):
        candidate_bits = [
            f"task={item.task_id or '?'}",
            f"route={item.route_id or '?'}",
            f"worktree={item.worktree_cwd or '?'}",
            f"doc_basis={item.doc_basis_id or '?'}",
            f"state={item.state or '?'}",
        ]
        candidate_status = item.identity_status
        if candidate_status:
            candidate_bits.append(f"identity={candidate_status}")
        prompt_hint = item.prompt_hint
        if prompt_hint:
            candidate_bits.append(f"hint={prompt_hint}")
        lines.append(f"candidate {index}: {', '.join(candidate_bits)}")

    if warnings:
        lines.append(f"warnings: {', '.join(warnings)}")

    return "\n".join(lines)


def emit_response(
    source_tool: str,
    hook_event: str,
    payload: dict[str, Any],
) -> str:
    """Return stdout string for the hook.

    Only SessionStart produces output (rendered_context).
    Everything else returns empty string.
    """
    if hook_event == "SessionStart":
        context = payload.get("rendered_context", "")
        if context:
            return json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context,
                }
            })
    return ""


def _resume_candidate_from_item(item: dict[str, Any]) -> ResumeCandidate:
    return ResumeCandidate(
        task_id=item.get("task_id"),
        route_id=item.get("route_id"),
        worktree_cwd=item.get("worktree_cwd"),
        doc_basis_id=item.get("doc_basis_id"),
        identity_status=item.get("identity_status") or "legacy_untrusted",
        state=item.get("state") or "closed",
        prompt_hint=item.get("prompt_hint"),
        source_session=item.get("source_session") or "",
    )


def _resume_decision_from_resume(resume: dict[str, Any]) -> ResumeDecision:
    validity = resume.get("validity", "unavailable")
    identity_status = resume.get("identity_status") or _identity_status(
        _resume_explicit_identity(resume)
    )
    resume_mode = resume.get("resume_mode") or _resume_mode(
        validity, identity_status
    )
    can_auto_resume = resume.get("can_auto_resume")
    can_execute_worker = resume.get("can_execute_worker")
    if can_auto_resume is None or can_execute_worker is None:
        can_auto_resume, can_execute_worker = _execution_gate(
            validity, identity_status
        )

    return ResumeDecision(
        validity=validity,
        coverage=resume.get("coverage", "unknown"),
        identity_status=identity_status,
        resume_mode=resume_mode,
        can_auto_resume=can_auto_resume,
        can_execute_worker=can_execute_worker,
        task_identity=dict(resume.get("task_identity", {})),
        doc_basis=dict(resume.get("doc_basis", {})),
        candidate_items=tuple(
            _resume_candidate_from_item(item)
            for item in resume.get("candidate_items", [])
        ),
        warnings=tuple(resume.get("warnings", [])),
    )
