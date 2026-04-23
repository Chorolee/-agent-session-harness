from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.harness import session_identity


class SessionIdentityTests(unittest.TestCase):
    def test_doc_basis_revision_changes_when_approved_doc_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_minimal_repo(repo)

            paths = [
                "docs/specs/project-roadmap/decision-log.md",
                "docs/specs/task-spec.md",
            ]
            before = session_identity.resolve_verified_docs_revision(
                worker_cwd=str(repo),
                docs_source="root-canonical",
                doc_mode="root-canonical",
                doc_basis_paths=paths,
            )

            (repo / "docs/specs/task-spec.md").write_text(
                "# Task Spec\n\nUpdated scope.\n",
                encoding="utf-8",
            )

            after = session_identity.resolve_verified_docs_revision(
                worker_cwd=str(repo),
                docs_source="root-canonical",
                doc_mode="root-canonical",
                doc_basis_paths=paths,
            )

            self.assertNotEqual(before, after)

    def test_doc_basis_paths_must_stay_inside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_minimal_repo(repo)

            outside = repo.parent / "outside.md"
            outside.write_text("outside\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                session_identity.resolve_verified_docs_revision(
                    worker_cwd=str(repo),
                    docs_source="root-canonical",
                    doc_mode="root-canonical",
                    doc_basis_paths=[str(outside)],
                )


def _init_minimal_repo(repo: Path) -> None:
    (repo / ".git").mkdir()
    (repo / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    (repo / "AI_INDEX.md").write_text("# Index\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text("# Claude\n", encoding="utf-8")
    (repo / "docs/specs/project-roadmap").mkdir(parents=True)
    (repo / "docs/ops").mkdir(parents=True)
    (repo / "docs/specs/AGENTS.md").write_text("# Specs\n", encoding="utf-8")
    (repo / "docs/specs/project-roadmap/decision-log.md").write_text(
        "# Decision Log\n",
        encoding="utf-8",
    )
    (repo / "docs/specs/task-spec.md").write_text(
        "# Task Spec\n\nInitial scope.\n",
        encoding="utf-8",
    )
    for rel_path in (
        "docs/ops/agent-operations.md",
        "docs/ops/model-routing.md",
        "docs/ops/operating-protocol.md",
        "docs/ops/resume-policy.md",
        "docs/ops/review-policy.md",
        "docs/ops/session-packets.md",
        "docs/ops/worktree-drift.md",
    ):
        (repo / rel_path).write_text(f"# {Path(rel_path).stem}\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
