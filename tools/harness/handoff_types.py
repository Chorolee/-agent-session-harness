"""Shared types for handoff resume decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional, TypeAlias


IdentityStatus: TypeAlias = Literal[
    "explicit_valid",
    "explicit_invalid",
    "legacy_untrusted",
    "producer_invalid",
]
ResumeValidity: TypeAlias = Literal["valid", "ambiguous", "unavailable"]
ResumeMode: TypeAlias = Literal[
    "resume-fast",
    "resume-preflight",
    "resume-chooser",
]


@dataclass(frozen=True)
class ResumeCandidate:
    task_id: Optional[str]
    route_id: Optional[str]
    worktree_cwd: Optional[str]
    doc_basis_id: Optional[str]
    identity_status: IdentityStatus
    state: str
    prompt_hint: Optional[str]
    source_session: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "route_id": self.route_id,
            "worktree_cwd": self.worktree_cwd,
            "doc_basis_id": self.doc_basis_id,
            "identity_status": self.identity_status,
            "state": self.state,
            "prompt_hint": self.prompt_hint,
            "source_session": self.source_session,
        }


@dataclass(frozen=True)
class ResumeDecision:
    validity: ResumeValidity
    coverage: str
    identity_status: IdentityStatus
    resume_mode: ResumeMode
    can_auto_resume: bool
    can_execute_worker: bool
    task_identity: dict[str, Any]
    doc_basis: dict[str, Any]
    candidate_items: tuple[ResumeCandidate, ...] = ()
    warnings: tuple[str, ...] = ()
