"""Artifact control: allowlist gate, trigger detection, spec validation & lint.

Three enforcement surfaces (PreToolUse, pre-commit, commit-msg) all call
the same functions here.  No glob/regex duplication across shell/JSON/Python.
"""

from __future__ import annotations

import os
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover – PyYAML optional at import time
    yaml = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Allowlist patterns (03-artifacts.md §Layer 1)
# ---------------------------------------------------------------------------

_ALLOWLIST_PATTERNS: list[str] = [
    "AI_INDEX.md",
    "AGENTS.md",
    "*/AGENTS.md",
    "AGENTS.override.md",
    "*/AGENTS.override.md",
    "CLAUDE.md",
    "*/CLAUDE.md",
    "CLAUDE.local.md",
    "*/CLAUDE.local.md",
    ".claude/rules/**/*.md",
    ".claude/skills/**/*.md",
    "docs/specs/**/*.md",
    "docs/_archive/**/*.md",
    "docs/ops/**/*.md",
    "*/docs/specs/**/*.md",
    "*/docs/_archive/**/*.md",
    "**/README.md",
    "**/_evidence/*.md",
]


# ---------------------------------------------------------------------------
# 1. check_allowlist
# ---------------------------------------------------------------------------

def check_allowlist_pattern(file_path: str, repo_root: str) -> bool:
    """Check if *file_path* matches an allowlist pattern (ignores file existence).

    Used by pre-commit where staged files may not exist on disk yet.
    """
    abs_path = os.path.abspath(file_path)
    if not abs_path.lower().endswith(".md"):
        return True
    try:
        rel = os.path.relpath(abs_path, os.path.abspath(repo_root))
    except ValueError:
        return False
    rel = rel.replace(os.sep, "/")
    for pattern in _ALLOWLIST_PATTERNS:
        if _match_pattern(rel, pattern):
            return True
    return False


def check_allowlist(file_path: str, repo_root: str) -> bool:
    """Return True if *file_path* is allowed for new markdown creation.

    Only ``.md`` files are subject to the allowlist.
    Existing files are always allowed (edit OK anywhere).
    Non-``.md`` files are always allowed.
    """
    abs_path = os.path.abspath(file_path)

    # Non-.md files are always allowed
    if not abs_path.lower().endswith(".md"):
        return True

    # Existing files are always allowed (editing, not creating)
    if os.path.exists(abs_path):
        return True

    # New .md file — check against allowlist
    try:
        rel = os.path.relpath(abs_path, os.path.abspath(repo_root))
    except ValueError:
        return False

    # Normalize to forward slashes for matching
    rel = rel.replace(os.sep, "/")

    for pattern in _ALLOWLIST_PATTERNS:
        if _match_pattern(rel, pattern):
            return True

    return False


# ---------------------------------------------------------------------------
# 2. detect_triggers
# ---------------------------------------------------------------------------

def detect_triggers(
    changed_paths: list[str],
    triggers_yml_path: str,
) -> list[dict[str, Any]]:
    """Detect cross-boundary triggers from changed file paths.

    Returns a list of dicts:
      [{"project": "...", "trigger_type": "db_schema|public_contract|queue_worker",
        "paths": [...]}]
    """
    if yaml is None:
        return []

    try:
        with open(triggers_yml_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception:
        return []

    projects = config.get("projects", {})
    results: list[dict[str, Any]] = []

    for project_name, triggers in projects.items():
        if not isinstance(triggers, dict):
            continue
        for trigger_type, patterns in triggers.items():
            if not isinstance(patterns, list):
                continue
            matched: list[str] = []
            for changed in changed_paths:
                norm_changed = changed.replace(os.sep, "/")
                for pat in patterns:
                    if _match_pattern(norm_changed, pat):
                        if changed not in matched:
                            matched.append(changed)
                        break
            if matched:
                results.append({
                    "project": project_name,
                    "trigger_type": trigger_type,
                    "paths": matched,
                })

    return results


# ---------------------------------------------------------------------------
# 3. validate_spec_ref
# ---------------------------------------------------------------------------

def validate_spec_ref(spec_path: str, repo_root: str) -> bool:
    """Validate that *spec_path* is a real spec file.

    Checks:
    - Path exists as a file.
    - Under docs/specs/ or */docs/specs/ (relative to repo_root).
    - NOT under _archive/.
    """
    abs_path = os.path.abspath(os.path.join(repo_root, spec_path))
    if not os.path.isfile(abs_path):
        return False

    try:
        rel = os.path.relpath(abs_path, os.path.abspath(repo_root))
    except ValueError:
        return False

    rel = rel.replace(os.sep, "/")

    # Must be under docs/specs/ or */docs/specs/
    parts = rel.split("/")
    found_specs = False
    for i, part in enumerate(parts):
        if part == "docs" and i + 1 < len(parts) and parts[i + 1] == "specs":
            found_specs = True
            break

    if not found_specs:
        return False

    # Must NOT be under _archive/
    if "_archive" in parts:
        return False

    return True


# ---------------------------------------------------------------------------
# 4. lint_spec
# ---------------------------------------------------------------------------

_REQUIRED_SECTIONS = ["Scope", "Decision", "Implementation Order", "Decision Log"]


def lint_spec(spec_path: str) -> list[str]:
    """Lint a spec file. Returns a list of warning messages (empty = clean).

    Checks:
    - 200-line soft cap.
    - Required sections present.
    """
    warnings: list[str] = []

    try:
        with open(spec_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as exc:
        return [f"cannot read spec: {exc}"]

    # Line count soft cap
    if len(lines) > 200:
        warnings.append(
            f"spec exceeds 200-line soft cap ({len(lines)} lines); consider splitting"
        )

    # Required sections (look for ## headings, exact match against heading text)
    for section in _REQUIRED_SECTIONS:
        # Match "## Section" at line start, ensuring it's not a prefix of another heading
        # e.g. "## Decision" should not match inside "## Decision Log"
        found = False
        for line in lines:
            stripped = line.strip()
            for prefix in ("## ", "# "):
                if stripped.startswith(prefix):
                    heading_text = stripped[len(prefix):].strip()
                    if heading_text == section:
                        found = True
                        break
            if found:
                break
        if not found:
            warnings.append(f"missing required section: ## {section}")

    return warnings


# ===========================================================================
# Private helpers
# ===========================================================================

def _match_pattern(path: str, pattern: str) -> bool:
    """Match a relative path against an allowlist/trigger pattern.

    Supports:
    - ** for recursive directory matching
    - * for single-component wildcard
    - fnmatch-style matching
    """
    # Normalize
    path = path.replace(os.sep, "/").strip("/")
    pattern = pattern.replace(os.sep, "/").strip("/")

    # Handle ** patterns
    if "**" in pattern:
        # Split on first ** occurrence
        idx = pattern.index("**")
        prefix = pattern[:idx].strip("/")
        suffix = pattern[idx + 2:].strip("/")

        if prefix and suffix:
            # prefix/**/suffix
            # prefix may contain * wildcards (e.g. */docs/specs)
            path_parts = path.split("/")
            prefix_parts = prefix.split("/")
            suffix_parts = suffix.split("/")

            # Try every possible split point
            for i in range(len(prefix_parts), len(path_parts)):
                head = "/".join(path_parts[:len(prefix_parts)])
                tail = "/".join(path_parts[i:])
                if fnmatch(head, prefix) and (
                    fnmatch(tail, suffix) or _recursive_match(tail, suffix)
                ):
                    return True
            # Also try matching with exactly prefix_parts components
            if len(path_parts) >= len(prefix_parts):
                head = "/".join(path_parts[:len(prefix_parts)])
                rest = "/".join(path_parts[len(prefix_parts):])
                if fnmatch(head, prefix) and (
                    fnmatch(rest, suffix) or _recursive_match(rest, suffix)
                ):
                    return True
            return False
        elif prefix:
            # prefix/**
            path_parts = path.split("/")
            prefix_parts = prefix.split("/")
            if len(path_parts) >= len(prefix_parts):
                head = "/".join(path_parts[:len(prefix_parts)])
                return fnmatch(head, prefix)
            return False
        elif suffix:
            # **/suffix
            # Match suffix anywhere in path
            path_parts = path.split("/")
            suffix_parts = suffix.split("/")
            for i in range(len(path_parts)):
                candidate = "/".join(path_parts[i:])
                if fnmatch(candidate, suffix):
                    return True
            return fnmatch(path, suffix)
        else:
            return True

    # Simple fnmatch
    return fnmatch(path, pattern)


def _recursive_match(path: str, suffix_pattern: str) -> bool:
    """Try to match suffix_pattern against any tail of path split by /."""
    path_parts = path.split("/")
    for i in range(len(path_parts)):
        candidate = "/".join(path_parts[i:])
        if fnmatch(candidate, suffix_pattern):
            return True
    return False
