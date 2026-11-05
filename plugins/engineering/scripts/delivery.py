"""One explicit StackOS engineering run with four separate Codex sessions.

The CLI owns sequencing while the daemon owns the durable run-plan and tracker
mirror. Recovery is bounded and persisted in the same run; uncertain effects are held.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import typer

from stackos.artifacts import redact_secret_text
from stackos.artifacts.checked_output import (
    CheckedConsole,
    CheckedStream,
    OutputPolicy,
    OutputRejected,
    atomic_write,
    private_policy_pipe,
    write_all,
)
from stackos.cli.app import delivery_app
from stackos.cli.operation_commands import _operation_call
from stackos.workflows.run_plan_schema import find_run_plan_secret_paths

DEFAULT_MODELS = {
    "planner": "gpt-6-astra",
    "executor": "gpt-6-luna",
    "reviewer": "gpt-6-astra",
    "test_writer": "gpt-6-luna",
}
ROLE_STEPS = (
    ("plan-task", "planner"),
    ("implement-task", "executor"),
    ("review-task", "reviewer"),
    ("write-tests", "test_writer"),
)
OUTPUT_KEYS = {
    "planner": "plan_receipt",
    "executor": "implementation_receipt",
    "reviewer": "review_receipt",
    "test_writer": "test_receipt",
}
WORKFLOW_VERSION = "0.7.0"
MAX_PROMPT_BYTES = 200_000
MAX_EVENT_BYTES = 2_000_000


_recovery_spec = importlib.util.spec_from_file_location(
    "_stackos_engineering_recovery", Path(__file__).with_name("delivery_recovery.py")
)
if _recovery_spec is None or _recovery_spec.loader is None:
    raise ImportError("engineering recovery controller is unavailable")
_recovery = importlib.util.module_from_spec(_recovery_spec)
_recovery_spec.loader.exec_module(_recovery)


_workspace_spec = importlib.util.spec_from_file_location(
    "_stackos_controlled_workspace", Path(__file__).with_name("controlled_workspace.py")
)
if _workspace_spec is None or _workspace_spec.loader is None:
    raise ImportError("controlled worker workspace is unavailable")
_workspace = importlib.util.module_from_spec(_workspace_spec)
_workspace_spec.loader.exec_module(_workspace)
_test_path = _workspace.test_path
_outcome_selection = _workspace.outcome_selection

_diagnostics_spec = importlib.util.spec_from_file_location(
    "_stackos_host_diagnostics", Path(__file__).with_name("host_diagnostics.py")
)
if _diagnostics_spec is None or _diagnostics_spec.loader is None:
    raise ImportError("engineering host diagnostics are unavailable")
_diagnostics = importlib.util.module_from_spec(_diagnostics_spec)
_diagnostics_spec.loader.exec_module(_diagnostics)


# Workers need host CLI discovery and login location, not controller/provider
# credentials, Python injection variables, or inherited application state.
_WORKER_ENV_KEYS = frozenset(
    {
        "HOME",
        "USER",
        "LOGNAME",
        "PATH",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "CODEX_HOME",
        "SYSTEMROOT",
        "WINDIR",
    }
)
_SECRET_ENV_PARTS = (
    "key",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "authorization",
    "connection_string",
    "database_url",
    "dsn",
)


def _capture_worker_environment() -> tuple[dict[str, str], tuple[str, ...]]:
    inherited = dict(os.environ)
    environment = {key: value for key, value in inherited.items() if key in _WORKER_ENV_KEYS}
    secrets = tuple(
        sorted(
            {
                value
                for key, value in inherited.items()
                if value
                and key not in _WORKER_ENV_KEYS
                and any(part in key.casefold() for part in _SECRET_ENV_PARTS)
            },
            key=len,
            reverse=True,
        )
    )
    return environment, secrets


_OUTPUT_POLICY = None
_ENVIRONMENT = None
_CONSOLE = None


def _policy():
    global _OUTPUT_POLICY, _ENVIRONMENT
    if _OUTPUT_POLICY is None:
        _ENVIRONMENT, values = _capture_worker_environment()
        _OUTPUT_POLICY = OutputPolicy(values)
    return _OUTPUT_POLICY


def _worker_environment():
    policy = _policy()
    return dict(_ENVIRONMENT), policy.values


def _console():
    global _CONSOLE
    if _CONSOLE is None:
        _CONSOLE = CheckedConsole(_policy(), sys.stdout.buffer, sys.stderr.buffer)
    return _CONSOLE


def _echo(text, *, err=False):
    _console().write((sys.stderr if err else sys.stdout).buffer,
                     _policy().encoded_text(text, suffix="\n"))


def _checked_invocation(function):
    @wraps(function)
    def invoke(*args, **kwargs):
        global _OUTPUT_POLICY, _ENVIRONMENT, _CONSOLE
        _OUTPUT_POLICY, _ENVIRONMENT, _CONSOLE = None, None, None
        try:
            _policy()
            return function(*args, **kwargs)
        except OutputRejected:
            # Even a static fallback can collide with a short inherited value.
            raise typer.Exit(code=2) from None
        except Exception as exc:
            if isinstance(exc, typer.Exit):
                raise
            with contextlib.suppress(Exception):
                _echo(_sanitize(str(exc)), err=True)
            raise typer.Exit(code=2) from None
        finally:
            if _CONSOLE is not None:
                _CONSOLE.close()
    return invoke


def _sanitize(value: Any, known_secrets: tuple[str, ...] | None = None) -> Any:
    """Remove known credential values as well as labelled secrets before retention."""
    if known_secrets is None:
        _environment, known_secrets = _worker_environment()
    # Codex turn-completion usage is numeric accounting, not credentials.
    # Normalize only these exact protocol fields before generic redaction so
    # daemon key-based secret checks do not confuse counts with auth tokens.
    if isinstance(value, dict) and value.get("type") == "turn.completed":
        usage = value.get("usage")
        names = {
            "input_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
            "total_tokens",
        }
        if (
            isinstance(usage, dict)
            and set(usage) <= names
            and all(
                isinstance(count, int) and not isinstance(count, bool) and count >= 0
                for count in usage.values()
            )
        ):
            value = dict(value)
            value.pop("usage")
            value["usage_counts"] = {
                name.removesuffix("_tokens") + "_count": count for name, count in usage.items()
            }
    if isinstance(value, dict):
        retained = {}
        omitted = 0
        for key, item in value.items():
            safe_key = _sanitize(str(key), known_secrets)
            # The canonical run-plan policy forbids secret-labelled keys even
            # when their values are redacted or null. Omit those fields rather
            # than retaining rejected placeholders or changing the validator.
            if find_run_plan_secret_paths({safe_key: None}):
                omitted += 1
                continue
            retained[safe_key] = _sanitize(item, known_secrets)
        if omitted:
            retained["redacted_field_count"] = omitted
        _policy().require(retained)
        return retained
    if isinstance(value, list):
        retained = [_sanitize(item, known_secrets) for item in value]
        _policy().require(retained)
        return retained
    if isinstance(value, str):
        value = redact_secret_text(value)
        value = _policy().retain_text(value)
    _policy().require(value)
    return value


def _utc() -> str:
    return datetime.now(UTC).isoformat()


def _safe_text(value: str, *, label: str) -> str:
    _policy().text(value)
    if not value.strip() or _sanitize(value) != value:
        raise ValueError(f"{label} is empty or contains secret-like content")
    return value


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    inner = payload.get("data")
    return inner if isinstance(inner, dict) else payload


def _call(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = _operation_call(
        operation, {**arguments, "response_mode": "raw"}, output_policy=_policy(),
        diagnostic_stream=_console(),
    )
    if not isinstance(result, dict):
        raise ValueError(f"{operation} returned no object")
    return _data(result)


def _git(repo: Path, *args: str) -> bytes:
    command = ["git", "-C", str(repo), *args]
    _policy().require(command)
    return subprocess.check_output(command, stderr=subprocess.PIPE)


def _repo_head(repo: Path) -> str:
    if not (repo / ".git").is_file():
        raise ValueError("--repo must be a separate linked worktree, not the primary checkout")
    if Path(os.fsdecode(_git(repo, "rev-parse", "--show-toplevel")).strip()).resolve() != repo:
        raise ValueError("--repo must name the exact isolated Git worktree root")
    if _git(repo, "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("--repo must start clean; preserve other owners' edits")
    return _git(repo, "rev-parse", "HEAD").decode().strip()


def _changed_paths(repo: Path) -> list[str]:
    tracked = _git(repo, "diff", "--no-renames", "--name-only", "HEAD", "-z").decode().split("\0")
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")
    return sorted({path for path in (*tracked, *untracked) if path})


def _file_digests(repo: Path, paths: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in paths:
        file = repo / path
        if file.is_symlink():
            raise ValueError(f"symlink in changed code: {path}")
        result[path] = (
            f"{file.stat().st_mode:o}:" + hashlib.sha256(file.read_bytes()).hexdigest()
            if file.is_file()
            else "deleted"
        )
    return result


def _actual_code(repo: Path, *, max_bytes: int) -> tuple[str, str, dict[str, str]]:
    paths = _changed_paths(repo)
    if not paths:
        raise ValueError("executor produced no changed code")
    for path in paths:
        _outcome_selection(path, changed=True)
    chunks = [_git(repo, "diff", "--binary", "HEAD")]
    untracked = set(
        _git(repo, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")
    )
    for path in paths:
        if path in untracked or (repo / path).is_file():
            file = repo / path
            if not file.is_file() or file.is_symlink():
                raise ValueError(f"unreviewable untracked path: {path}")
            chunks.append(f"\n--- complete changed file {path} ---\n".encode() + file.read_bytes())
    data = b"\n".join(chunks)
    if len(data) > max_bytes:
        raise ValueError("actual code exceeds reviewer input limit; review is blocked")
    try:
        code = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("binary code requires a separate review path") from exc
    _safe_text(code, label="actual code")
    return code, hashlib.sha256(data).hexdigest(), _file_digests(repo, paths)


def _outcome_snapshot(repo: Path, destination: Path, *, worker: bool = False) -> dict[str, Any]:
    """Materialize a bounded controller-selected outcome/dependency manifest.

    Unchanged untracked files have no source provenance and are not dependencies.
    Every changed path must be included or explicitly held, including deletions.
    All selected bytes pass secret scanning before any tool can disclose them.
    Guidance/config files are inert data: the isolated host never loads them.
    """
    tracked = set(_git(repo, "ls-files", "-z").decode().split("\0")) - {""}
    changed = set(_changed_paths(repo))
    # Git confirms deletions relative to the frozen base, including staged
    # removals and renamed-away paths. A merely missing dependency is not one.
    confirmed_deleted = set(
        _git(repo, "diff", "HEAD", "--no-renames", "--diff-filter=D", "--name-only", "-z")
        .decode()
        .split("\0")
    ) - {""}
    selected = {}
    excluded = []
    for relative in sorted(tracked | changed):
        reason = _outcome_selection(relative, changed=relative in changed, worker=worker)
        if reason:
            selected[relative] = reason
        else:
            excluded.append(relative)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(mode=0o700)
    total = 0
    files = []
    deleted = []
    for relative, reason in selected.items():
        _policy().text(relative)
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
            raise ValueError("invalid outcome snapshot path")
        # Open every source path component without following symlinks. This
        # also prevents a racing replacement from importing host files.
        descriptors = [os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)]
        try:
            try:
                for part in path.parts[:-1]:
                    descriptors.append(
                        os.open(
                            part,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptors[-1],
                        )
                    )
                descriptor = os.open(
                    path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptors[-1]
                )
            except FileNotFoundError as exc:
                if relative not in changed or relative not in confirmed_deleted:
                    raise ValueError(f"selected dependency disappeared: {relative}") from exc
                deleted.append(relative)
                continue
            descriptors.append(descriptor)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(f"outcome snapshot rejects special files: {relative}")
            total += before.st_size
            if before.st_size > 20_000_000 or total > 200_000_000:
                raise ValueError("outcome snapshot exceeds bounded size; review is held")
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                data = stream.read(before.st_size + 1)
            after = os.fstat(descriptor)
            if len(data) != before.st_size or (
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("outcome changed while capturing snapshot")
            # Scan every file, including unchanged dependencies and embedded
            # text in binary assets. Rejection preserves actual artifact bytes.
            binary_asset = path.suffix.lower() in {
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".webp",
                ".woff",
                ".woff2",
                ".ttf",
            }
            try:
                text = data.decode("utf-8", errors="ignore" if binary_asset else "strict")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    f"review held: selected non-media artifact is not inspectable UTF-8: {relative}"
                ) from exc
            if _sanitize(text) != text:
                raise ValueError(
                    f"review held: selected artifact contains secret-like data: {relative}"
                )
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o600 | (before.st_mode & 0o111))
            files.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "mode": 0o600 | (before.st_mode & 0o111),
                    "selection": reason,
                }
            )
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
    if (
        set(_changed_paths(repo)) != changed
        or set(
            _git(repo, "diff", "HEAD", "--no-renames", "--diff-filter=D", "--name-only", "-z")
            .decode()
            .split("\0")
        )
        - {""}
        != confirmed_deleted
    ):
        raise ValueError("candidate path/deletion set changed while capturing outcome")
    manifest = {
        "version": 1,
        "selection_contract": "controller-source-outcome-v1",
        "files": files,
        "deleted": deleted,
        "changed_paths": sorted(changed),
        "excluded_paths": excluded,
    }
    data = _policy().encoded_json(manifest, sort_keys=True, separators=(",", ":"), suffix="\n")
    manifest_path = destination.parent / "outcome-manifest.json"
    atomic_write(manifest_path, data, policy=_policy())
    manifest_path.chmod(0o600)
    return {"sha256": hashlib.sha256(data).hexdigest(), **manifest}


def _models(project_id: int, overrides: dict[str, str | None]) -> dict[str, str]:
    extension = _call(
        "workflowExtension.get",
        {"project_id": project_id, "workflow_key": "engineering.tracked-delivery"},
    ).get("extension")
    defaults = {}
    if isinstance(extension, dict) and extension.get("enabled"):
        context = extension.get("selected_context_json") or {}
        defaults = context.get("agent_models") or {}
    if not isinstance(defaults, dict) or any(key not in DEFAULT_MODELS for key in defaults):
        raise ValueError("project engineering agent_models must map only the four role names")
    resolved = {
        **DEFAULT_MODELS,
        **defaults,
        **{key: value for key, value in overrides.items() if value},
    }
    if any(not isinstance(value, str) or not value.strip() for value in resolved.values()):
        raise ValueError("every role needs an explicit nonempty model")
    return resolved


def _schema(role: str) -> dict[str, Any]:
    if role == "designer":
        refs = {
            "type": "array",
            "items": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
            "minItems": 1,
            "description": "Exact bare failure_id values from durable failure evidence; "
            "include the current failure ID. No suffixes, artifact refs or episode IDs.",
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "classification",
                "decision",
                "plan",
                "rationale",
                "cause",
                "authority",
                "evidence_refs",
                "comparisons",
            ],
            "properties": {
                "classification": {"type": "string", "enum": ["design", "execution"]},
                "decision": {"type": "string", "enum": ["stop", "revise", "try_again"]},
                "plan": {"type": "string"},
                "rationale": {"type": "string"},
                "cause": {"type": "string"},
                "evidence_refs": refs,
                "authority": {"type": "string", "enum": ["clear", "denied", "uncertain"]},
                "comparisons": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["episode_id", "relation", "rationale", "evidence_refs"],
                        "properties": {
                            "episode_id": {"type": "string"},
                            "relation": {
                                "type": "string",
                                "enum": ["same", "distinct", "uncertain"],
                            },
                            "rationale": {"type": "string"},
                            "evidence_refs": refs,
                        },
                    },
                },
            },
        }
    fields = (
        *{
            "planner": ("status", "plan", "question"),
            "executor": ("status", "summary"),
            "reviewer": ("verdict", "findings", "missing_requirements", "irrelevant_changes"),
            "test_writer": ("status", "summary", "tests_written"),
        }[role],
        "progress",
        "issues",
    )
    properties: dict[str, Any] = {key: {"type": "string"} for key in fields}
    for key in (
        "findings",
        "missing_requirements",
        "irrelevant_changes",
        "tests_written",
        "progress",
        "issues",
    ):
        if key in properties:
            properties[key] = {"type": "array", "items": {"type": "string"}}
    if role == "reviewer":
        properties["verdict"]["enum"] = ["approve", "reject"]
    else:
        properties["status"]["enum"] = (
            ["complete", "needs_clarification", "incomplete"]
            if role == "planner"
            else ["complete", "incomplete"]
        )
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(fields),
        "properties": properties,
    }


def _prompt(role: str, task: str, *, plan: str = "", code: str = "") -> str:
    if role == "planner":
        return (
            f"Original task and requirements:\n{task}\n\n"
            "Return JSON with status=complete and a concrete plan, or "
            "status=needs_clarification and one question. Include progress and issues arrays. "
            "Do not edit files or write tests."
        )
    if role == "executor":
        return (
            f"Original requirements:\n{task}\n\nApproved plan:\n{plan}\n\n"
            "Implement and document only this plan in the current isolated worktree. "
            "Do not write tests yet. Return JSON with status, summary, progress and issues. "
            "Stop and report ambiguity."
        )
    if role == "reviewer":
        return (
            f"Original requirements:\n{task}\n\nActual changed code "
            f"(not an executor report):\n{code}\n\n"
            "Review the actual code against every original requirement. Identify "
            "missing, incorrect, irrelevant or sloppy changes. Return JSON with "
            "verdict=approve only if logic and scope match; otherwise reject with "
            "findings, progress and issues. Do not edit files or write tests."
        )
    return (
        f"Original requirements:\n{task}\n\nImplementation code approved "
        f"by a separate reviewer:\n{code}\n\n"
        "Write only relevant tests for this approved logic in the isolated worktree, "
        "then run the affected checks. Do not change implementation logic. "
        "Return JSON with status, summary, tests_written, progress and issues."
    )


_SUPERVISOR = r"""
import json, os, select, signal, subprocess, sys, time
lease, result, policy_fd = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])
try:
    child = subprocess.Popen(sys.argv[4:], close_fds=True,
                             pass_fds=() if policy_fd < 0 else (policy_fd,))
    if policy_fd >= 0:
        os.close(policy_fd)
    while child.poll() is None:
        if select.select([lease], [], [], 0.1)[0] and not os.read(lease, 1):
            os.killpg(os.getpgrp(), signal.SIGKILL)
    with open(result + ".tmp", "x") as stream:
        json.dump({"exit_code": child.returncode}, stream)
    os.replace(result + ".tmp", result)
    while True:
        if select.select([lease], [], [], 0.1)[0] and not os.read(lease, 1):
            os.killpg(os.getpgrp(), signal.SIGKILL)
except BaseException:
    os.killpg(os.getpgrp(), signal.SIGKILL)
"""


def _execute(
    command: list[str],
    repo: Path,
    seconds: int,
    stdout: Any,
    stderr: Any,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Bound input/output and retain only sanitized logs; own the original group."""
    environment, known_secrets = _worker_environment()
    _policy().require(command)
    _policy().require(environment)
    if prompt is not None:
        _policy().text(prompt, limit=MAX_PROMPT_BYTES)
    lease_read, lease_write = os.pipe()
    policy_fd = -1
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    process = None
    selector = selectors.DefaultSelector()
    with (
        tempfile.TemporaryDirectory(prefix="stackos-process-") as directory,
        tempfile.TemporaryFile() as input_file,
    ):
        result_path = Path(directory) / "exit.json"
        if prompt is not None:
            write_all(input_file, _policy().encoded_text(prompt, limit=MAX_PROMPT_BYTES))
            input_file.seek(0)
        started = time.monotonic()

        def drain(timeout: float) -> None:
            for key, _mask in selector.select(timeout):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffers[key.data].extend(chunk)
                if len(buffers[key.data]) > MAX_EVENT_BYTES:
                    raise ValueError("worker output exceeded bounded capture limit")

        try:
            if prompt is not None:
                policy_fd = private_policy_pipe(_policy())
                command = [*command, "--policy-fd", str(policy_fd)]
            launch = [sys.executable, "-c", _SUPERVISOR, str(lease_read), str(result_path),
                      str(policy_fd), *command]
            _policy().require(launch)
            _policy().require(environment)
            process = subprocess.Popen(
                launch,
                cwd=repo,
                env=environment,
                stdin=input_file,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                pass_fds=(lease_read,) if policy_fd < 0 else (lease_read, policy_fd),
            )
            os.close(lease_read)
            lease_read = -1
            if policy_fd >= 0:
                os.close(policy_fd)
                policy_fd = -1
            for name in buffers:
                pipe = getattr(process, name)
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, name)
            while not result_path.exists() and time.monotonic() - started < seconds:
                drain(0.05)
            result = (
                json.loads(result_path.read_text()) | {"timed_out": False}
                if result_path.exists()
                else {"exit_code": None, "timed_out": True}
            )
        finally:
            try:
                if process is not None:
                    # The group leader has never been reaped and reserves its ID.
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                    until = time.monotonic() + 5
                    while selector.get_map() and time.monotonic() < until:
                        drain(0.05)
                    if selector.get_map():
                        raise ValueError("worker streams remain open after group cleanup")
            finally:
                os.close(lease_write)
                if lease_read != -1:
                    os.close(lease_read)
                if policy_fd >= 0:
                    os.close(policy_fd)
                selector.close()
                retained = []
                for name, target in (("stdout", stdout), ("stderr", stderr)):
                    value = buffers[name].decode("utf-8", errors="replace")
                    if prompt is not None and name == "stdout":
                        lines = []
                        for line in value.splitlines():
                            try:
                                event = json.loads(line)
                                # Preserve typed host refusals before secret-labelled
                                # diagnostic keys are removed. Never scan tool output.
                                if isinstance(event, dict) and _recovery.denied(event):
                                    event["host_refusal"] = True
                                lines.append(json.dumps(_sanitize(event, known_secrets)))
                            except json.JSONDecodeError:
                                lines.append(_sanitize(line, known_secrets))
                        value = "\n".join(lines)
                    else:
                        value = _sanitize(value, known_secrets)
                    # The entire retained stream, including joined event framing,
                    # is admitted before its first physical write.
                    retained.append((target, _policy().encoded_text(value)))
                for target, data in retained:
                    CheckedStream(_policy()).write(target, data)
        return {
            **result,
            "group_cleared": True,
            "group_leaked": False,
            "custody_scope": "original process group; detached sessions are not contained",
        }


def _observations(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    data = _policy().decode_json(_safe_text(path.read_text(), label="observation specification"))
    if not isinstance(data, list) or not 1 <= len(data) <= 20:
        raise ValueError("observation specification must contain 1 to 20 checks")
    for check in data:
        if (
            not isinstance(check, dict)
            or not {"requirement", "argv", "expected_stdout"} <= set(check)
            or set(check) - {"requirement", "argv", "expected_stdout", "confinement"}
            or check.get("confinement", "unconfined") not in {"unconfined", "read_only"}
            or not isinstance(check["requirement"], str)
            or not check["requirement"].strip()
            or not isinstance(check["argv"], list)
            or not check["argv"]
            or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in check["argv"])
            or not isinstance(check["expected_stdout"], str)
        ):
            raise ValueError(
                "each observation needs requirement, argv, expected_stdout "
                "and optional read_only confinement"
            )
    return data


class ObservationFailed(ValueError):
    def __init__(self, reason: str, receipts: list[dict[str, Any]]) -> None:
        super().__init__(_sanitize(reason))
        self.receipts = _sanitize(receipts)


def _observe(repo: Path, checks: list[dict[str, Any]], seconds: int) -> list[dict[str, Any]]:
    receipts = []
    for check in checks:
        _policy().require(check)
        receipt = {
            **check,
            "started_at": _utc(),
            "exit_code": None,
            "timed_out": False,
            "status": "held",
            "confinement": {"mode": check.get("confinement", "unconfined"), "verified": False},
            "refusal_state": "unknown",
        }
        error = None
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                if check.get("confinement") == "read_only":
                    binary = shutil.which("codex")
                    if not binary:
                        raise ValueError("Codex CLI is not installed")
                    with tempfile.TemporaryDirectory(prefix="stackos-observation-host-") as host:
                        command = [
                            sys.executable,
                            str(Path(__file__).with_name("reviewer_isolation.py")),
                            "--observe",
                            binary,
                            str(repo),
                            str(seconds),
                            host,
                        ]
                        # The broker admits no model turn. Its receipt certifies the
                        # exact read-only/no-network command profile before dispatch.
                        custody = _execute(
                            command, repo, seconds, out, err, json.dumps({"argv": check["argv"]})
                        )
                    out.seek(0)
                    stream = out.read(MAX_EVENT_BYTES + 1)
                    if len(stream) > MAX_EVENT_BYTES:
                        raise ValueError("confined observation event stream exceeds limit")
                    rows = [
                        _policy().decode_json(line)
                        for line in stream.decode().splitlines()
                    ]
                    if any(type(row) is not dict for row in rows):
                        raise ValueError("confined observation event is malformed")
                    receipt["host_diagnostics"] = [
                        row for row in rows if row.get("type") != "observation.completed"
                    ]
                    if any(_recovery.denied(row) for row in rows):
                        receipt["host_refusal"] = True
                    if (
                        not rows
                        or rows[-1].get("type") != "observation.completed"
                        or not all(_diagnostics.observation_telemetry(row) for row in rows[:-1])
                        or receipt.get("host_refusal")
                        or custody.get("exit_code") != 0
                        or custody.get("timed_out")
                    ):
                        raise ValueError(
                            "confined observation broker did not return a complete receipt"
                        )
                    observed = rows[-1]
                    receipt.update(custody)
                    receipt.update(
                        {
                            key: observed[key]
                            for key in ("exit_code", "confinement", "refusal_state")
                        }
                    )
                    if observed.get("host_refusal"):
                        receipt["host_refusal"] = True
                    stdout_bytes = _policy().encoded_text(_sanitize(observed["stdout"]))
                    stderr_bytes = _policy().encoded_text(_sanitize(observed["stderr"]))
                    out.seek(0)
                    out.truncate()
                    write_all(out, stdout_bytes)
                    err.seek(0)
                    err.truncate()
                    write_all(err, stderr_bytes)
                else:
                    receipt["confinement"] = {"mode": "unconfined", "verified": False}
                    receipt["refusal_state"] = "unknown"
                    receipt.update(_execute(check["argv"], repo, seconds, out, err))
            except Exception as exc:
                error = _sanitize(str(exc))
            finally:
                # _execute sanitizes even its exception output before writing
                # these anonymous files. Read before closing; retain earlier
                # checks too when this one fails.
                out.seek(0)
                err.seek(0)
                stdout = out.read(MAX_EVENT_BYTES + 1)
                stderr = err.read(MAX_EVENT_BYTES + 1)
                receipt.update(
                    {
                        "stdout": stdout[:MAX_EVENT_BYTES].decode("utf-8", errors="replace"),
                        "stderr": stderr[:MAX_EVENT_BYTES].decode("utf-8", errors="replace"),
                        "ended_at": _utc(),
                    }
                )
                if max(len(stdout), len(stderr)) > MAX_EVENT_BYTES:
                    error = "observation output limit exceeded"
        if error is None and (
            receipt["timed_out"]
            or receipt["exit_code"] != 0
            or receipt["stdout"] != check["expected_stdout"]
        ):
            error = "integrated observation failed"
        receipt["status"] = "held" if error else "complete"
        if error:
            receipt["error"] = error
        receipts.append(_sanitize(receipt))
        if error:
            raise ObservationFailed(error, receipts)
    return receipts


def _assert_base(repo: Path, head: str) -> None:
    if _git(repo, "rev-parse", "HEAD").decode().strip() != head:
        raise ValueError("worker changed the base commit; candidate must be reconciled")


class RoleNotStarted(ValueError):
    """Controller preparation ended before process dispatch."""


def _run_role_impl(
    role: str,
    model: str,
    prompt: str,
    repo: Path,
    seconds: int,
    run_id: int,
    *,
    review_state: Path | None = None,
    resume_thread: str = "",
    worker_state: Path | None = None,
) -> dict[str, Any]:
    try:
        if len(prompt.encode()) > MAX_PROMPT_BYTES:
            raise ValueError("role prompt exceeds bounded input limit")
        _safe_text(prompt, label=f"{role} prompt")
        binary = shutil.which("codex")
        if not binary:
            raise ValueError("Codex CLI is not installed")
        schema_bytes = _policy().encoded_json(_schema(role))
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".json", prefix="stackos-role-schema-", delete=False
        ) as schema_file:
            schema_path = Path(schema_file.name)
        atomic_write(schema_path, schema_bytes, policy=_policy())
        os.chmod(schema_path, 0o600)
        log_dir = Path(tempfile.gettempdir()) / "stackos-delivery-logs"
        log_dir.mkdir(mode=0o700, exist_ok=True)
        if log_dir.is_symlink() or log_dir.stat().st_uid != os.getuid():
            raise ValueError("role log directory is not owned by this controller")
        log_dir.chmod(0o700)
        out_path = log_dir / f"run-{run_id}-{role}-{uuid4().hex[:8]}.jsonl"
        err_path = out_path.with_suffix(".stderr")
        binary = _workspace.native_binary(binary, output_policy=_policy())
        if role != "reviewer":
            if worker_state is None:
                raise ValueError("ordinary role requires a retained private host")
            if Path(__file__).resolve().is_relative_to(repo.resolve()):
                raise ValueError(
                    "use an installed trusted controller outside the candidate repository"
                )
            manifest_bytes = (worker_state / "outcome-manifest.json").read_bytes()
            command = [
                sys.executable,
                str(Path(__file__).with_name("reviewer_isolation.py")),
                "--worker",
                role,
                binary,
                str(repo),
                str(schema_path),
                model,
                str(seconds),
                str(worker_state / "host"),
                resume_thread,
                str(worker_state / "outcome"),
                hashlib.sha256(manifest_bytes).hexdigest(),
            ]
        outcome_manifest = (
            None
            if role == "reviewer"
            else {
                "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                **_policy().decode_json(manifest_bytes),
            }
        )
        if role == "reviewer":
            if review_state is None:
                raise ValueError("reviewer requires an isolated outcome runtime")
            manifest_bytes = (review_state / "outcome-manifest.json").read_bytes()
            outcome_manifest = {
                "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                **_policy().decode_json(manifest_bytes),
            }
            # Keep process custody and bounded capture in the existing supervisor.
            # The adapter itself admits the model only after its host handshake.
            command = [
                sys.executable,
                str(Path(__file__).with_name("reviewer_isolation.py")),
                binary,
                str(review_state / "outcome"),
                str(schema_path),
                model,
                str(seconds),
                str(review_state / "host"),
                resume_thread,
                outcome_manifest["sha256"],
            ]
    except Exception as exc:
        raise RoleNotStarted(str(exc)) from exc
    started_at, start = _utc(), time.monotonic()
    timed_out = False
    group_leaked = False
    group_cleared = False
    receipt = {
        "role": role,
        "model": model,
        "requested_model": model,
        "resolved_model": None,
        "prompt": prompt,
        "started_at": started_at,
        "thread_id": None,
        "status": "held",
        "event_log": str(out_path),
        "stderr_log": str(err_path),
        "events": [],
        "progress": [],
        "exit_code": None,
        "group_cleared": False,
        "outcome_manifest": outcome_manifest,
    }
    try:
        out_fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            err_fd = os.open(err_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            os.close(out_fd)
            raise
        with os.fdopen(out_fd, "wb") as stdout, os.fdopen(err_fd, "wb") as stderr:
            execution = _execute(command, repo, seconds, stdout, stderr, prompt)
        receipt.update(execution)
        timed_out = execution["timed_out"]
        group_leaked = execution["group_leaked"]
        group_cleared = execution["group_cleared"]
        if out_path.stat().st_size > MAX_EVENT_BYTES:
            raise ValueError("role event stream exceeded output limit; effect status unknown")
        events = [
            _policy().decode_json(line)
            for line in out_path.read_text(errors="replace").splitlines()
        ]
        thread_ids = [
            event.get("thread_id") for event in events if event.get("type") == "thread.started"
        ]
        completions = [event for event in events if event.get("type") == "turn.completed"]
        messages = [
            event["item"].get("text")
            for event in events
            if event.get("type") == "item.completed"
            and isinstance(event.get("item"), dict)
            and event["item"].get("type") == "agent_message"
        ]
        progress = [
            {
                "event": str(event.get("type", "unknown")),
                "item_type": str(event.get("item", {}).get("type", ""))
                if isinstance(event.get("item"), dict)
                else "",
                "item_status": str(event.get("item", {}).get("status", ""))
                if isinstance(event.get("item"), dict)
                else "",
            }
            for event in events
            if event.get("type") not in {"turn.started", "thread.started"}
        ][:40]
        receipt = {
            "thread_id": thread_ids[0] if len(thread_ids) == 1 else None,
            "role": role,
            "model": model,
            "requested_model": model,
            "resolved_model": next(
                (
                    event.get("resolved_model")
                    for event in events
                    if event.get("type") == "isolation.verified"
                ),
                None,
            ),
            "started_at": started_at,
            "ended_at": _utc(),
            "duration_seconds": round(time.monotonic() - start, 2),
            "exit_code": execution["exit_code"],
            "timed_out": timed_out,
            "group_leaked": group_leaked,
            "group_cleared": group_cleared,
            "event_count": len(events),
            "progress": progress,
            "events": _sanitize(events),
            "usage_counts": completions[-1].get("usage_counts") if completions else None,
            "event_log": str(out_path),
            "stderr_log": str(err_path),
            "outcome_manifest": outcome_manifest,
            "status": "held"
            if timed_out
            or group_leaked
            or not group_cleared
            or execution["exit_code"] != 0
            or len(completions) != 1
            or len(thread_ids) != 1
            else "complete",
        }
        if receipt["resolved_model"] != model:
            receipt["status"] = "held"
            receipt["error"] = "host-resolved model does not match configured model"
        receipt["effect_disposition"] = _recovery.role_effect_disposition(role, receipt)
        if receipt["status"] == "complete":
            if outcome_manifest is not None:
                admissions = [
                    event for event in events if event.get("type") == "isolation.verified"
                ]
                if (
                    len(admissions) != 1
                    or admissions[0].get("outcome_manifest_sha256") != outcome_manifest["sha256"]
                ):
                    raise ValueError("review receipt lacks matching outcome manifest admission")
            try:
                receipt["answer"] = _policy().decode_json(messages[-1]) if messages else None
            except json.JSONDecodeError:
                receipt["status"] = "held"
        return _sanitize(receipt)
    except Exception as exc:
        # The process may already have produced identity/progress even when
        # transport, custody, size limits or JSON parsing fails. Keep that
        # evidence linked from the blocked step; never infer completion.
        receipt.update(
            {
                "status": "held",
                "error": _sanitize(str(exc)),
                "ended_at": _utc(),
                "duration_seconds": round(time.monotonic() - start, 2),
            }
        )
        partial = []
        try:
            with out_path.open("rb") as stream:
                data = stream.read(MAX_EVENT_BYTES)
            for line in data.decode("utf-8", errors="replace").splitlines():
                try:
                    event = _policy().decode_json(line)
                except OutputRejected:
                    continue
                if isinstance(event, dict):
                    partial.append(event)
            receipt["events"] = partial
            receipt["event_count"] = len(partial)
            identities = {
                event.get("thread_id")
                for event in partial
                if event.get("type") == "thread.started" and event.get("thread_id")
            }
            receipt["thread_id"] = next(iter(identities)) if len(identities) == 1 else None
            receipt["progress"] = [
                event for event in partial if event.get("type") != "thread.started"
            ][:40]
        except OSError as read_exc:
            receipt["evidence_read_error"] = _sanitize(str(read_exc))
        receipt["effect_disposition"] = _recovery.role_effect_disposition(role, receipt)
        return _sanitize(receipt)
    finally:
        schema_path.unlink(missing_ok=True)


def _run_role(*args, **kwargs):
    # Only the explicitly typed pre-dispatch region proves no launch.
    # Unexpected post-dispatch errors leave ledger intent unresolved.
    try:
        return _run_role_impl(*args, **kwargs)
    except RoleNotStarted as exc:
        return {
            "role": args[0],
            "model": args[1],
            "requested_model": args[1],
            "resolved_model": None,
            "status": "held",
            "error": _sanitize(str(exc)),
            "dispatch_state": "not_started",
            "custody": {"worker_launched": False},
            "effect_disposition": {
                "state": "settled",
                "basis": "controller pre-dispatch; no worker launched",
            },
        }


def _receipt_step(project_id: int, plan_id: int, run_id: int, step: str) -> dict[str, Any]:
    scope = {"project_id": project_id, "run_plan_id": plan_id}
    plan = _call("runPlan.get", scope)
    if (plan.get("id"), plan.get("project_id"), plan.get("run_id")) != (
        plan_id,
        project_id,
        run_id,
    ):
        raise ValueError("receipt reconciliation returned a different plan or run")
    persisted = _call("runPlan.getStep", {**scope, "step_id": step})
    if (persisted.get("run_plan_id"), persisted.get("step_id")) != (plan_id, step):
        raise ValueError("receipt reconciliation returned a different step")
    return persisted


def _record(
    project_id: int,
    plan_id: int,
    token: str,
    step: str,
    status: str,
    result: dict[str, Any],
    *,
    run_id: int,
) -> None:
    requested = _sanitize(_recovery.evidence(result))
    # Compare JSON representations so Python's True == 1 cannot accept a
    # different persisted receipt. Raw reads preserve the complete result.
    expected_json = json.dumps(requested, sort_keys=True, allow_nan=False)

    def matches(persisted: dict[str, Any]) -> bool:
        return (
            persisted.get("status") == status
            and "error" in persisted
            and persisted["error"] is None
            and json.dumps(persisted.get("result_json"), sort_keys=True, allow_nan=False)
            == expected_json
        )

    persisted = _receipt_step(project_id, plan_id, run_id, step)
    if matches(persisted):
        return
    if persisted.get("status") != "running":
        raise ValueError("step already has a different receipt; inspect before continuation")
    try:
        _call(
            "runPlan.recordStep",
            {
                "project_id": project_id,
                "run_plan_id": plan_id,
                "step_id": step,
                "status": status,
                "result_json": requested,
                "run_token": token,
            },
        )
    except Exception:
        # The CLI adapter erases transport/server/denial distinctions. Reconcile
        # by reading only; even a running step cannot prove the request will not
        # commit later. Never replay a write or relaunch a worker here.
        try:
            persisted = _receipt_step(project_id, plan_id, run_id, step)
            if matches(persisted):
                return
        except Exception:
            pass
        # Keep the original write exception if the canonical read failed,
        # mismatched, or still showed running. The caller holds the delivery.
        _echo(
            f"{step}: receipt write remains uncertain; inspect plan {plan_id}, "
            f"run {run_id} before continuation; no write retry performed",
            err=True,
        )
        raise


@delivery_app.command(name="run")
@_checked_invocation
def run_delivery(
    project_id: Annotated[int, typer.Option("--project", help="Existing StackOS project ID.")],
    repo: Annotated[Path, typer.Option("--repo", help="Clean isolated Git worktree.")],
    task_file: Annotated[
        Path, typer.Option("--task-file", help="Original requirements as UTF-8 text.")
    ],
    observation_file: Annotated[
        Path | None,
        typer.Option("--observation-file", help="Predeclared integrated behavior checks."),
    ] = None,
    confirm: Annotated[
        bool, typer.Option("--confirm", help="Launch the four bounded agent sessions.")
    ] = False,
    planner_model: Annotated[str | None, typer.Option("--planner-model")] = None,
    executor_model: Annotated[str | None, typer.Option("--executor-model")] = None,
    reviewer_model: Annotated[str | None, typer.Option("--reviewer-model")] = None,
    test_writer_model: Annotated[str | None, typer.Option("--test-writer-model")] = None,
    role_seconds: Annotated[int, typer.Option("--role-seconds", min=30, max=900)] = 180,
    resume_plan: Annotated[
        int | None, typer.Option("--resume-plan", help="Continue this same frozen recovery plan.")
    ] = None,
    max_code_bytes: Annotated[
        int, typer.Option("--max-code-bytes", min=1024, max=500_000)
    ] = 120_000,
) -> None:
    """Create a real run, then launch planner, implementer, reviewer and test writer."""
    if not confirm:
        raise typer.BadParameter("--confirm is required before model execution")
    repo = repo.resolve()
    if Path(__file__).resolve().is_relative_to(repo):
        raise typer.BadParameter(
            "use the installed trusted controller outside the candidate repository"
        )
    controller_lock = _recovery.controller_lock(repo)
    controller_lock.__enter__()
    try:
        task = _safe_text(task_file.read_text(encoding="utf-8"), label="original requirements")
        existing = None
        if resume_plan is not None:
            existing = _call("runPlan.get", {"project_id": project_id, "run_plan_id": resume_plan})
            if (
                existing.get("template_version") != WORKFLOW_VERSION
                or (existing.get("workflow_contract") or {}).get("status") != "current"
            ):
                raise ValueError("frozen workflow differs; reconcile contract before resume")
            saved_inputs = existing.get("inputs_json") or {}
            saved_context = saved_inputs.get("repo_context") or {}
            if (
                existing.get("project_id") != project_id
                or saved_inputs.get("goal") != task
                or saved_context.get("repo") != str(repo)
                or not (repo / ".git").is_file()
            ):
                raise ValueError(
                    "resume scope, original task, or worktree differs from frozen plan"
                )
            head = saved_context["base_commit"]
            _assert_base(repo, head)
        else:
            head = _repo_head(repo)
        observations = _observations(observation_file)
        models = _models(
            project_id,
            {
                "planner": planner_model,
                "executor": executor_model,
                "reviewer": reviewer_model,
                "test_writer": test_writer_model,
            },
        )
        effective = _call(
            "workflowTemplate.describe",
            {"project_id": project_id, "key": "engineering.tracked-delivery"},
        ).get("spec")
        if not isinstance(effective, dict) or effective.get("version") != WORKFLOW_VERSION:
            raise ValueError(
                "installed engineering workflow is not the reviewed four-session version"
            )
        if [step.get("id") for step in effective.get("steps", [])] != [
            *(step for step, _role in ROLE_STEPS),
            "handoff",
        ]:
            raise ValueError("installed engineering workflow has unexpected steps")
        guidance = _call(
            "agentPreset.resolveForWorkflow",
            {
                "project_id": project_id,
                "workflow_key": "engineering.tracked-delivery",
            },
        )
        if guidance.get("unresolved_requirements") or guidance.get(
            "unresolved_skill_preset_requirements"
        ):
            raise ValueError("workflow role or orchestrator guidance is unresolved")
        role_guidance = {
            item["role"].replace("-", "_"): item for item in guidance.get("required_agents", [])
        }
        if any(role not in role_guidance for _step, role in ROLE_STEPS):
            raise ValueError("effective workflow lacks one of the four role contracts")
        if existing is None:
            plan = _call(
                "runPlan.create",
                {
                    "project_id": project_id,
                    "template_key": "engineering.tracked-delivery",
                    "plugin_slug": "engineering",
                    "key": f"engineering-four-session-{uuid4().hex[:16]}",
                    "title": "Four-session engineering delivery",
                    "inputs_json": {
                        "goal": task,
                        "repo_context": {"repo": str(repo), "base_commit": head},
                        "observations": observations,
                    },
                    "metadata_json": {
                        "controller": "stackos.delivery.run",
                        "agent_models": models,
                        "recovery_policy": _recovery.POLICY,
                    },
                    "created_by": "stackos.delivery.run",
                },
            )
            plan_id = int(plan["id"])
            started = _call("runPlan.start", {"project_id": project_id, "run_plan_id": plan_id})
            token, run_id = str(started["run_token"]), int(started["run_id"])
        else:
            plan_id, run_id = int(existing["id"]), int(existing["run_id"])
            saved_meta = existing.get("metadata_json") or {}
            if (
                saved_meta.get("agent_models") != models
                or saved_meta.get("recovery_policy") != _recovery.POLICY
            ):
                raise ValueError(
                    "resume requires the same frozen recovery policy and effective models"
                )
            if saved_inputs.get("observations", []) != observations:
                raise ValueError("resume observation specification differs from frozen plan")
            run = _call("run.get", {"project_id": project_id, "run_id": run_id})
            if run.get("id") != run_id or run.get("project_id") != project_id:
                raise ValueError("resume run identity mismatch")
            token = str(run.get("client_session_id") or "")
            if not token:
                raise ValueError("existing run has no controller authority")
        _echo(f"StackOS plan {plan_id}, run {run_id}, base {head}")
        while True:
            state = _recovery.stages(
                sys.modules[__name__],
                project_id=project_id,
                plan_id=plan_id,
                run_id=run_id,
                token=token,
                repo=repo,
                head=head,
                task=task,
                models=models,
                role_guidance=role_guidance,
                role_seconds=role_seconds,
                max_code_bytes=max_code_bytes,
                review_state=None,
            )
            if state.get("handoff_done"):
                _echo(f"StackOS plan {plan_id}: recorded handoff is already complete")
                break
            token, seen, code_hash, after = (
                state[key] for key in ("token", "seen", "code_hash", "after")
            )
            review_state, reviewer_thread, ledger = (
                state[key] for key in ("review_state", "reviewer_thread", "ledger")
            )
            _call(
                "runPlan.claimStep",
                {
                    "project_id": project_id,
                    "run_plan_id": plan_id,
                    "step_id": "handoff",
                    "claimed_by": "stackos.delivery.run",
                    "run_token": token,
                },
            )
            if not observations:
                _record(
                    project_id,
                    plan_id,
                    token,
                    "handoff",
                    "blocked",
                    {
                        "reason": "No integrated observation specification was supplied",
                        "original_requirements": task,
                        "reviewed_code_sha256": code_hash,
                        "role_sessions": sorted(seen),
                        "outcome_unknown": False,
                    },
                    run_id=run_id,
                )
                _echo(f"StackOS plan {plan_id}: held; integrated outcome not demonstrated")
                raise typer.Exit(code=2)
            observation_receipts = []
            outcome_review = None
            final_digests = None
            try:
                final_digests = _file_digests(repo, _changed_paths(repo))
                if final_digests != after:
                    raise ValueError("candidate changed after test-writing")
                observation_attempt = uuid4().hex
                ledger.append(
                    "dispatch",
                    attempt_id=observation_attempt,
                    role="observation",
                    observations=observations,
                    file_digests=final_digests,
                )
                try:
                    observation_receipts = _observe(repo, observations, role_seconds)
                except ObservationFailed as failure:
                    ledger.append(
                        "completion",
                        attempt_id=observation_attempt,
                        role="observation",
                        receipts=failure.receipts,
                    )
                    raise
                ledger.append(
                    "completion",
                    attempt_id=observation_attempt,
                    role="observation",
                    receipts=observation_receipts,
                )
                _assert_base(repo, head)
                if _file_digests(repo, _changed_paths(repo)) != final_digests:
                    raise ValueError("candidate changed during integrated observation")
                _outcome_snapshot(repo, review_state / "outcome")
                actual_observations = [
                    {key: item[key] for key in ("argv", "stdout", "stderr", "exit_code")}
                    for item in observation_receipts
                ]
                outcome_prompt = (
                    f"Original requirements:\n{task}\n\nActual executed outcome observations:\n"
                    + json.dumps(actual_observations)
                    + "\nThe outcome snapshot now contains the final artifacts. "
                    "Independently inspect "
                    "and run relevant outcome commands using your scoped tools."
                    " Return the same review "
                    "JSON schema. Approve only if actual behavior demonstrates every original "
                    "requirement. Reject canned output, missing consumer coverage, or uncertainty. "
                    "Do not write files or tests."
                )
                outcome_review = ledger.dispatch(
                    "reviewer",
                    models["reviewer"],
                    outcome_prompt,
                    repo,
                    role_seconds,
                    review_state=review_state,
                    resume=reviewer_thread,
                )
                outcome_answer = outcome_review.get("answer") or {}
                if (
                    outcome_review.get("status") != "complete"
                    or _recovery.denied(outcome_review)
                    or outcome_review.get("thread_id") != reviewer_thread
                    or outcome_answer.get("verdict") != "approve"
                    or any(
                        outcome_answer.get(key)
                        for key in ("findings", "missing_requirements", "irrelevant_changes")
                    )
                ):
                    raise ValueError("same-session reviewer did not approve the actual outcome")
                _assert_base(repo, head)
                if _file_digests(repo, _changed_paths(repo)) != final_digests:
                    raise ValueError("candidate changed during final outcome review")
            except Exception as exc:
                if isinstance(exc, ObservationFailed):
                    observation_receipts = exc.receipts
                _record(
                    project_id,
                    plan_id,
                    token,
                    "handoff",
                    "blocked",
                    {
                        "reason": _sanitize(str(exc)),
                        "observations": observation_receipts,
                        "outcome_review": outcome_review,
                        "outcome_unknown": True,
                    },
                    run_id=run_id,
                )
                # Re-enter only through the canonical blocked handoff. The
                # recovery controller performs read-only triage and never treats
                # a completed dispatch, stdout, or a file digest as effect proof.
                continue
            # Completion uses only receipts produced by this invocation. No caller-
            # supplied JSON or resumed, unauthenticated role receipts can close it.
            _record(
                project_id,
                plan_id,
                token,
                "handoff",
                "success",
                {
                    "outcome_receipt": {
                        "original_requirements": task,
                        "observed_behavior": (
                            "Executed integrated observations; same reviewer approved "
                            "actual outcome"
                        ),
                        "outcome_review": outcome_review,
                        "evidence_refs": [
                            f"run:{run_id}:handoff:observation:{i}"
                            for i in range(len(observations))
                        ],
                        "observations": observation_receipts,
                        "gaps": [],
                        "status": "demonstrated",
                        "candidate_code_sha256": code_hash,
                        "file_digests": final_digests,
                        "role_sessions": sorted(seen),
                    }
                },
                run_id=run_id,
            )
            for episode in ledger.value["episodes"]:
                episode["resolved"] = True
            ledger.save()
            _echo(
                f"StackOS plan {plan_id}: role sessions and integrated behavior demonstrated"
            )
            break
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        _echo(_sanitize(str(exc)), err=True)
        raise typer.Exit(code=2) from None
    finally:
        controller_lock.__exit__(None, None, None)


__all__ = ["run_delivery"]
