"""Explicit session identity bindings for harness hook enrichment."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import shlex
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


IDENTITY_SCHEMA_VERSION = 2
BINDING_TTL_SECONDS = 900
HARNESS_LAUNCH_BINDING_ID_ENV = "HARNESS_LAUNCH_BINDING_ID"
HARNESS_LAUNCH_TOKEN_ENV = "HARNESS_LAUNCH_TOKEN"
HARNESS_BINDING_SCHEMA_ENV = "HARNESS_BINDING_SCHEMA"
HARNESS_HANDOFF_DIR_ENV = "HARNESS_HANDOFF_DIR"
HARNESS_LAUNCH_SESSION_PID_ENV = "HARNESS_LAUNCH_SESSION_PID"
_LAUNCH_MODE_VALUES = {"interactive", "one-shot", "manual"}
_SHELL_WRAPPER_EXECUTABLES = {"bash", "sh", "dash", "ksh", "zsh"}
_REQUIRED_BINDING_ENV_NAMES = {
    HARNESS_LAUNCH_BINDING_ID_ENV,
    HARNESS_LAUNCH_TOKEN_ENV,
    HARNESS_BINDING_SCHEMA_ENV,
    HARNESS_HANDOFF_DIR_ENV,
    HARNESS_LAUNCH_SESSION_PID_ENV,
}
_IDENTITY_FIELDS = (
    "task_id",
    "route_id",
    "worker_cwd",
    "worktree_cwd",
    "doc_basis_id",
    "docs_source",
    "docs_revision",
    "doc_mode",
)
_TERMINAL_BINDING_STATES = {"acknowledged", "rejected", "expired"}
_CANONICAL_DOC_RELATIVE_PATHS = (
    Path("AGENTS.md"),
    Path("docs/specs/AGENTS.md"),
    Path("docs/ops/agent-operations.md"),
    Path("docs/ops/operating-protocol.md"),
    Path("docs/ops/session-packets.md"),
    Path("docs/ops/resume-policy.md"),
    Path("docs/ops/worktree-drift.md"),
    Path("docs/ops/review-policy.md"),
    Path("docs/ops/model-routing.md"),
)
_DECISION_LOG_RELATIVE_PATH = Path("docs/specs/project-roadmap/decision-log.md")
_EXECUTION_MIRROR_RELATIVE_PATHS = (
    Path("AI_INDEX.md"),
    Path("CLAUDE.md"),
    Path(".claude/rules"),
    Path(".claude/skills"),
    Path(".githooks"),
    Path("tools/harness"),
)
_TRUSTED_VENDOR_BINARIES: dict[str, str | None] = {}


def _nvm_version_key(candidate: Path) -> tuple[int, int, int, str]:
    version = candidate.parent.parent.name
    if version.startswith("v"):
        version = version[1:]
    parts = version.split(".")
    parsed: list[int] = []
    for part in parts[:3]:
        try:
            parsed.append(int(part))
        except ValueError:
            parsed.append(-1)
    while len(parsed) < 3:
        parsed.append(-1)
    return (parsed[0], parsed[1], parsed[2], version)


def _discover_allowlisted_vendor_binary(executable: str) -> str | None:
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / executable,
        Path("/usr/local/bin") / executable,
        Path("/usr/bin") / executable,
        Path("/bin") / executable,
    ]
    if executable == "codex":
        nvm_root = home / ".nvm" / "versions" / "node"
        if nvm_root.exists():
            candidates.extend(
                sorted(
                    nvm_root.glob("*/bin/codex"),
                    key=_nvm_version_key,
                    reverse=True,
                )
            )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve())
    return None


def _resolved_trusted_vendor_binary(executable: str) -> str:
    if executable not in {"claude", "codex"}:
        raise ValueError("launch must invoke bare claude/codex directly")
    cached = _TRUSTED_VENDOR_BINARIES.get(executable)
    if cached and Path(cached).exists():
        return str(Path(cached).resolve())
    resolved = _discover_allowlisted_vendor_binary(executable)
    if resolved is None:
        raise FileNotFoundError(executable)
    _TRUSTED_VENDOR_BINARIES[executable] = resolved
    return resolved


def compute_doc_basis_id(
    *,
    route_id: str,
    worker_cwd: str,
    docs_source: str,
    docs_revision: str,
    doc_mode: str,
    doc_basis_paths: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Compute the cheap-validation doc basis fingerprint from current canonical inputs."""
    normalized_doc_basis_paths = _validated_doc_basis_paths(
        worker_cwd,
        doc_basis_paths,
        require_explicit=True,
    )
    verified_docs_revision = resolve_verified_docs_revision(
        worker_cwd=worker_cwd,
        docs_source=docs_source,
        doc_mode=doc_mode,
        doc_basis_paths=normalized_doc_basis_paths,
    )
    if docs_revision != verified_docs_revision:
        raise ValueError(
            "declared docs_revision does not match current approved basis "
            f"(expected {verified_docs_revision})"
        )
    payload = {
        "route_id": route_id,
        "docs_source": docs_source,
        "docs_revision": verified_docs_revision,
        "doc_mode": doc_mode,
        "doc_basis_paths": list(normalized_doc_basis_paths),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"db_{digest}"


def validate_doc_basis(
    *,
    route_id: str,
    worker_cwd: str,
    doc_basis_id: str,
    docs_source: str,
    docs_revision: str,
    doc_mode: str,
    doc_basis_paths: tuple[str, ...] | list[str] | None = None,
) -> tuple[bool, str]:
    """Return whether the declared doc basis matches the current cheap-validation fingerprint."""
    expected = compute_doc_basis_id(
        route_id=route_id,
        worker_cwd=worker_cwd,
        docs_source=docs_source,
        docs_revision=docs_revision,
        doc_mode=doc_mode,
        doc_basis_paths=doc_basis_paths,
    )
    return hmac.compare_digest(doc_basis_id, expected), expected


def _approved_projects_from_doc_basis_paths(
    worker_cwd: str,
    doc_basis_paths: tuple[str, ...] | list[str] | None,
) -> set[str]:
    repo_root = Path(_find_repo_root(worker_cwd)).resolve()
    normalized_doc_basis_paths = _validated_doc_basis_paths(
        worker_cwd,
        doc_basis_paths,
        require_explicit=False,
    )
    candidates: set[str] = set()
    for rel_text in normalized_doc_basis_paths:
        parts = Path(rel_text).parts
        if not parts:
            continue
        candidate = repo_root / parts[0]
        if (candidate / "AGENTS.md").exists():
            candidates.add(parts[0])
    return candidates


def _project_name_for_worker_cwd(worker_cwd: str) -> str | None:
    worker_path = Path(worker_cwd).resolve()
    repo_root = Path(_find_repo_root(worker_cwd)).resolve()
    try:
        rel = worker_path.relative_to(repo_root)
    except ValueError:
        return None
    if not rel.parts:
        return None
    candidate = repo_root / rel.parts[0]
    if (candidate / "AGENTS.md").exists():
        return rel.parts[0]
    return None


def validate_doc_basis_project_scope(
    *,
    worker_cwd: str,
    doc_basis_paths: tuple[str, ...] | list[str] | None,
) -> None:
    approved_projects = _approved_projects_from_doc_basis_paths(
        worker_cwd,
        doc_basis_paths,
    )
    if (
        approved_projects
        and _project_name_for_worker_cwd(worker_cwd) not in approved_projects
    ):
        raise ValueError("worker_cwd must match approved doc-basis project")


def identity_handoff_dir_for_cwd(session_cwd: str) -> Path:
    """Resolve the default handoff dir for a session cwd."""
    repo_root = _find_repo_root(session_cwd)
    return Path(repo_root) / ".claude" / "handoff"


def binding_path_for_id(handoff_dir: str | Path, binding_id: str) -> Path:
    handoff_dir = Path(handoff_dir)
    return handoff_dir / "active" / "session-identities" / f"{binding_id}.json"


def binding_records_for_cwd(
    handoff_dir: str | Path,
    session_cwd: str,
) -> list[dict[str, Any]]:
    normalized_cwd = str(Path(session_cwd).resolve())
    records: list[dict[str, Any]] = []
    for path in _binding_dir(handoff_dir).glob("*.json"):
        try:
            binding = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if binding.get("session_cwd") == normalized_cwd:
            records.append(binding)
    records.sort(key=lambda item: item.get("issued_at") or "", reverse=True)
    return records


def build_binding(
    *,
    session_cwd: str,
    task_id: str,
    route_id: str,
    worker_cwd: str,
    worktree_cwd: str,
    doc_basis_id: str,
    docs_source: str,
    docs_revision: str,
    doc_mode: str,
    doc_basis_paths: list[str] | tuple[str, ...] | None = None,
    launch_mode: str = "interactive",
    binding_id: str | None = None,
    token_hash: str | None = None,
    state: str = "issued",
    issued_at: str | None = None,
    expires_at: str | None = None,
    claim_session_id: str | None = None,
    claim_event_id: str | None = None,
    ack_session_id: str | None = None,
    ack_event_id: str | None = None,
    acknowledged_at: str | None = None,
    reject_reason: str | None = None,
    launch_session_pid: str | None = None,
) -> dict[str, Any]:
    """Build a persisted binding payload from explicit user-approved fields."""
    normalized_session_cwd = str(Path(session_cwd).resolve())
    normalized_worker_cwd = str(Path(worker_cwd).resolve())
    normalized_worktree_cwd = str(Path(worktree_cwd).resolve())
    git_head = _git_head_for_cwd(normalized_worker_cwd)
    if not git_head:
        raise ValueError("cannot bind session identity without a resolved git HEAD")
    issued = issued_at or _utc_now_iso()
    expires = expires_at or _utc_iso_after(BINDING_TTL_SECONDS)
    normalized_launch_mode = _normalize_launch_mode(launch_mode)
    return {
        "v": IDENTITY_SCHEMA_VERSION,
        "binding_id": binding_id or _new_binding_id(),
        "token_hash": token_hash or _token_hash(secrets.token_hex(16)),
        "state": state,
        "issued_at": issued,
        "expires_at": expires,
        "session_cwd": normalized_session_cwd,
        "git_head": git_head,
        "task_id": task_id,
        "route_id": route_id,
        "worker_cwd": normalized_worker_cwd,
        "worktree_cwd": normalized_worktree_cwd,
        "doc_basis_id": doc_basis_id,
        "docs_source": docs_source,
        "docs_revision": docs_revision,
        "doc_mode": doc_mode,
        "doc_basis_paths": list(doc_basis_paths or []),
        "launch_mode": normalized_launch_mode,
        "producer_schema_version": IDENTITY_SCHEMA_VERSION,
        "claim_session_id": claim_session_id,
        "claim_event_id": claim_event_id,
        "ack_session_id": ack_session_id,
        "ack_event_id": ack_event_id,
        "acknowledged_at": acknowledged_at,
        "reject_reason": reject_reason,
        "launch_session_pid": (
            launch_session_pid
            if launch_session_pid is not None
            else (None if normalized_launch_mode == "manual" else str(os.getppid()))
        ),
    }


def issue_binding(**kwargs: Any) -> tuple[dict[str, Any], str]:
    """Create a new binding record plus its one-time opaque launch token."""
    token = secrets.token_urlsafe(32)
    binding = build_binding(
        token_hash=_token_hash(token),
        **kwargs,
    )
    return binding, token


def write_binding(handoff_dir: str | Path, binding: dict[str, Any]) -> Path:
    """Atomically persist a binding for later hook enrichment."""
    path = binding_path_for_id(handoff_dir, binding["binding_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=".session-identity-", suffix=".tmp"
    )
    try:
        payload = json.dumps(binding, ensure_ascii=False, indent=2).encode("utf-8")
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(tmp_path, path)
    return path


def launch_bound_command(
    handoff_dir: str | Path,
    *,
    command: list[str],
    session_cwd: str,
    task_id: str,
    route_id: str,
    worker_cwd: str,
    worktree_cwd: str,
    doc_basis_id: str,
    docs_source: str,
    docs_revision: str,
    doc_mode: str,
    doc_basis_paths: tuple[str, ...] | list[str] | None = None,
) -> int:
    """Persist a binding, launch a command, and retire any unconsumed binding on exit."""
    if not command:
        raise ValueError("launch requires a command after --")
    _validate_supported_shell_wrappers(command)
    _validate_downstream_cwd_flags(command)
    _validate_binding_env_unsets(command)
    _validate_final_launch_target(command)
    launch_mode = _command_launch_mode(command)
    launch_command = _launch_execution_command(command)
    if not launch_command:
        raise ValueError("launch requires an executable command after --")
    if launch_command[0] not in {"claude", "codex"}:
        raise ValueError("launch must invoke bare claude/codex directly")
    resolved_executable = _resolved_trusted_vendor_binary(launch_command[0])
    validate_worktree_doc_mode(
        worker_cwd=worker_cwd,
        worktree_cwd=worktree_cwd,
        docs_source=docs_source,
        docs_revision=docs_revision,
        doc_mode=doc_mode,
        doc_basis_paths=doc_basis_paths,
    )
    normalized_doc_basis_paths = _validated_doc_basis_paths(
        worker_cwd,
        doc_basis_paths,
        require_explicit=True,
    )
    validate_doc_basis_project_scope(
        worker_cwd=worker_cwd,
        doc_basis_paths=normalized_doc_basis_paths,
    )
    verified_docs_revision = resolve_verified_docs_revision(
        worker_cwd=worker_cwd,
        docs_source=docs_source,
        doc_mode=doc_mode,
        doc_basis_paths=normalized_doc_basis_paths,
    )
    if docs_revision != verified_docs_revision:
        raise ValueError(
            "declared docs_revision does not match current approved basis "
            f"(expected {verified_docs_revision})"
        )
    if not _git_head_for_cwd(worker_cwd):
        raise ValueError("cannot bind session identity without a resolved git HEAD")
    doc_basis_valid, expected_doc_basis_id = validate_doc_basis(
        route_id=route_id,
        worker_cwd=worker_cwd,
        doc_basis_id=doc_basis_id,
        docs_source=docs_source,
        docs_revision=verified_docs_revision,
        doc_mode=doc_mode,
        doc_basis_paths=normalized_doc_basis_paths,
    )
    if not doc_basis_valid:
        raise ValueError(
            "declared doc_basis_id does not match current canonical basis "
            f"(expected {expected_doc_basis_id})"
        )
    binding, token = issue_binding(
        session_cwd=session_cwd,
        task_id=task_id,
        route_id=route_id,
        worker_cwd=worker_cwd,
        worktree_cwd=worktree_cwd,
        doc_basis_id=doc_basis_id,
        docs_source=docs_source,
        docs_revision=verified_docs_revision,
        doc_mode=doc_mode,
        doc_basis_paths=normalized_doc_basis_paths,
        launch_mode=launch_mode,
    )
    env = os.environ.copy()
    env[HARNESS_LAUNCH_BINDING_ID_ENV] = binding["binding_id"]
    env[HARNESS_LAUNCH_TOKEN_ENV] = token
    env[HARNESS_BINDING_SCHEMA_ENV] = str(IDENTITY_SCHEMA_VERSION)
    env[HARNESS_HANDOFF_DIR_ENV] = str(Path(handoff_dir).resolve())
    try:
        return _run_bound_vendor_process(
            handoff_dir,
            binding,
            env,
            resolved_executable,
            launch_command,
        )
    finally:
        _retire_unacknowledged_binding(handoff_dir, binding["binding_id"])


def _run_bound_vendor_process(
    handoff_dir: str | Path,
    binding: dict[str, Any],
    env: dict[str, str],
    resolved_executable: str,
    launch_command: list[str],
) -> int:
    ready_r, ready_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(ready_w)
            os.read(ready_r, 1)
            os.close(ready_r)
            child_env = dict(env)
            child_env[HARNESS_LAUNCH_SESSION_PID_ENV] = str(os.getpid())
            os.chdir(str(binding["worker_cwd"]))
            os.execve(
                resolved_executable,
                [resolved_executable, *launch_command[1:]],
                child_env,
            )
        except FileNotFoundError:
            os._exit(127)
        except Exception:
            os._exit(126)

    os.close(ready_r)
    released = False
    try:
        binding["launch_session_pid"] = str(pid)
        write_binding(handoff_dir, binding)
        os.write(ready_w, b"1")
        released = True
    finally:
        os.close(ready_w)
        if not released:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
    _, status = os.waitpid(pid, 0)
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return 1


def load_binding(
    handoff_dir: str | Path,
    session_cwd: str,
    *,
    git_head: str | None = None,
) -> dict[str, Any] | None:
    """Return a single valid issued binding for a cwd when it is unambiguous."""
    valid = [
        binding
        for binding in binding_records_for_cwd(handoff_dir, session_cwd)
        if _binding_validation_status(
            binding,
            session_cwd=session_cwd,
            git_head=git_head,
            require_token=False,
            require_issued=True,
        )
        == "valid"
    ]
    if len(valid) != 1:
        return None
    return valid[0]


def binding_handoff_dir_for_worker_cwd(worker_cwd: str) -> Path:
    """Resolve the default binding store for the actual worker checkout."""
    return identity_handoff_dir_for_cwd(worker_cwd)


def clear_binding(handoff_dir: str | Path, session_cwd: str) -> bool:
    """Remove non-acknowledged bindings for an exact session cwd."""
    removed = False
    for binding in binding_records_for_cwd(handoff_dir, session_cwd):
        if binding.get("state") == "acknowledged":
            continue
        removed = _clear_binding_by_id(handoff_dir, binding["binding_id"]) or removed
    return removed


def _apply_binding_to_event(
    event: dict[str, Any],
    binding: dict[str, Any],
    *,
    acknowledged: bool,
) -> dict[str, Any]:
    facts = event.setdefault("facts", {})
    for field in _IDENTITY_FIELDS:
        if facts.get(field) in (None, ""):
            facts[field] = binding.get(field)
    if facts.get("doc_basis_paths") in (None, ""):
        facts["doc_basis_paths"] = list(binding.get("doc_basis_paths") or [])
    facts["identity_acknowledged"] = acknowledged
    facts["identity_source"] = "binding"
    facts["identity_binding_id"] = binding.get("binding_id")
    facts["binding_state"] = "acknowledged" if acknowledged else "validated"
    facts["binding_launch_mode"] = _normalize_launch_mode(binding.get("launch_mode"))
    event["facts"] = facts
    return event


def _normalize_launch_mode(launch_mode: Any) -> str:
    if isinstance(launch_mode, str) and launch_mode in _LAUNCH_MODE_VALUES:
        return launch_mode
    return "interactive"


def _command_launch_mode(command: list[str]) -> str:
    command = _normalize_policy_command(command)
    executable = Path(command[0]).name if command else ""
    args = command[1:]
    if executable in _SHELL_WRAPPER_EXECUTABLES:
        payload = _shell_payload(args)
        if payload is None:
            return "interactive"
        simple = _simple_shell_command(payload)
        if simple is not None:
            return _command_launch_mode(simple)
        return "one-shot" if _shell_payload_mentions_one_shot(payload) else "interactive"
    if executable == "claude":
        for arg in args:
            if arg == "--":
                break
            if arg in {"-p", "--print"}:
                return "one-shot"
        return "interactive"
    if executable == "codex":
        codex_global_options = {
            "-c",
            "--config",
            "--enable",
            "--disable",
            "--remote",
            "--remote-auth-token-env",
            "-i",
            "--image",
            "-m",
            "--model",
            "--local-provider",
            "-p",
            "--profile",
            "-s",
            "--sandbox",
            "-a",
            "--ask-for-approval",
            "-C",
            "--cd",
            "--add-dir",
        }
        codex_flag_only_options = {
            "--oss",
            "--search",
            "--no-alt-screen",
            "--full-auto",
            "--dangerously-bypass-approvals-and-sandbox",
            "-h",
            "--help",
            "-V",
            "--version",
        }
        index = 0
        while index < len(args):
            arg = args[index]
            if arg == "--":
                return "interactive"
            if arg in {"exec", "e"}:
                return "one-shot"
            if arg == "review":
                return "one-shot"
            if not arg.startswith("-"):
                return "interactive"
            if arg in codex_flag_only_options:
                index += 1
                continue
            if any(arg.startswith(f"{option}=") for option in codex_global_options):
                index += 1
                continue
            if arg in codex_global_options:
                index += 2
                continue
            if any(arg.startswith(prefix) for prefix in ("-m", "-p", "-s", "-a", "-C")):
                index += 1
                continue
            index += 1
    return "interactive"


def _validate_downstream_cwd_flags(command: list[str]) -> None:
    command = _normalize_policy_command(
        command,
        reject_env_cwd=True,
    )
    executable = Path(command[0]).name if command else ""
    args = command[1:]
    if executable in _SHELL_WRAPPER_EXECUTABLES:
        payload = _shell_payload(args)
        if payload is None:
            return
        simple = _simple_shell_command(payload)
        if simple is not None:
            _validate_downstream_cwd_flags(simple)
            return
        if _shell_payload_mentions_downstream_codex_cd(payload):
            raise ValueError(
                "downstream codex -C/--cd is not supported; the launcher already controls worker_cwd"
            )
        return
    if executable != "codex":
        return
    for arg in args:
        if arg == "--":
            break
        if arg.startswith("-C") or arg in {"--cd"} or arg.startswith("--cd="):
            raise ValueError(
                "downstream codex -C/--cd is not supported; the launcher already controls worker_cwd"
            )


def _env_wrapped_command(
    args: list[str],
    *,
    reject_chdir: bool = False,
) -> list[str]:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            index += 1
            break
        if arg in {"-i", "--ignore-environment"}:
            index += 1
            continue
        if arg in {"-C", "--chdir"}:
            if reject_chdir:
                raise ValueError(
                    "env -C/--chdir is not supported; the launcher already controls worker_cwd"
                )
            index += 2
            continue
        if arg.startswith("-C") and len(arg) > 2:
            if reject_chdir:
                raise ValueError(
                    "env -C/--chdir is not supported; the launcher already controls worker_cwd"
                )
            index += 1
            continue
        if arg.startswith("--chdir="):
            if reject_chdir:
                raise ValueError(
                    "env -C/--chdir is not supported; the launcher already controls worker_cwd"
                )
            index += 1
            continue
        if arg in {"-u", "--unset"}:
            index += 2
            continue
        if arg.startswith("--unset="):
            index += 1
            continue
        if arg.startswith("-u") and len(arg) > 2:
            index += 1
            continue
        if arg in {"-S", "--split-string"}:
            if index + 1 >= len(args):
                return []
            try:
                split_args = shlex.split(args[index + 1])
            except ValueError:
                return []
            return _env_wrapped_command(
                [*split_args, *args[index + 2:]],
                reject_chdir=reject_chdir,
            )
        if arg.startswith("--split-string="):
            try:
                split_args = shlex.split(arg.split("=", 1)[1])
            except ValueError:
                return []
            return _env_wrapped_command(
                [*split_args, *args[index + 1:]],
                reject_chdir=reject_chdir,
            )
        if arg.startswith("-S") and len(arg) > 2:
            try:
                split_args = shlex.split(arg[2:])
            except ValueError:
                return []
            return _env_wrapped_command(
                [*split_args, *args[index + 1:]],
                reject_chdir=reject_chdir,
            )
        if arg.startswith("-") and not arg.startswith("--") and len(arg) > 2:
            consumed = _env_short_option_cluster(
                arg,
                next_args=args[index + 1:],
                reject_chdir=reject_chdir,
            )
            if consumed is not None:
                consumed_args, extra_skip = consumed
                return _env_wrapped_command(
                    [*consumed_args, *args[index + 1 + extra_skip:]],
                    reject_chdir=reject_chdir,
                )
            raise ValueError(f"unsupported env option bundle: {arg}")
        if arg.startswith("--"):
            raise ValueError(f"unsupported env option: {arg}")
        if arg.startswith("-"):
            raise ValueError(f"unsupported env option: {arg}")
        if "=" in arg and not arg.startswith("-"):
            index += 1
            continue
        break
    return args[index:]


def _env_short_option_cluster(
    arg: str,
    *,
    next_args: list[str],
    reject_chdir: bool,
) -> tuple[list[str], int] | None:
    cluster = arg[1:]
    if not cluster:
        return None
    remaining: list[str] = []
    extra_skip = 0
    index = 0
    while index < len(cluster):
        option = cluster[index]
        if option == "i":
            index += 1
            continue
        if option == "u":
            value = cluster[index + 1 :]
            if value:
                index = len(cluster)
            else:
                if not next_args:
                    return ([], 0)
                extra_skip = max(extra_skip, 1)
            return (remaining, extra_skip)
        if option == "C":
            value = cluster[index + 1 :]
            if reject_chdir:
                raise ValueError(
                    "env -C/--chdir is not supported; the launcher already controls worker_cwd"
                )
            if value:
                index = len(cluster)
            else:
                if not next_args:
                    return ([], 0)
                extra_skip = max(extra_skip, 1)
            return (remaining, extra_skip)
        if option == "S":
            split_source = cluster[index + 1 :]
            if not split_source:
                if not next_args:
                    return ([], 0)
                split_source = next_args[0]
                extra_skip = max(extra_skip, 1)
            try:
                split_args = shlex.split(split_source)
            except ValueError:
                return ([], 0)
            return ([*split_args, *remaining], extra_skip)
        return None
    return (remaining, extra_skip)


def _shell_payload(args: list[str]) -> str | None:
    payload_index = _shell_payload_index(args)
    if payload_index is None:
        return None
    return args[payload_index]


def _shell_payload_index(args: list[str]) -> int | None:
    for index, arg in enumerate(args):
        if arg == "--":
            break
        if arg == "-c":
            return index + 1 if index + 1 < len(args) else None
        if arg == "+c":
            return index + 1 if index + 1 < len(args) else None
        if arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]:
            return index + 1 if index + 1 < len(args) else None
    return None


def _shell_commands(payload: str) -> list[list[str]] | None:
    try:
        lexer = shlex.shlex(payload, posix=True, punctuation_chars=";&|()<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    if not tokens:
        return []
    commands: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in {";", "&", "&&", "||", "|"}:
            if current:
                commands.append(current)
                current = []
            continue
        current.append(token)
    if current:
        commands.append(current)
    return commands


def _simple_shell_command(payload: str) -> list[str] | None:
    if _shell_payload_uses_metasyntax(payload):
        return None
    commands = _shell_commands(payload)
    if not commands or len(commands) != 1:
        return None
    command = commands[0]
    candidate = _strip_leading_env_assignments(command)
    executable = candidate[0] if candidate else ""
    if executable not in {"claude", "codex", "env"}:
        return None
    return command


def _shell_payload_uses_metasyntax(payload: str) -> bool:
    return any(token in payload for token in ("\n", "\r", "$", "`", "<", ">"))


def _validate_supported_shell_wrappers(command: list[str]) -> None:
    command = _normalize_policy_command(
        command,
        reject_env_cwd=True,
    )
    executable = Path(command[0]).name if command else ""
    args = command[1:]
    if executable in _SHELL_WRAPPER_EXECUTABLES:
        payload = _shell_payload(args)
        if payload is None:
            return
        simple = _simple_shell_command(payload)
        if simple is None:
            raise ValueError(
                "non-trivial bash/sh -c wrappers are not supported; pass the target command directly"
            )
        if not _shell_wrapper_launch_is_rewrite_safe(simple):
            raise ValueError(
                "non-trivial bash/sh -c wrappers are not supported; pass the target command directly"
            )
        if _shell_wrapper_uses_unsafe_expansion(simple):
            raise ValueError(
                "non-trivial bash/sh -c wrappers are not supported; pass the target command directly"
            )
        _validate_supported_shell_wrappers(simple)


def _validate_binding_env_unsets(command: list[str]) -> None:
    if _leading_env_assignment_names(command) & _REQUIRED_BINDING_ENV_NAMES:
        raise ValueError(
            "launch may not clear, unset, or override required HARNESS_* binding env"
        )
    normalized = _strip_leading_env_assignments(command)
    if not normalized:
        return
    executable = Path(normalized[0]).name
    args = normalized[1:]
    if executable in _SHELL_WRAPPER_EXECUTABLES:
        payload = _shell_payload(args)
        if payload is None:
            return
        simple = _simple_shell_command(payload)
        if simple is not None:
            _validate_binding_env_unsets(simple)
        return
    if executable != "env":
        return
    if _env_uses_ignore_environment(args):
        raise ValueError(
            "launch may not clear, unset, or override required HARNESS_* binding env"
        )
    if _env_assignment_names(args) & _REQUIRED_BINDING_ENV_NAMES:
        raise ValueError(
            "launch may not clear, unset, or override required HARNESS_* binding env"
        )
    unset_names = _env_unset_names(args)
    if unset_names & _REQUIRED_BINDING_ENV_NAMES:
        raise ValueError(
            "launch may not clear, unset, or override required HARNESS_* binding env"
        )


def _final_launch_target(command: list[str]) -> list[str]:
    target = _launch_target_command(command)
    if not target:
        return []
    if target[0] != "env":
        return target
    return _env_command_remainder(target[1:])


def _launch_target_rebinds_path(command: list[str]) -> bool:
    if "PATH" in _leading_env_assignment_names(command):
        return True
    target = _launch_target_command(command)
    if target and target[0] == "env":
        return "PATH" in _env_assignment_names(target[1:])
    return False


def _validate_final_launch_target(command: list[str]) -> None:
    final_target = _final_launch_target(command)
    if not final_target:
        return
    executable = final_target[0]
    if _launch_target_rebinds_path(command) and executable in {"claude", "codex"}:
        raise ValueError(
            "launch may not override PATH while resolving claude/codex"
        )
    if executable not in {"claude", "codex"}:
        raise ValueError("launch must invoke bare claude/codex directly")


def _shell_payload_mentions_one_shot(payload: str) -> bool:
    commands = _shell_commands(payload) or []
    return any(
        _command_launch_mode(_normalize_policy_command(command)) == "one-shot"
        for command in commands
    )


def _shell_payload_mentions_downstream_codex_cd(payload: str) -> bool:
    commands = _shell_commands(payload) or []
    for command in commands:
        candidate = _normalize_policy_command(
            command,
            reject_env_cwd=True,
        )
        executable = Path(candidate[0]).name if candidate else ""
        if executable != "codex":
            continue
        for arg in candidate[1:]:
            if arg == "--":
                break
            if arg.startswith("-C") or arg in {"--cd"} or arg.startswith("--cd="):
                return True
    return False


def _strip_shell_prefix_tokens(command: list[str]) -> list[str]:
    candidate = _strip_leading_env_assignments(command)
    while candidate:
        token = candidate[0]
        if token not in {"then", "do", "elif", "exec", "command", "builtin", "(", "!"}:
            break
        candidate = _strip_leading_env_assignments(candidate[1:])
    return candidate


def _normalize_policy_command(
    command: list[str],
    *,
    reject_env_cwd: bool = False,
) -> list[str]:
    candidate = list(command)
    while True:
        previous = candidate
        candidate = _strip_leading_env_assignments(candidate)
        executable = Path(candidate[0]).name if candidate else ""
        if executable == "env":
            candidate = _env_wrapped_command(
                candidate[1:],
                reject_chdir=reject_env_cwd,
            )
            if not candidate:
                return candidate
            continue
        candidate = _strip_shell_prefix_tokens(candidate)
        if candidate == previous:
            return candidate


def _launch_target_command(command: list[str]) -> list[str]:
    candidate = list(command)
    if not candidate:
        return []
    executable = Path(candidate[0]).name
    if executable in _SHELL_WRAPPER_EXECUTABLES:
        payload = _shell_payload(candidate[1:])
        if payload is None:
            return candidate
        simple = _simple_shell_command(payload)
        if simple is None:
            return candidate
        return _launch_target_command(simple)
    if executable == "env":
        return _launch_target_env_command(candidate)
    return _collapse_launch_prefixes(candidate)


def _collapse_launch_prefixes(command: list[str]) -> list[str]:
    tokens = list(command)
    env_assignments: list[str] = []
    while tokens:
        while tokens and _is_env_assignment(tokens[0]):
            env_assignments.append(tokens.pop(0))
        if tokens and tokens[0] in {"then", "do", "elif", "exec", "command", "builtin", "(", "!"}:
            tokens.pop(0)
            continue
        break
    if not tokens:
        return []
    if env_assignments:
        if Path(tokens[0]).name == "env":
            return ["env", *env_assignments, *tokens[1:]]
        return ["env", *env_assignments, *tokens]
    return tokens


def _launch_execution_command(command: list[str]) -> list[str]:
    candidate = list(command)
    if not candidate:
        return []
    executable = Path(candidate[0]).name
    if executable not in _SHELL_WRAPPER_EXECUTABLES:
        return candidate
    payload_index = _shell_payload_index(candidate[1:])
    if payload_index is None:
        return candidate
    simple = _simple_shell_command(candidate[1:][payload_index])
    if simple is None:
        return candidate
    launch_target = _launch_target_command(simple)
    if not launch_target:
        return candidate
    rewritten = list(candidate)
    rewritten[1 + payload_index] = f"exec {shlex.join(launch_target)}"
    return rewritten


def _shell_wrapper_launch_is_rewrite_safe(command: list[str]) -> bool:
    candidate = list(command)
    if not candidate:
        return True
    if "PATH" in _leading_env_assignment_names(candidate):
        return False
    if Path(candidate[0]).name != "env":
        return True
    initial_remainder = _env_command_remainder(candidate[1:])
    if initial_remainder and initial_remainder[0] in {"command", "builtin", "exec"}:
        return False
    target = _launch_target_command(candidate)
    env_args = target[1:] if target and target[0] == "env" else candidate[1:]
    if "PATH" in _env_assignment_names(env_args):
        return False
    remainder = _env_command_remainder(env_args)
    if not remainder:
        return True
    return remainder[0] not in {"command", "builtin", "exec"}


def _shell_wrapper_uses_unsafe_expansion(command: list[str]) -> bool:
    target = _launch_target_command(command)
    if not target:
        return False
    final_command = target
    if Path(final_command[0]).name == "env":
        remainder = _env_command_remainder(final_command[1:])
        if not remainder:
            return False
        final_command = remainder
    if not final_command:
        return False
    executable = final_command[0]
    if executable not in {"claude", "codex"}:
        if Path(executable).name in {"claude", "codex"}:
            return True
        return False
    return any(
        any(marker in token for marker in ("~", "*", "?", "[", "]", "{", "}"))
        for token in final_command
    )


def _env_command_remainder(args: list[str]) -> list[str]:
    index = 0
    options_complete = False
    while index < len(args):
        arg = args[index]
        if not options_complete and arg == "--":
            options_complete = True
            index += 1
            continue
        if not options_complete and arg in {"-i", "--ignore-environment"}:
            index += 1
            continue
        if not options_complete and arg in {"-u", "--unset"}:
            index += 2
            continue
        if not options_complete and (arg.startswith("--unset=") or (arg.startswith("-u") and len(arg) > 2)):
            index += 1
            continue
        if not options_complete and arg in {"-S", "--split-string"}:
            if index + 1 >= len(args):
                return []
            try:
                split_args = shlex.split(args[index + 1])
            except ValueError:
                return []
            return [*split_args, *args[index + 2:]]
        if not options_complete and arg.startswith("--split-string="):
            try:
                split_args = shlex.split(arg.split("=", 1)[1])
            except ValueError:
                return []
            return [*split_args, *args[index + 1:]]
        if not options_complete and arg.startswith("-S") and len(arg) > 2:
            try:
                split_args = shlex.split(arg[2:])
            except ValueError:
                return []
            return [*split_args, *args[index + 1:]]
        if _is_env_assignment(arg):
            index += 1
            continue
        break
    return args[index:]


def _env_unset_names(args: list[str]) -> set[str]:
    names: set[str] = set()
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            break
        if arg in {"-i", "--ignore-environment"}:
            index += 1
            continue
        if arg in {"-C", "--chdir"}:
            index += 2
            continue
        if arg.startswith("-C") and len(arg) > 2:
            index += 1
            continue
        if arg.startswith("--chdir="):
            index += 1
            continue
        if arg in {"-u", "--unset"}:
            if index + 1 < len(args):
                names.add(args[index + 1])
            index += 2
            continue
        if arg.startswith("--unset="):
            names.add(arg.split("=", 1)[1])
            index += 1
            continue
        if arg.startswith("-u") and len(arg) > 2:
            names.add(arg[2:])
            index += 1
            continue
        if arg in {"-S", "--split-string"}:
            if index + 1 >= len(args):
                return names
            try:
                split_args = shlex.split(args[index + 1])
            except ValueError:
                return names
            return names | _env_unset_names([*split_args, *args[index + 2:]])
        if arg.startswith("--split-string="):
            try:
                split_args = shlex.split(arg.split("=", 1)[1])
            except ValueError:
                return names
            return names | _env_unset_names([*split_args, *args[index + 1:]])
        if arg.startswith("-S") and len(arg) > 2:
            try:
                split_args = shlex.split(arg[2:])
            except ValueError:
                return names
            return names | _env_unset_names([*split_args, *args[index + 1:]])
        if _is_env_assignment(arg):
            index += 1
            continue
        break
    return names


def _is_env_assignment(token: str) -> bool:
    return "=" in token and not token.startswith("-")


def _env_assignment_name(token: str) -> str | None:
    if not _is_env_assignment(token):
        return None
    return token.split("=", 1)[0]


def _leading_env_assignment_names(command: list[str]) -> set[str]:
    names: set[str] = set()
    for token in command:
        name = _env_assignment_name(token)
        if name is None:
            break
        names.add(name)
    return names


def _env_assignment_names(args: list[str]) -> set[str]:
    names: set[str] = set()
    index = 0
    options_complete = False
    while index < len(args):
        arg = args[index]
        if not options_complete and arg == "--":
            options_complete = True
            index += 1
            continue
        if not options_complete and arg in {"-i", "--ignore-environment"}:
            index += 1
            continue
        if not options_complete and arg in {"-C", "--chdir", "-u", "--unset"}:
            index += 2
            continue
        if not options_complete and (
            arg.startswith("-C")
            or arg.startswith("--chdir=")
            or arg.startswith("--unset=")
            or (arg.startswith("-u") and len(arg) > 2)
        ):
            index += 1
            continue
        if not options_complete and arg in {"-S", "--split-string"}:
            if index + 1 >= len(args):
                return names
            try:
                split_args = shlex.split(args[index + 1])
            except ValueError:
                return names
            return names | _env_assignment_names([*split_args, *args[index + 2:]])
        if not options_complete and arg.startswith("--split-string="):
            try:
                split_args = shlex.split(arg.split("=", 1)[1])
            except ValueError:
                return names
            return names | _env_assignment_names([*split_args, *args[index + 1:]])
        if not options_complete and arg.startswith("-S") and len(arg) > 2:
            try:
                split_args = shlex.split(arg[2:])
            except ValueError:
                return names
            return names | _env_assignment_names([*split_args, *args[index + 1:]])
        if _is_env_assignment(arg):
            name = _env_assignment_name(arg)
            if name is not None:
                names.add(name)
            index += 1
            continue
        break
    return names


def _env_uses_ignore_environment(args: list[str]) -> bool:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            return False
        if arg in {"-i", "--ignore-environment"}:
            return True
        if arg in {"-C", "--chdir", "-u", "--unset"}:
            index += 2
            continue
        if (
            arg.startswith("--chdir=")
            or arg.startswith("--unset=")
            or (arg.startswith("-u") and len(arg) > 2)
        ):
            index += 1
            continue
        if arg in {"-S", "--split-string"}:
            if index + 1 >= len(args):
                return False
            try:
                split_args = shlex.split(args[index + 1])
            except ValueError:
                return False
            return _env_uses_ignore_environment([*split_args, *args[index + 2:]])
        if arg.startswith("--split-string="):
            try:
                split_args = shlex.split(arg.split("=", 1)[1])
            except ValueError:
                return False
            return _env_uses_ignore_environment([*split_args, *args[index + 1:]])
        if arg.startswith("-S") and len(arg) > 2:
            try:
                split_args = shlex.split(arg[2:])
            except ValueError:
                return False
            return _env_uses_ignore_environment([*split_args, *args[index + 1:]])
        if arg.startswith("-") and not arg.startswith("--") and len(arg) > 2:
            cluster = arg[1:]
            cluster_index = 0
            while cluster_index < len(cluster):
                option = cluster[cluster_index]
                if option == "i":
                    return True
                if option in {"u", "C"}:
                    break
                if option == "S":
                    split_source = cluster[cluster_index + 1 :]
                    if not split_source:
                        if index + 1 >= len(args):
                            return False
                        split_source = args[index + 1]
                        remaining_args = args[index + 2 :]
                    else:
                        remaining_args = args[index + 1 :]
                    try:
                        split_args = shlex.split(split_source)
                    except ValueError:
                        return False
                    return _env_uses_ignore_environment([*split_args, *remaining_args])
                cluster_index += 1
            index += 1
            continue
        if _is_env_assignment(arg):
            index += 1
            continue
        break
    return False


def _launch_target_env_command(command: list[str]) -> list[str]:
    args = command[1:]
    prefix = ["env"]
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            index += 1
            break
        if arg in {"-i", "--ignore-environment"}:
            prefix.append(arg)
            index += 1
            continue
        if arg in {"-u", "--unset"}:
            if index + 1 >= len(args):
                return prefix
            prefix.extend(args[index:index + 2])
            index += 2
            continue
        if arg.startswith("--unset="):
            prefix.append(arg)
            index += 1
            continue
        if arg.startswith("-u") and len(arg) > 2:
            prefix.append(arg)
            index += 1
            continue
        if arg in {"-S", "--split-string"}:
            if index + 1 >= len(args):
                return prefix
            try:
                split_args = shlex.split(args[index + 1])
            except ValueError:
                return prefix
            inner = _launch_target_command([*split_args, *args[index + 2:]])
            if inner and inner[0] == "env":
                return [*prefix, *inner[1:]]
            return [*prefix, *inner] if inner else prefix
        if arg.startswith("--split-string="):
            try:
                split_args = shlex.split(arg.split("=", 1)[1])
            except ValueError:
                return prefix
            inner = _launch_target_command([*split_args, *args[index + 1:]])
            if inner and inner[0] == "env":
                return [*prefix, *inner[1:]]
            return [*prefix, *inner] if inner else prefix
        if arg.startswith("-S") and len(arg) > 2:
            try:
                split_args = shlex.split(arg[2:])
            except ValueError:
                return prefix
            inner = _launch_target_command([*split_args, *args[index + 1:]])
            if inner and inner[0] == "env":
                return [*prefix, *inner[1:]]
            return [*prefix, *inner] if inner else prefix
        if _is_env_assignment(arg):
            prefix.append(arg)
            index += 1
            continue
        break
    inner = _launch_target_command(args[index:])
    if inner and inner[0] == "env":
        return [*prefix, *inner[1:]]
    return [*prefix, *inner] if inner else prefix


def _strip_leading_env_assignments(command: list[str]) -> list[str]:
    index = 0
    while index < len(command):
        token = command[index]
        if "=" not in token or token.startswith("-"):
            break
        index += 1
    return command[index:] if index < len(command) else command


def _binding_matches_event_facts(
    event: dict[str, Any],
    binding: dict[str, Any],
) -> bool:
    facts = event.get("facts") or {}
    present_fields = [
        field for field in _IDENTITY_FIELDS if facts.get(field) not in (None, "")
    ]
    if not present_fields:
        return True
    if len(present_fields) != len(_IDENTITY_FIELDS):
        return False
    return all(facts.get(field) == binding.get(field) for field in _IDENTITY_FIELDS)


def enrich_event_identity(
    event: dict[str, Any],
    handoff_dir: str | Path,
    *,
    git_head: str | None = None,
    consume: bool = False,
) -> dict[str, Any]:
    """Fill missing identity fields on an event from a tokenized launch binding."""
    cwd = event.get("cwd")
    if not cwd:
        return event

    binding_id = os.environ.get(HARNESS_LAUNCH_BINDING_ID_ENV)
    token = os.environ.get(HARNESS_LAUNCH_TOKEN_ENV)
    if not binding_id or not token:
        return event

    normalized_cwd = str(Path(cwd).resolve())
    binding = _read_binding_by_id(handoff_dir, binding_id)
    require_token = True
    if not binding or not binding_id:
        return event
    if (
        _normalize_launch_mode(binding.get("launch_mode")) != "manual"
        and not _launch_session_pid_matches(binding)
    ):
        return event

    status = _binding_validation_status(
        binding,
        session_cwd=normalized_cwd,
        git_head=git_head,
        token=token,
        require_token=require_token,
        require_issued=True,
    )
    if status != "valid":
        _mark_binding_rejected(
            handoff_dir,
            binding_id,
            token=token,
            require_token=require_token,
            session_cwd=normalized_cwd,
            git_head=git_head,
            reason=status,
            session_id=event.get("session_id"),
            event_id=event.get("event_id"),
        )
        return event

    if not _binding_matches_event_facts(event, binding):
        _mark_binding_rejected(
            handoff_dir,
            binding_id,
            token=token,
            require_token=require_token,
            session_cwd=normalized_cwd,
            git_head=git_head,
            reason="explicit-mismatch",
            session_id=event.get("session_id"),
            event_id=event.get("event_id"),
        )
        return event
    if binding.get("state") == "issued" and not _claim_binding_candidate(
        handoff_dir,
        binding_id,
        token=token,
        require_token=require_token,
        session_cwd=normalized_cwd,
        git_head=git_head,
        session_id=event.get("session_id"),
        event_id=event.get("event_id"),
    ):
        return event
    binding = _read_binding_by_id(handoff_dir, binding_id) or binding

    if consume:
        acknowledged = _acknowledge_binding(
            handoff_dir,
            binding_id,
            token=token,
            require_token=require_token,
            session_cwd=normalized_cwd,
            git_head=git_head,
            session_id=event.get("session_id"),
            event_id=event.get("event_id"),
        )
        if not acknowledged:
            return event
        return _apply_binding_to_event(event, binding, acknowledged=True)

    return _apply_binding_to_event(event, binding, acknowledged=False)


def acknowledge_event_identity(
    event: dict[str, Any],
    handoff_dir: str | Path,
    *,
    git_head: str | None = None,
) -> bool:
    """Finalize a previously validated binding after SessionStart is durable."""
    cwd = event.get("cwd")
    if not cwd:
        return False
    facts = event.get("facts") or {}
    binding_id = facts.get("identity_binding_id")
    if not binding_id or facts.get("identity_source") != "binding":
        return False

    token = os.environ.get(HARNESS_LAUNCH_TOKEN_ENV)
    env_binding_id = os.environ.get(HARNESS_LAUNCH_BINDING_ID_ENV)
    if not token or env_binding_id != binding_id:
        return False
    binding = _read_binding_by_id(handoff_dir, str(binding_id))
    if binding and _normalize_launch_mode(binding.get("launch_mode")) == "manual":
        return False
    if not binding or not _launch_session_pid_matches(binding):
        return False
    acknowledged = _acknowledge_binding(
        handoff_dir,
        str(binding_id),
        token=token,
        require_token=True,
        session_cwd=str(Path(cwd).resolve()),
        git_head=git_head,
        session_id=event.get("session_id"),
        event_id=event.get("event_id"),
    )
    if not acknowledged:
        return False
    binding = _read_binding_by_id(handoff_dir, str(binding_id))
    if not binding:
        return False
    _apply_binding_to_event(event, binding, acknowledged=True)
    return True


def env_handoff_dir_matches_event(
    handoff_dir: str | Path,
    event: dict[str, Any],
    *,
    git_head: str | None = None,
) -> bool:
    """Return True when env binding metadata proves this store belongs to the current session."""
    cwd = event.get("cwd")
    binding_id = os.environ.get(HARNESS_LAUNCH_BINDING_ID_ENV)
    token = os.environ.get(HARNESS_LAUNCH_TOKEN_ENV)
    if not cwd or not binding_id or not token:
        return False

    binding = _read_binding_by_id(handoff_dir, binding_id)
    if not binding or binding.get("v") != IDENTITY_SCHEMA_VERSION:
        return False
    if not _launch_session_pid_matches(binding):
        return False
    if any(not binding.get(field) for field in _IDENTITY_FIELDS):
        return False
    launch_mode = _normalize_launch_mode(binding.get("launch_mode"))

    normalized_cwd = str(Path(cwd).resolve())
    binding_cwd = str(
        Path(binding.get("worker_cwd") or binding.get("session_cwd") or "").resolve()
    )
    binding_worktree_cwd = str(
        Path(binding.get("worktree_cwd") or binding_cwd).resolve()
    )
    normalized_repo_root = event.get("repo_root")
    if normalized_repo_root:
        normalized_repo_root = str(Path(normalized_repo_root).resolve())
        if normalized_repo_root != binding_worktree_cwd:
            return False
    token_hash = binding.get("token_hash")
    if not token_hash or not hmac.compare_digest(token_hash, _token_hash(token)):
        return False

    state = binding.get("state")
    if launch_mode == "one-shot":
        if event.get("hook_event") != "SessionStart" or state != "issued":
            return False
    if state == "issued":
        if event.get("hook_event") != "SessionStart":
            return False
        if normalized_cwd != binding_cwd:
            return False
        if not _binding_is_fresh(binding):
            return False
        binding_head = binding.get("git_head")
        if not binding_head or not git_head or binding_head != git_head:
            return False
        return True
    if state != "acknowledged":
        return False
    try:
        Path(normalized_cwd).relative_to(Path(binding_cwd))
    except ValueError:
        return False
    session_id = event.get("session_id")
    if session_id and binding.get("ack_session_id") not in (None, session_id):
        return False
    return True


def _launch_session_pid_matches(binding: dict[str, Any]) -> bool:
    expected_parent_pid = binding.get("launch_session_pid")
    if not expected_parent_pid:
        return False
    return str(expected_parent_pid) == str(os.getppid())


def binding_is_acknowledged(
    handoff_dir: str | Path,
    binding_id: str,
    *,
    session_id: str | None = None,
    event_id: str | None = None,
) -> bool:
    binding = _read_binding_by_id(handoff_dir, binding_id)
    if not binding or binding.get("state") != "acknowledged":
        return False
    if session_id and binding.get("ack_session_id") != session_id:
        return False
    if event_id and binding.get("ack_event_id") != event_id:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Manage explicit session identity bindings for harness hooks."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    bind_parser = subparsers.add_parser(
        "bind",
        help="Advanced/manual binding for an exact session cwd (not the canonical launcher path)",
    )
    bind_parser.add_argument("--handoff-dir", default=None)
    bind_parser.add_argument("--session-cwd", default=os.getcwd())
    bind_parser.add_argument("--task-id", required=True)
    bind_parser.add_argument("--route-id", required=True)
    bind_parser.add_argument("--worker-cwd", required=True)
    bind_parser.add_argument("--worktree-cwd", required=True)
    bind_parser.add_argument("--doc-basis-id", required=True)
    bind_parser.add_argument(
        "--docs-source",
        required=True,
        choices=("root-canonical", "branch-docs-approved"),
    )
    bind_parser.add_argument("--docs-revision", required=True)
    bind_parser.add_argument(
        "--doc-mode",
        required=True,
        choices=("root-canonical", "branch-docs-approved"),
    )
    bind_parser.add_argument(
        "--doc-basis-path",
        dest="doc_basis_paths",
        action="append",
        default=None,
    )

    launch_parser = subparsers.add_parser(
        "launch",
        help="Create a binding, run a command, and retire any unused binding on exit",
    )
    launch_parser.add_argument("--handoff-dir", default=None)
    launch_parser.add_argument("--session-cwd", default=os.getcwd())
    launch_parser.add_argument("--task-id", required=True)
    launch_parser.add_argument("--route-id", required=True)
    launch_parser.add_argument("--worker-cwd", required=True)
    launch_parser.add_argument("--worktree-cwd", required=True)
    launch_parser.add_argument("--doc-basis-id", required=True)
    launch_parser.add_argument(
        "--docs-source",
        required=True,
        choices=("root-canonical", "branch-docs-approved"),
    )
    launch_parser.add_argument("--docs-revision", required=True)
    launch_parser.add_argument(
        "--doc-mode",
        required=True,
        choices=("root-canonical", "branch-docs-approved"),
    )
    launch_parser.add_argument(
        "--doc-basis-path",
        dest="doc_basis_paths",
        action="append",
        default=None,
    )
    launch_parser.add_argument("launch_command", nargs=argparse.REMAINDER)

    show_parser = subparsers.add_parser("show", help="Show bindings for an exact session cwd")
    show_parser.add_argument("--handoff-dir", default=None)
    show_parser.add_argument("--session-cwd", default=os.getcwd())

    clear_parser = subparsers.add_parser("clear", help="Clear bindings for an exact session cwd")
    clear_parser.add_argument("--handoff-dir", default=None)
    clear_parser.add_argument("--session-cwd", default=os.getcwd())

    args = parser.parse_args(argv)
    if args.command == "launch" and args.handoff_dir:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": "launch manages handoff storage automatically",
                },
                ensure_ascii=False,
            )
        )
        return 1
    if args.handoff_dir:
        handoff_dir = Path(args.handoff_dir)
    elif args.command in {"bind", "launch"}:
        handoff_dir = binding_handoff_dir_for_worker_cwd(args.worker_cwd)
    else:
        handoff_dir = identity_handoff_dir_for_cwd(args.session_cwd)

    if args.command == "bind":
        try:
            validate_worktree_doc_mode(
                worker_cwd=args.worker_cwd,
                worktree_cwd=args.worktree_cwd,
                docs_source=args.docs_source,
                docs_revision=args.docs_revision,
                doc_mode=args.doc_mode,
                doc_basis_paths=args.doc_basis_paths,
            )
            normalized_doc_basis_paths = _validated_doc_basis_paths(
                args.worker_cwd,
                args.doc_basis_paths,
                require_explicit=True,
            )
            validate_doc_basis_project_scope(
                worker_cwd=args.worker_cwd,
                doc_basis_paths=normalized_doc_basis_paths,
            )
            verified_docs_revision = resolve_verified_docs_revision(
                worker_cwd=args.worker_cwd,
                docs_source=args.docs_source,
                doc_mode=args.doc_mode,
                doc_basis_paths=normalized_doc_basis_paths,
            )
            if args.docs_revision != verified_docs_revision:
                raise ValueError(
                    "declared docs_revision does not match current approved basis "
                    f"(expected {verified_docs_revision})"
                )
            doc_basis_valid, expected_doc_basis_id = validate_doc_basis(
                route_id=args.route_id,
                worker_cwd=args.worker_cwd,
                doc_basis_id=args.doc_basis_id,
                docs_source=args.docs_source,
                docs_revision=verified_docs_revision,
                doc_mode=args.doc_mode,
                doc_basis_paths=normalized_doc_basis_paths,
            )
            if not doc_basis_valid:
                raise ValueError(
                    "declared doc_basis_id does not match current canonical basis "
                    f"(expected {expected_doc_basis_id})"
                )
            binding, token = issue_binding(
                session_cwd=args.session_cwd,
                task_id=args.task_id,
                route_id=args.route_id,
                worker_cwd=args.worker_cwd,
                worktree_cwd=args.worktree_cwd,
                doc_basis_id=args.doc_basis_id,
                docs_source=args.docs_source,
                docs_revision=verified_docs_revision,
                doc_mode=args.doc_mode,
                doc_basis_paths=normalized_doc_basis_paths,
                launch_mode="manual",
            )
            path = write_binding(handoff_dir, binding)
        except ValueError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
            return 1
        except OSError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
            return 1
        print(
            json.dumps(
                {
                    "status": "bound",
                    "path": str(path),
                    "binding": binding,
                    "binding_id": binding["binding_id"],
                    "token": token,
                },
                ensure_ascii=False,
            )
        )
        return 0

    if args.command == "launch":
        command = list(args.launch_command)
        if command[:1] == ["--"]:
            command = command[1:]
        try:
            return launch_bound_command(
                handoff_dir,
                command=command,
                session_cwd=args.session_cwd,
                task_id=args.task_id,
                route_id=args.route_id,
                worker_cwd=args.worker_cwd,
                worktree_cwd=args.worktree_cwd,
                doc_basis_id=args.doc_basis_id,
                docs_source=args.docs_source,
                docs_revision=args.docs_revision,
                doc_mode=args.doc_mode,
                doc_basis_paths=args.doc_basis_paths,
            )
        except ValueError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
            return 1
        except FileNotFoundError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
            return 127
        except OSError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
            return 1

    if args.command == "show":
        session_cwd = str(Path(args.session_cwd).resolve())
        current_head = _git_head_for_cwd(session_cwd)
        bindings = binding_records_for_cwd(handoff_dir, session_cwd)
        payload_bindings = [
            {
                **binding,
                "validation_status": _binding_validation_status(
                    binding,
                    session_cwd=session_cwd,
                    git_head=current_head,
                    require_token=False,
                    require_issued=False,
                ),
            }
            for binding in bindings
        ]
        first = payload_bindings[0] if payload_bindings else None
        print(
            json.dumps(
                {
                    "status": "found" if bindings else "missing",
                    "validation_status": (
                        first.get("validation_status") if first else "missing"
                    ),
                    "binding": first,
                    "bindings": payload_bindings,
                },
                ensure_ascii=False,
            )
        )
        return 0

    removed = clear_binding(handoff_dir, args.session_cwd)
    print(json.dumps({"status": "cleared" if removed else "missing"}, ensure_ascii=False))
    return 0


def _binding_dir(handoff_dir: str | Path) -> Path:
    return Path(handoff_dir) / "active" / "session-identities"


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return ""


def _workspace_agents_sha256(repo_root: str) -> str:
    for parent in Path(repo_root).resolve().parents:
        agents = parent / "AGENTS.md"
        if agents.exists():
            return _file_sha256(agents)
    return ""


def _closest_repo_agents_path(worker_cwd: str) -> Path | None:
    repo_root = Path(_find_repo_root(worker_cwd)).resolve()
    current = Path(worker_cwd).resolve()
    if current.is_file():
        current = current.parent
    while True:
        agents = current / "AGENTS.md"
        if agents.exists():
            return agents
        if current == repo_root:
            break
        if repo_root not in current.parents:
            break
        current = current.parent
    return None


def _canonical_doc_relative_paths(worker_cwd: str) -> tuple[Path, ...]:
    repo_root = Path(_find_repo_root(worker_cwd)).resolve()
    paths = list(_CANONICAL_DOC_RELATIVE_PATHS)
    repo_local_agents = _closest_repo_agents_path(worker_cwd)
    if repo_local_agents is not None:
        try:
            rel_path = repo_local_agents.relative_to(repo_root)
        except ValueError:
            rel_path = None
        if rel_path is not None and rel_path not in paths:
            paths.append(rel_path)
    return tuple(paths)


def _validated_doc_basis_paths(
    worker_cwd: str,
    doc_basis_paths: tuple[str, ...] | list[str] | None,
    *,
    require_explicit: bool,
) -> tuple[str, ...]:
    repo_root = Path(_find_repo_root(worker_cwd)).resolve()
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_path in doc_basis_paths or ():
        raw_value = str(raw_path).strip()
        if not raw_value:
            continue
        candidate = Path(raw_value)
        if not candidate.is_absolute():
            candidate = (repo_root / candidate).resolve()
        else:
            candidate = candidate.resolve()
        try:
            rel_path = candidate.relative_to(repo_root)
        except ValueError as exc:
            raise ValueError(
                f"doc_basis_path must stay within worker repo: {raw_value}"
            ) from exc
        if not candidate.is_file():
            raise ValueError(f"doc_basis_path does not exist: {rel_path}")
        rel_text = str(rel_path)
        if rel_text in seen:
            continue
        seen.add(rel_text)
        normalized.append(rel_text)

    if not require_explicit:
        return tuple(normalized)

    if not normalized:
        raise ValueError(
            "binding-first launch requires explicit --doc-basis-path entries "
            "(include decision-log and approved spec)"
        )

    decision_log = str(_DECISION_LOG_RELATIVE_PATH)
    if (repo_root / _DECISION_LOG_RELATIVE_PATH).is_file() and decision_log not in seen:
        raise ValueError(
            f"binding-first launch requires --doc-basis-path {decision_log}"
        )

    if not any(path != decision_log for path in normalized):
        raise ValueError(
            "binding-first launch requires at least one non-decision-log --doc-basis-path"
        )
    return tuple(normalized)


def _canonical_doc_hashes(
    worker_cwd: str,
    *,
    repo_root: str | Path | None = None,
) -> dict[str, str]:
    repo_root_path = (
        Path(repo_root).resolve() if repo_root is not None else Path(_find_repo_root(worker_cwd)).resolve()
    )
    payload: dict[str, str] = {}
    for rel_path in _canonical_doc_relative_paths(worker_cwd):
        path = repo_root_path / rel_path
        payload[str(rel_path)] = _file_sha256(path) if path.is_file() else "<missing>"
    return payload


def _execution_mirror_hashes(repo_root: str | Path) -> dict[str, str]:
    repo_root_path = Path(repo_root).resolve()
    payload: dict[str, str] = {}
    for rel_path in _EXECUTION_MIRROR_RELATIVE_PATHS:
        path = repo_root_path / rel_path
        rel_text = str(rel_path)
        if path.is_file():
            payload[rel_text] = _file_sha256(path)
            continue
        if path.is_dir():
            files = sorted(p for p in path.rglob("*") if p.is_file())
            if not files:
                payload[rel_text] = "<empty-dir>"
                continue
            for file_path in files:
                payload[str(file_path.relative_to(repo_root_path))] = _file_sha256(file_path)
            continue
        payload[rel_text] = "<missing>"
    return payload


def _doc_basis_source_root(
    worker_cwd: str,
    *,
    docs_source: str,
) -> Path:
    worker_repo_root = Path(_find_repo_root(worker_cwd)).resolve()
    if docs_source != "root-canonical":
        return worker_repo_root
    canonical_repo_root = _git_common_checkout_root(worker_cwd)
    if canonical_repo_root:
        return Path(canonical_repo_root).resolve()
    return worker_repo_root


def _doc_basis_hashes(
    worker_cwd: str,
    *,
    docs_source: str,
    doc_basis_paths: tuple[str, ...] | list[str] | None,
) -> dict[str, str]:
    normalized_doc_basis_paths = _validated_doc_basis_paths(
        worker_cwd,
        doc_basis_paths,
        require_explicit=False,
    )
    basis_root = _doc_basis_source_root(worker_cwd, docs_source=docs_source)
    payload: dict[str, str] = {}
    for rel_text in normalized_doc_basis_paths:
        path = basis_root / rel_text
        payload[rel_text] = _file_sha256(path) if path.is_file() else "<missing>"
    return payload


def resolve_verified_docs_revision(
    *,
    worker_cwd: str,
    docs_source: str,
    doc_mode: str,
    doc_basis_paths: tuple[str, ...] | list[str] | None = None,
) -> str:
    normalized_doc_basis_paths = _validated_doc_basis_paths(
        worker_cwd,
        doc_basis_paths,
        require_explicit=False,
    )
    basis_root = _doc_basis_source_root(worker_cwd, docs_source=docs_source)
    payload = {
        "docs_source": docs_source,
        "doc_mode": doc_mode,
        "workspace_agents_sha256": _workspace_agents_sha256(str(basis_root)),
        "canonical_doc_hashes": _canonical_doc_hashes(worker_cwd, repo_root=basis_root),
        "execution_mirror_hashes": _execution_mirror_hashes(basis_root),
        "approved_doc_paths": list(normalized_doc_basis_paths),
        "approved_doc_hashes": _doc_basis_hashes(
            worker_cwd,
            docs_source=docs_source,
            doc_basis_paths=normalized_doc_basis_paths,
        ),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"drv_{digest}"


def _is_worktree_repo_root(path_value: str | Path) -> bool:
    git_path = Path(path_value).resolve() / ".git"
    if not git_path.is_file():
        return False
    try:
        git_ref = git_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return git_ref.startswith("gitdir:") and "/worktrees/" in git_ref.replace("\\", "/")


def _git_common_checkout_root(session_cwd: str) -> str | None:
    repo_root = _find_repo_root(session_cwd)
    git_path = Path(repo_root) / ".git"
    if git_path.is_file():
        try:
            git_ref = git_path.read_text(encoding="utf-8").strip()
        except OSError:
            git_ref = ""
        normalized_ref = git_ref.replace("\\", "/")
        marker = "/.git/worktrees/"
        if git_ref.startswith("gitdir:") and marker in normalized_ref:
            gitdir = normalized_ref[len("gitdir:") :].strip()
            prefix, _, _ = gitdir.partition(marker)
            if prefix:
                return str(Path(prefix).resolve())
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    common_dir = proc.stdout.strip()
    if not common_dir:
        return None
    common_path = Path(common_dir)
    if common_path.name != ".git":
        return None
    return str(common_path.parent.resolve())


def _root_canonical_paths_match(
    worker_repo_root: str,
    canonical_repo_root: str,
    *,
    worker_cwd: str,
    doc_basis_paths: tuple[str, ...] | list[str] | None = None,
) -> bool:
    worker_root = Path(worker_repo_root).resolve()
    canonical_root = Path(canonical_repo_root).resolve()
    normalized_doc_basis_paths = _validated_doc_basis_paths(
        worker_cwd,
        doc_basis_paths,
        require_explicit=False,
    )
    rel_paths: list[Path] = list(_canonical_doc_relative_paths(worker_cwd))
    for rel_text in normalized_doc_basis_paths:
        rel_path = Path(rel_text)
        if rel_path not in rel_paths:
            rel_paths.append(rel_path)
    for rel_path in rel_paths:
        worker_path = worker_root / rel_path
        canonical_path = canonical_root / rel_path
        if not worker_path.is_file() or not canonical_path.is_file():
            return False
        if _file_sha256(worker_path) != _file_sha256(canonical_path):
            return False
    if _execution_mirror_hashes(worker_root) != _execution_mirror_hashes(canonical_root):
        return False
    return True


def validate_worktree_doc_mode(
    *,
    worker_cwd: str,
    worktree_cwd: str,
    docs_source: str,
    docs_revision: str | None,
    doc_mode: str,
    doc_basis_paths: tuple[str, ...] | list[str] | None = None,
) -> None:
    worker_repo_root = Path(_find_repo_root(worker_cwd)).resolve()
    if Path(worktree_cwd).resolve() != worker_repo_root:
        raise ValueError("worktree_cwd must match worker_cwd git top-level")
    if not _is_worktree_repo_root(worker_repo_root):
        return
    if docs_source == "branch-docs-approved" or doc_mode == "branch-docs-approved":
        if (
            docs_source != "branch-docs-approved"
            or doc_mode != "branch-docs-approved"
            or not docs_revision
        ):
            raise ValueError(
                "worktree branch-docs-approved requires explicit "
                "--docs-source branch-docs-approved --doc-mode branch-docs-approved "
                "--docs-revision ..."
            )
        return
    canonical_repo_root = _git_common_checkout_root(worker_cwd)
    if not canonical_repo_root or not _root_canonical_paths_match(
        str(worker_repo_root),
        canonical_repo_root,
        worker_cwd=worker_cwd,
        doc_basis_paths=doc_basis_paths,
    ):
        raise ValueError(
            "drifted worktree worker_cwd requires explicit --docs-source "
            "branch-docs-approved --doc-mode branch-docs-approved --docs-revision ..."
        )


def _find_repo_root(cwd: str) -> str:
    p = Path(cwd).resolve()
    for parent in [p, *p.parents]:
        if (parent / ".git").exists():
            return str(parent)
    return str(p)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_iso_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_binding_id() -> str:
    return f"lb_{secrets.token_hex(12)}"


def _parse_utc(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _binding_is_fresh(binding: dict[str, Any]) -> bool:
    expires_at = _parse_utc(binding.get("expires_at"))
    if expires_at is None:
        issued_at = _parse_utc(binding.get("issued_at"))
        if issued_at is None:
            return False
        expires_at = issued_at + timedelta(seconds=BINDING_TTL_SECONDS)
    return datetime.now(timezone.utc) <= expires_at


def _read_binding_by_id(handoff_dir: str | Path, binding_id: str) -> dict[str, Any] | None:
    path = binding_path_for_id(handoff_dir, binding_id)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _binding_validation_status(
    binding: dict[str, Any],
    *,
    session_cwd: str | None,
    git_head: str | None = None,
    token: str | None = None,
    require_token: bool,
    require_issued: bool,
) -> str:
    if binding.get("v") != IDENTITY_SCHEMA_VERSION:
        return "wrong-version"
    binding_id = binding.get("binding_id")
    if not binding_id:
        return "missing-binding-id"
    if any(not binding.get(field) for field in _IDENTITY_FIELDS):
        return "incomplete-identity"
    if not _binding_is_fresh(binding):
        return "expired"
    if session_cwd is not None:
        normalized_cwd = str(Path(session_cwd).resolve())
        binding_cwd = str(
            Path(binding.get("worker_cwd") or binding.get("session_cwd") or "").resolve()
        )
        if binding_cwd != normalized_cwd:
            return "wrong-cwd"
    state = binding.get("state")
    if require_issued and state != "issued":
        return "wrong-state"
    if state in _TERMINAL_BINDING_STATES:
        return state
    binding_head = binding.get("git_head")
    if not binding_head:
        return "missing-binding-head"
    if not git_head:
        return "missing-current-head"
    if git_head != binding_head:
        return "head-mismatch"
    token_hash = binding.get("token_hash")
    if not token_hash:
        return "missing-token-hash"
    if require_token:
        if not token:
            return "missing-token"
        if not hmac.compare_digest(token_hash, _token_hash(token)):
            return "token-mismatch"
    return "valid"


def _clear_binding_by_id(handoff_dir: str | Path, binding_id: str) -> bool:
    path = binding_path_for_id(handoff_dir, binding_id)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def _retire_unacknowledged_binding(handoff_dir: str | Path, binding_id: str) -> None:
    path = binding_path_for_id(handoff_dir, binding_id)
    try:
        with path.open("r+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                binding = json.load(fh)
            except Exception:
                binding = None
            if isinstance(binding, dict) and binding.get("state") == "issued":
                binding["state"] = "expired"
                binding["reject_reason"] = "launcher-exited-before-ack"
                binding["expires_at"] = _utc_now_iso()
                fh.seek(0)
                json.dump(binding, fh, ensure_ascii=False, indent=2)
                fh.truncate()
                fh.flush()
                os.fsync(fh.fileno())
            fcntl.flock(fh, fcntl.LOCK_UN)
    except FileNotFoundError:
        return


def _apply_failed_consume_to_binding(
    binding: dict[str, Any],
    *,
    status: str,
    session_id: str | None,
    event_id: str | None,
) -> bool:
    if status in {"valid", "wrong-state", "missing-current-head", "missing-token"}:
        return False
    next_state = "expired" if status == "expired" else "rejected"
    if binding.get("state") != "issued":
        return False
    binding["state"] = next_state
    binding["reject_reason"] = status
    binding["ack_session_id"] = session_id
    binding["ack_event_id"] = event_id
    binding["acknowledged_at"] = _utc_now_iso()
    if next_state == "expired":
        binding["expires_at"] = _utc_now_iso()
    return True


def _acknowledge_binding(
    handoff_dir: str | Path,
    binding_id: str,
    *,
    token: str | None,
    require_token: bool,
    session_cwd: str,
    git_head: str | None,
    session_id: str | None,
    event_id: str | None,
) -> bool:
    path = binding_path_for_id(handoff_dir, binding_id)
    try:
        with path.open("r+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            binding = json.load(fh)
            status = _binding_validation_status(
                binding,
                session_cwd=session_cwd,
                git_head=git_head,
                token=token,
                require_token=require_token,
                require_issued=True,
            )
            if status != "valid":
                if _apply_failed_consume_to_binding(
                    binding,
                    status=status,
                    session_id=session_id,
                    event_id=event_id,
                ):
                    fh.seek(0)
                    json.dump(binding, fh, ensure_ascii=False, indent=2)
                    fh.truncate()
                    fh.flush()
                    os.fsync(fh.fileno())
                fcntl.flock(fh, fcntl.LOCK_UN)
                return False
            claim_session_id = binding.get("claim_session_id")
            claim_event_id = binding.get("claim_event_id")
            if (
                (claim_session_id not in (None, session_id))
                or (claim_event_id not in (None, event_id))
            ):
                fcntl.flock(fh, fcntl.LOCK_UN)
                return False
            binding["state"] = "acknowledged"
            binding["claim_session_id"] = session_id
            binding["claim_event_id"] = event_id
            binding["ack_session_id"] = session_id
            binding["ack_event_id"] = event_id
            binding["acknowledged_at"] = _utc_now_iso()
            fh.seek(0)
            json.dump(binding, fh, ensure_ascii=False, indent=2)
            fh.truncate()
            fh.flush()
            os.fsync(fh.fileno())
            fcntl.flock(fh, fcntl.LOCK_UN)
            return True
    except FileNotFoundError:
        return False


def revoke_acknowledged_binding(
    handoff_dir: str | Path,
    binding_id: str,
    *,
    session_id: str | None = None,
    event_id: str | None = None,
    reason: str = "ack-journal-append-failed",
) -> bool:
    """Reject an acknowledged binding when its durable ack event was not committed."""
    path = binding_path_for_id(handoff_dir, binding_id)
    try:
        with path.open("r+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            binding = json.load(fh)
            if binding.get("state") != "acknowledged":
                fcntl.flock(fh, fcntl.LOCK_UN)
                return False
            if session_id and binding.get("ack_session_id") != session_id:
                fcntl.flock(fh, fcntl.LOCK_UN)
                return False
            if event_id and binding.get("ack_event_id") != event_id:
                fcntl.flock(fh, fcntl.LOCK_UN)
                return False
            binding["state"] = "rejected"
            binding["reject_reason"] = reason
            binding["ack_session_id"] = None
            binding["ack_event_id"] = None
            binding["acknowledged_at"] = None
            fh.seek(0)
            json.dump(binding, fh, ensure_ascii=False, indent=2)
            fh.truncate()
            fh.flush()
            os.fsync(fh.fileno())
            fcntl.flock(fh, fcntl.LOCK_UN)
            return True
    except FileNotFoundError:
        return False


def _claim_binding_candidate(
    handoff_dir: str | Path,
    binding_id: str,
    *,
    token: str | None,
    require_token: bool,
    session_cwd: str,
    git_head: str | None,
    session_id: str | None,
    event_id: str | None,
) -> bool:
    if not session_id or not event_id:
        return False
    path = binding_path_for_id(handoff_dir, binding_id)
    try:
        with path.open("r+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            binding = json.load(fh)
            status = _binding_validation_status(
                binding,
                session_cwd=session_cwd,
                git_head=git_head,
                token=token,
                require_token=require_token,
                require_issued=True,
            )
            if status != "valid":
                fcntl.flock(fh, fcntl.LOCK_UN)
                return False
            claimed_session_id = binding.get("claim_session_id")
            claimed_event_id = binding.get("claim_event_id")
            if claimed_session_id or claimed_event_id:
                allowed = (
                    claimed_session_id == session_id and claimed_event_id == event_id
                )
                fcntl.flock(fh, fcntl.LOCK_UN)
                return allowed
            binding["claim_session_id"] = session_id
            binding["claim_event_id"] = event_id
            fh.seek(0)
            json.dump(binding, fh, ensure_ascii=False, indent=2)
            fh.truncate()
            fh.flush()
            os.fsync(fh.fileno())
            fcntl.flock(fh, fcntl.LOCK_UN)
            return True
    except FileNotFoundError:
        return False


def _mark_binding_rejected(
    handoff_dir: str | Path,
    binding_id: str,
    *,
    token: str | None,
    require_token: bool,
    session_cwd: str,
    git_head: str | None,
    reason: str,
    session_id: str | None,
    event_id: str | None,
) -> bool:
    path = binding_path_for_id(handoff_dir, binding_id)
    try:
        with path.open("r+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            binding = json.load(fh)
            status = _binding_validation_status(
                binding,
                session_cwd=session_cwd,
                git_head=git_head,
                token=token,
                require_token=require_token,
                require_issued=True,
            )
            if status != "valid":
                if _apply_failed_consume_to_binding(
                    binding,
                    status=status,
                    session_id=session_id,
                    event_id=event_id,
                ):
                    fh.seek(0)
                    json.dump(binding, fh, ensure_ascii=False, indent=2)
                    fh.truncate()
                    fh.flush()
                    os.fsync(fh.fileno())
                fcntl.flock(fh, fcntl.LOCK_UN)
                return False
            binding["state"] = "rejected"
            binding["reject_reason"] = reason
            binding["ack_session_id"] = session_id
            binding["ack_event_id"] = event_id
            binding["acknowledged_at"] = _utc_now_iso()
            fh.seek(0)
            json.dump(binding, fh, ensure_ascii=False, indent=2)
            fh.truncate()
            fh.flush()
            os.fsync(fh.fileno())
            fcntl.flock(fh, fcntl.LOCK_UN)
            return True
    except FileNotFoundError:
        return False


def _git_head_for_cwd(session_cwd: str) -> str | None:
    repo_root = _find_repo_root(session_cwd)
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


if __name__ == "__main__":
    raise SystemExit(main())
