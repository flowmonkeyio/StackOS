"""Outcome-only app-server adapter. Runs under delivery.py's existing supervisor.

No turn is admitted until the host reports the exact read-restricted policy and
an empty instruction/skill surface. There is deliberately no CLI fallback.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path

from controlled_workspace import Workspace, native_binary
from host_diagnostics import DENIAL_CODES, RPC_ERROR_CODES, RPC_METHODS, STAGES, error_metadata

from stackos.artifacts import redact_secret_text
from stackos.artifacts.checked_output import (
    CheckedStream,
    OutputRejected,
    receive_private_policy,
)

POLICY = None
EVENT_STREAM = None


def policy():
    if POLICY is None:
        raise OutputRejected("private policy was not admitted")
    return POLICY

PROFILE = "stackos_outcome_review"
COMMAND_PROFILE = "stackos_worker_command"
LIMIT = 2_000_000
# Controller-owned positive no-launch proof. Set before attempting the RPC;
# failures after that boundary cannot establish whether a turn was launched.
TURN_START_ATTEMPTED = False
# Diagnostics are finite metadata, never an error-text or configuration channel.
# The stage names the last controller activity, not the underlying root cause.
DIAGNOSTIC_STAGE = "preparation"
# Configuration contract: Codex CLI 0.164.0, its generated app-server schema,
# and https://learn.chatgpt.com/docs/config-file/config-reference . This is a
# pinned configuration admission contract, not a host-reported tool inventory.
# New host versions/feature names require renewed review before a model turn.
# shell_tool is the command-tool admission switch. unified_exec is deliberately
# not overridden: 0.164.0 ignores user opt-outs for that execution backend;
# managed requirements retain control of it (configuration reference).
HOST_VERSION = "codex-cli 0.164.0"
FEATURE_NAMES_SHA256 = "9525ece8bc739aeb1626ce8a6a0ee6c227cee3219b4dd437fb0fe462180d2f55"
# The pinned host routes dynamic tools through functions.exec. Its V8 dispatcher
# has no native filesystem/network access; only the admitted tools grant effects.
# Enable its stable host, not the separate experimental code-mode/tool-search flags.
ENABLED = ("code_mode_host",)
DISABLED = tuple(
    [
        "agent_message_board",
        "apps",
        "artifact",
        "browser_annotation_api",
        "browser_use",
        "browser_use_external",
        "browser_use_full_cdp_access",
        "chronicle",
        "code_mode",
        "code_mode_host_grpc",
        "code_mode_only",
        "code_mode_tool_search",
        "computer_use",
        "default_mode_request_user_input",
        "deferred_executor",
        "enable_mcp_apps",
        "executor_capability_discovery",
        "executor_capability_discovery_v2",
        "external_agent_memory_import",
        "goals",
        "guardian_conversation_history_tools",
        "hooks",
        "image_generation",
        "in_app_browser",
        "in_app_chat",
        "in_app_dictation",
        "in_app_local_automation",
        "in_app_voice",
        "js_repl",
        "memories",
        "mentions_v2",
        "multi_agent",
        "multi_agent_v2",
        "multi_agent_v2_dynamic_tools",
        "multi_env_copy_file",
        "plugins",
        "plugin_sharing",
        "recommended_plugins",
        "remote_control",
        "remote_plugin",
        "request_permissions_tool",
        "runtime_agent_instances",
        "search_tool",
        "send_async_message",
        "send_message_to_user_async",
        "shell_snapshot",
        "shell_snapshot_v2",
        "shell_tool",
        "skill_mcp_dependency_install",
        "skill_search",
        "sleep_tool",
        "standalone_web_search",
        "step_model_switching",
        "subagent_thread_environments",
        "tool_suggest",
        "unified_exec_tty",
        "view_image",
        "wake_on_agent_messages",
        "web_search_cached",
        "web_search_request",
        "workspace_dependencies",
    ]
)


def emit(event: dict) -> None:
    EVENT_STREAM.write(sys.stdout.buffer, policy().encoded_json(event, suffix="\n"))


class UnsupportedHost(ValueError):
    """Safe, actionable admission error with no host configuration values."""


class HostRefusal(ValueError):
    """A typed policy/auth rejection supplied by the host, never tool text."""


def nonnull(value):
    if isinstance(value, dict):
        return {k: nonnull(v) for k, v in value.items() if v is not None}
    return value


class Server:
    def __init__(self, command, cwd, env, deadline):
        policy().require(command)
        policy().require(env)
        self.input_stream = CheckedStream(policy())
        self.process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.selector.register(self.process.stderr, selectors.EVENT_READ)
        self.pending = bytearray()
        self.total = 0
        self.sequence = 0
        self.deadline = deadline
        self.notifications = []
        self.runtime_thread = None
        self.runtime_turn = None
        self.request_ids = set()
        self.allowed_tools = {"outcome_run", "outcome_image"}

    def queue_request(self, message):
        params = message.get("params", {})
        request_id = message.get("id")
        if (
            not self.runtime_thread
            or message.get("method") != "item/tool/call"
            or params.get("threadId") != self.runtime_thread
            or not isinstance(params.get("turnId"), str)
            or (self.runtime_turn and params.get("turnId") != self.runtime_turn)
            or params.get("tool") not in self.allowed_tools
            or not isinstance(params.get("arguments"), dict)
            or type(request_id) not in (int, str)
            or request_id in self.request_ids
            or len(self.notifications) >= 128
        ):
            raise ValueError("unexpected or excessive review capability request")
        self.request_ids.add(request_id)
        self.notifications.append(message)

    def send(self, value):
        data = policy().encoded_json(value, suffix="\n", limit=LIMIT)
        self.input_stream.write(self.process.stdin, data)

    def receive(self):
        while time.monotonic() < self.deadline:
            if b"\n" in self.pending:
                line, _, rest = self.pending.partition(b"\n")
                self.pending[:] = rest
                message = policy().decode_json(bytes(line), limit=LIMIT)
                params = message.get("params", {})
                if message.get("method") == "model/rerouted" and self.runtime_thread:
                    emit(
                        {
                            "type": "model.mismatch",
                            "thread_id": params.get("threadId"),
                            "turn_id": params.get("turnId"),
                            "requested_model": params.get("fromModel"),
                            "resolved_model": params.get("toModel"),
                        }
                    )
                    raise ValueError("host rerouted the configured model")
                error = (
                    params.get("error")
                    if message.get("method") == "error"
                    else params.get("turn", {}).get("error")
                    if message.get("method") == "turn/completed"
                    else None
                )
                info = error.get("codexErrorInfo") if isinstance(error, dict) else None
                if error is not None:
                    emit({
                        "type": "host.diagnostic",
                        "stage": "host_notification"
                        if message.get("method") == "error" else "turn_completion",
                        **error_metadata(
                            error,
                            will_retry=params.get("willRetry")
                            if message.get("method") == "error" else None,
                        ),
                    })
                if isinstance(info, str) and info in DENIAL_CODES:
                    emit(
                        {
                            "type": "authorization.denied",
                            "host_refusal": True,
                            "authorization_denied": True,
                            "host_code": info,
                        }
                    )
                    raise HostRefusal("Host policy or authorization refused this operation.")
                return message
            for key, _ in self.selector.select(0.1):
                data = os.read(key.fd, 65536)
                if not data:
                    self.selector.unregister(key.fileobj)
                    continue
                self.total += len(data)
                if self.total > LIMIT:
                    raise ValueError("review server exceeded bounded output")
                if key.fileobj is self.process.stdout:
                    self.pending.extend(data)
                # Host diagnostics can contain configuration/auth details. Do
                # not forward them to the model or retain raw stderr.
            if self.process.poll() is not None and not self.selector.get_map():
                raise ValueError("review server exited before completion")
        raise ValueError("review server deadline exceeded")

    def call(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = self.receive()
            if message.get("id") == request_id and "method" not in message:
                if "error" in message or "result" not in message:
                    error = message.get("error", {})
                    code = error.get("code") if isinstance(error, dict) else None
                    emit(
                        {
                            "type": "host.error",
                            "stage": "rpc_response",
                            "rpc_method": method
                            if type(method) is str and method in RPC_METHODS else "unknown",
                            "rpc_code": code
                            if type(code) is int and code in RPC_ERROR_CODES else None,
                            **error_metadata(error),
                            "refusal_state": "unclassified_host_error",
                        }
                    )
                    raise ValueError(f"review server rejected {method}")
                return message["result"]
            if "id" in message:
                # Admission rejects all requests. During a turn, validate and
                # queue server requests while waiting for this RPC's response.
                # Dispatch remains iterative: no nested command RPC/deadlock.
                self.queue_request(message)
            else:
                self.notifications.append(message)

    def close(self):
        self.process.kill()
        self.process.wait(timeout=5)
        self.selector.close()


def tools(worker=False):
    def tool(name, description, properties, required):
        return {
            "type": "function",
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        }

    return [
        tool(
            "outcome_run",
            (
                "Run argv with the filtered workspace view read-only and "
                "private scratch writable. HOME and TMPDIR point to "
                "scratch. No network or actual repository access; report "
                "unsupported runtime dependencies."
                if worker
                else "Inspect actual outcome by running argv in its read-only "
                "snapshot. No network, writes, host files or external "
                "services. Commands needing those capabilities are "
                "unsupported; report the gap."
            ),
            {"argv": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
            ["argv"],
        ),
        tool(
            "outcome_image",
            "Inspect a PNG, JPEG, GIF or WebP artifact in the outcome "
            "snapshot using its relative path.",
            {"path": {"type": "string"}},
            ["path"],
        ),
    ]


def verify_manifest(snapshot, manifest_digest):
    with (snapshot.parent / "outcome-manifest.json").open("rb") as stream:
        manifest_bytes = stream.read(10_000_001)
    if len(manifest_bytes) > 10_000_000:
        raise ValueError("outcome manifest exceeds bound")
    if hashlib.sha256(manifest_bytes).hexdigest() != manifest_digest:
        raise ValueError("controller outcome manifest changed")
    manifest = policy().decode_json(manifest_bytes)
    expected_paths = {item["path"] for item in manifest["files"]}
    actual_paths = set()
    for path in snapshot.rglob("*"):
        if path.is_symlink():
            raise ValueError("snapshot contains a symlink")
        if path.is_file():
            actual_paths.add(path.relative_to(snapshot).as_posix())
    if actual_paths != expected_paths:
        raise ValueError("snapshot differs from controller manifest")
    for item in manifest["files"]:
        path = snapshot / item["path"]
        if (
            not path.resolve().is_relative_to(snapshot)
            or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]
            or path.stat().st_mode & 0o777 != item["mode"]
        ):
            raise ValueError("snapshot artifact differs from controller manifest")
    return manifest


def command_runtime():
    """Admit only the packaged interpreter already running this helper.

    Public runtime/package code is readable; app payload, app data, host state,
    reports and credentials gain no access. Source validation can use this same
    installed interpreter without admitting a development venv or Homebrew.
    """
    system_path = "/usr/bin:/bin:/usr/sbin:/sbin"
    runtime = Path("/Applications/StackOS.app/Contents/Resources/stackos/.venv")
    if Path(sys.prefix) != runtime:
        return {}, system_path
    binary = runtime / "bin" / "python"
    roots = (runtime / "bin", runtime / "lib", runtime / "pyvenv.cfg")
    if (
        runtime.resolve(strict=True) != runtime
        or Path(sys.base_prefix) != runtime
        or Path(sys.executable).resolve(strict=True) != binary
        or (runtime / "bin" / "python3").resolve(strict=True) != binary
        or any(path.resolve(strict=True) != path for path in roots)
        or not roots[0].is_dir()
        or not roots[1].is_dir()
        or not roots[2].is_file()
    ):
        raise UnsupportedHost("Packaged command runtime provenance does not match")
    return {str(path): "read" for path in roots}, f"{runtime / 'bin'}:{system_path}"


def outcome_command(server, argv, snapshot, deadline, profile=PROFILE, scratch=None):
    policy().require(argv)
    if (
        not isinstance(argv, list)
        or not 1 <= len(argv) <= 100
        or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in argv)
        or len(json.dumps(argv)) > 16000
    ):
        raise ValueError("invalid outcome command")
    result = server.call(
        "command/exec",
        {
            "command": argv,
            "cwd": str(snapshot),
            "permissionProfile": profile,
            "timeoutMs": min(30000, max(1, int((deadline - time.monotonic()) * 1000))),
            "outputBytesCap": 100000,
            "env": {
                "CODEX_HOME": None,
                "PATH": command_runtime()[1],
                "HOME": str(scratch) if scratch else None,
                "TMPDIR": str(scratch) if scratch else None,
            },
        },
    )
    if (
        not isinstance(result, dict)
        or type(result.get("exitCode")) is not int
        or not isinstance(result.get("stdout"), str)
        or not isinstance(result.get("stderr"), str)
    ):
        raise ValueError("host command result is incomplete")
    return result


def main():
    global TURN_START_ATTEMPTED, POLICY, EVENT_STREAM, DIAGNOSTIC_STAGE
    DIAGNOSTIC_STAGE = "preparation"
    if len(sys.argv) < 3 or sys.argv[-2] != "--policy-fd":
        raise OutputRejected("private policy descriptor required")
    POLICY = receive_private_policy(int(sys.argv[-1]))
    del sys.argv[-2:]
    EVENT_STREAM = CheckedStream(POLICY, limit=LIMIT)
    policy().require(sys.argv)
    observe = sys.argv[1:2] == ["--observe"]
    worker = sys.argv[1:2] == ["--worker"]
    workspace = None
    admitted = False
    thread_id = None
    if worker:
        (
            _,
            role,
            binary,
            repo_arg,
            schema_arg,
            model,
            seconds_arg,
            host_arg,
            resume_id,
            snapshot_arg,
            manifest_digest,
        ) = sys.argv[1:]
        if role not in {"planner", "executor", "designer", "test_writer"}:
            raise ValueError("unsupported worker role")
    elif observe:
        _, binary, snapshot_arg, seconds_arg, host_arg = sys.argv[1:]
    else:
        (
            binary,
            snapshot_arg,
            schema_arg,
            model,
            seconds_arg,
            host_arg,
            resume_id,
            manifest_digest,
        ) = sys.argv[1:]
    snapshot = Path(snapshot_arg).resolve(strict=True)
    if not snapshot.is_dir():
        raise ValueError("outcome root is not a directory")
    if not observe:
        verify_manifest(snapshot, manifest_digest)
        with Path(schema_arg).open("rb") as schema_stream:
            schema = policy().decode_json(schema_stream.read(LIMIT + 1), limit=LIMIT)
    prompt = sys.stdin.buffer.read(200_001)
    if len(prompt) > 200_000:
        raise ValueError("review prompt exceeds limit")
    policy().text(prompt.decode("utf-8"), limit=200_000)
    deadline = time.monotonic() + int(seconds_arg)
    host = Path(host_arg).resolve(strict=True)
    if host.is_relative_to(snapshot) or snapshot.is_relative_to(host):
        raise ValueError("private host state must be separate from the command-readable root")
    scratch = host / "scratch" if worker else None
    if worker:
        scratch.mkdir(mode=0o700, exist_ok=True)
        if scratch.is_symlink() or scratch.resolve(strict=True) != scratch:
            raise ValueError("private scratch is not a direct host directory")
        workspace = Workspace(
            Path(repo_arg),
            snapshot,
            scratch,
            role,
            manifest_digest,
            output_policy=policy(),
            emit=emit,
        )
    review_home = host / "home"
    review_home.mkdir(mode=0o700, exist_ok=True)
    # Reference existing auth; never read, serialize or copy credentials.
    # This directory is outside the model's filesystem permission roots.
    if not observe:
        inherited_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        auth = inherited_home / "auth.json"
        if not auth.is_file():
            raise ValueError("isolated review requires existing file-backed Codex authentication")
        auth_ref = review_home / "auth.json"
        if not auth_ref.is_symlink():
            auth_ref.symlink_to(auth.resolve(strict=True))
        if auth_ref.resolve(strict=True) != auth.resolve(strict=True):
            raise ValueError("review authentication reference changed")
    env = {
        k: v
        for k, v in os.environ.items()
        if k in {"HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR"}
    }
    env["CODEX_HOME"] = str(review_home)
    runtime_reads, command_path = command_runtime()
    profile = {
        "filesystem": {":minimal": "read", **runtime_reads, str(snapshot): "read"},
        "network": {"enabled": False},
    }
    overrides = {
        "default_permissions": PROFILE,
        f"permissions.{PROFILE}.filesystem": profile["filesystem"],
        f"permissions.{PROFILE}.network.enabled": False,
        "sqlite_home": str(host / "state"),
        "log_dir": str(host / "logs"),
        "thread_unload_delay_secs": 0,
        "project_doc_max_bytes": 0,
        "memories.use_memories": False,
        "memories.generate_memories": False,
        "skills.include_instructions": False,
        "skills.bundled.enabled": False,
        "web_search": "disabled",
        "approval_policy": "never",
        "allow_login_shell": False,
        "cli_auth_credentials_store": "file",
        "browser_use.allow_history_access": False,
        "browser_use.default_origin_policy": {
            "access": "deny",
            "downloads": "deny",
            "uploads": "deny",
            "full_cdp_access": "deny",
        },
        "computer_use.default_app_access": "deny",
        "shell_environment_policy.inherit": "none",
        "shell_environment_policy.set": {
            "PATH": command_path,
            "LANG": "C.UTF-8",
        },
        f"projects.{json.dumps(str(snapshot))}.trust_level": "untrusted",
    }
    command_profile = (
        {
            "filesystem": {**profile["filesystem"], str(scratch): "write"},
            "network": {"enabled": False},
        }
        if worker
        else None
    )
    if worker:
        overrides[f"permissions.{COMMAND_PROFILE}.filesystem"] = command_profile["filesystem"]
        overrides[f"permissions.{COMMAND_PROFILE}.network.enabled"] = False

    def toml(value):
        if isinstance(value, dict):
            return "{" + ",".join(json.dumps(k) + "=" + toml(v) for k, v in value.items()) + "}"
        return json.dumps(value)

    DIAGNOSTIC_STAGE = "host_admission"
    binary = native_binary(binary, output_policy=policy())
    policy().require([binary, "--version", env])
    version = subprocess.run(
        [binary, "--version"],
        cwd=host,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    if version.stdout.strip() != HOST_VERSION:
        raise UnsupportedHost(
            "Review requires the pinned Codex CLI 0.164.0 capability "
            "contract; review the new host before admission."
        )
    command = [binary, "app-server", "--strict-config"]
    for key, value in overrides.items():
        command += ["-c", key + "=" + toml(value)]
    for flag in DISABLED:
        command += ["--disable", flag]
    for flag in ENABLED:
        command += ["--enable", flag]
    policy().require([binary, "features", "list", *command[3:], env])
    feature_result = subprocess.run(
        [binary, "features", "list", *command[3:]],
        cwd=host,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    features = {}
    for line in feature_result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[-1] not in {"true", "false"} or fields[0] in features:
            raise UnsupportedHost(
                "Review host feature discovery differs from the pinned "
                "contract; review is held before any model turn."
            )
        features[fields[0]] = fields[-1] == "true"
    if (
        hashlib.sha256("\n".join(sorted(features)).encode()).hexdigest() != FEATURE_NAMES_SHA256
        or any(features.get(flag) is not False for flag in DISABLED)
        or any(features.get(flag) is not True for flag in ENABLED)
    ):
        raise UnsupportedHost(
            "Review host capabilities differ from the pinned contract; "
            "renew the contract before any model turn."
        )
    server = Server(command, host, env, deadline)
    try:
        if worker:
            server.allowed_tools.update(spec["name"] for spec in workspace.specs())
        server.call(
            "initialize",
            {
                "clientInfo": {"name": "stackos-outcome-review", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        server.send({"method": "initialized"})
        requirements_response = server.call("configRequirements/read", None)
        if "requirements" not in requirements_response:
            raise ValueError("review host did not disclose managed requirements")
        requirements = requirements_response["requirements"] or {}
        if requirements.get("additionalDeveloperInstructions") or requirements.get("hooks"):
            raise ValueError("managed context cannot be excluded from outcome-only review")
        profiles = server.call("permissionProfile/list", {"cwd": str(host)})
        selected = [item for item in profiles.get("data", []) if item.get("id") == PROFILE]
        if len(selected) != 1 or selected[0].get("allowed") is not True:
            raise ValueError("managed requirements do not allow isolated review profile")
        config = nonnull(
            server.call("config/read", {"cwd": str(host), "includeLayers": False})["config"]
        )
        if config.get("permissions", {}).get(PROFILE) != profile:
            raise ValueError("effective review permission policy does not match")
        if worker:
            command_selected = [
                item for item in profiles.get("data", []) if item.get("id") == COMMAND_PROFILE
            ]
            if (
                len(command_selected) != 1
                or command_selected[0].get("allowed") is not True
                or config.get("permissions", {}).get(COMMAND_PROFILE) != command_profile
            ):
                raise ValueError("effective worker command policy does not match")
        if config.get("default_permissions") != PROFILE:
            raise ValueError("effective default review profile does not match")
        if any(config.get("features", {}).get(key) is not False for key in DISABLED):
            raise ValueError("review host tools were not disabled")
        if any(config.get("features", {}).get(key) is not True for key in ENABLED):
            raise ValueError("review host dynamic-tool dispatcher was not enabled")
        for key in (
            "developer_instructions",
            "instructions",
            "mcp_servers",
            "hooks",
            "model_instructions_file",
        ):
            if config.get(key):
                raise ValueError("review host contains inherited instructions or capabilities")
        for key in (
            "skills",
            "memories",
            "shell_environment_policy",
            "browser_use",
            "computer_use",
        ):
            expected = {
                name.split(".", 1)[1]: value
                for name, value in overrides.items()
                if name.startswith(key + ".")
            }
            actual = config.get(key, {})
            for field, value in expected.items():
                parts = field.split(".")
                found = actual
                for part in parts:
                    found = found.get(part) if isinstance(found, dict) else None
                if found != value:
                    raise ValueError("review context/environment configuration does not match")
        if config.get("web_search") != "disabled" or config.get("project_doc_max_bytes") != 0:
            raise ValueError("review context sources remain enabled")
        skills = server.call("skills/list", {"cwds": [str(host)], "forceReload": True})
        rows = skills.get("data")
        if (
            not isinstance(rows, list)
            or len(rows) != 1
            or rows[0].get("cwd") != str(host)
            or rows[0].get("skills") != []
            or rows[0].get("errors") != []
        ):
            raise ValueError("review host contains skills or uncertain skill discovery")
        if observe:
            DIAGNOSTIC_STAGE = "observation_admission"
            spec = policy().decode_json(prompt)
            if not isinstance(spec, dict) or set(spec) != {"argv"}:
                raise ValueError("invalid confined observation request")
            DIAGNOSTIC_STAGE = "observation_dispatch"
            result = outcome_command(server, spec["argv"], snapshot, deadline)
            # A successful command RPC is distinct from a host/RPC refusal.
            # Nonzero is a program failure for recovery triage. This field
            # makes no claim about permissions failures handled inside it.
            DIAGNOSTIC_STAGE = "observation_result"
            emit(
                {
                    "type": "observation.completed",
                    "exit_code": result["exitCode"],
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                    "refusal_state": "no_refusal_reported",
                    "confinement": {
                        "mode": "read_only",
                        "verified": True,
                        "network": False,
                        "roots": [str(snapshot)],
                        "profile": PROFILE,
                        "host_contract": HOST_VERSION,
                        "feature_names_sha256": FEATURE_NAMES_SHA256,
                    },
                }
            )
            return
        params = {
            "cwd": str(host),
            "runtimeWorkspaceRoots": [str(snapshot)],
            "permissions": PROFILE,
            "approvalPolicy": "never",
            "model": model,
            "developerInstructions": "",
        }
        if resume_id:
            params.update({"threadId": resume_id, "excludeTurns": True})
            start = server.call("thread/resume", params)
        else:
            params.update(
                {
                    "ephemeral": False,
                    "dynamicTools": tools(worker) + (workspace.specs() if worker else []),
                    "allowProviderModelFallback": False,
                }
            )
            start = server.call("thread/start", params)
        if (
            start.get("activePermissionProfile") != {"id": PROFILE, "extends": None}
            or start.get("instructionSources") != []
            or start.get("runtimeWorkspaceRoots") != [str(snapshot)]
            or start.get("cwd") != str(host)
            or start.get("approvalPolicy") != "never"
            or start.get("model") != model
            or start.get("sandbox") != {"type": "readOnly", "networkAccess": False}
        ):
            raise ValueError("review session isolation handshake failed")
        thread_id = start["thread"]["id"]
        if resume_id and thread_id != resume_id:
            raise ValueError("review resumed a different session")
        emit({"type": "thread.started", "thread_id": thread_id})
        if worker:
            admitted = True
            emit(
                {
                    "type": "scope.admitted",
                    "role": role,
                    "scope": "controlled_workspace",
                    "profile": PROFILE,
                    "command_profile": COMMAND_PROFILE,
                    "network": False,
                    "workspace_roots": [str(snapshot)],
                    "command_write_roots": [str(scratch)],
                    "actual_repository_command_access": False,
                    "mutation_owner": "synchronous_main_process_broker",
                    "custody": "outer_existing_execute",
                    "manifest_sha256": manifest_digest,
                    "host_contract": HOST_VERSION,
                    "feature_names_sha256": FEATURE_NAMES_SHA256,
                    "requested_model": model,
                    "resolved_model": start["model"],
                    "thread_id": thread_id,
                }
            )
            emit(
                {
                    "type": "model.resolved",
                    "requested_model": model,
                    "resolved_model": start["model"],
                    "thread_id": thread_id,
                }
            )
        emit(
            {
                "type": "isolation.verified",
                "profile": PROFILE,
                "instruction_sources": [],
                "admitted_dynamic_tools": sorted(server.allowed_tools),
                "host_contract": HOST_VERSION,
                "feature_names_sha256": FEATURE_NAMES_SHA256,
                "disabled_features": list(DISABLED),
                "enabled_features": list(ENABLED),
                "outcome_manifest_sha256": manifest_digest,
                "requested_model": model,
                "resolved_model": start["model"],
            }
        )
        server.runtime_thread = thread_id
        TURN_START_ATTEMPTED = True
        DIAGNOSTIC_STAGE = "turn_start"
        turn = server.call(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt.decode()}],
                "outputSchema": schema,
            },
        )
        turn_id = turn["turn"]["id"]
        server.runtime_turn = turn_id
        if worker:
            emit(
                {
                    "type": "worker.started",
                    "role": role,
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "requested_model": model,
                    "resolved_model": start["model"],
                }
            )
        while True:
            DIAGNOSTIC_STAGE = "turn_receive"
            if server.notifications:
                message = server.notifications.pop(0)
            else:
                message = server.receive()
                if "id" in message:
                    server.queue_request(message)
                    continue
            method, params = message.get("method"), message.get("params", {})
            if "id" in message:
                DIAGNOSTIC_STAGE = "tool_admission"
                if (
                    method != "item/tool/call"
                    or params.get("threadId") != thread_id
                    or params.get("turnId") != turn_id
                ):
                    raise ValueError("unexpected review capability request")
                args = params.get("arguments")
                policy().require(args)
                if not isinstance(args, dict):
                    raise ValueError("invalid outcome tool arguments")
                DIAGNOSTIC_STAGE = "tool_dispatch"
                if params.get("tool") == "outcome_run" and set(args) == {"argv"}:
                    argv = args["argv"]
                    result = outcome_command(
                        server,
                        argv,
                        snapshot,
                        deadline,
                        COMMAND_PROFILE if worker else PROFILE,
                        scratch,
                    )
                    content = [
                        {"type": "inputText", "text": policy().encoded_json(result).decode()}
                    ]
                elif params.get("tool") == "outcome_image" and set(args) == {"path"}:
                    relative = Path(args["path"])
                    path = (snapshot / relative).resolve(strict=True)
                    if (
                        relative.is_absolute()
                        or not path.is_relative_to(snapshot)
                        or not path.is_file()
                        or path.stat().st_size > 500000
                    ):
                        raise ValueError("image is outside the bounded outcome")
                    data = path.read_bytes()
                    # Scan embedded plaintext exactly as controller admission does;
                    # this does not inspect pixels or arbitrary compressed encodings.
                    text = data.decode("utf-8", errors="ignore")
                    policy().text(text)
                    if redact_secret_text(text) != text:
                        raise ValueError("image contains secret-like text")
                    mime = (
                        "image/png"
                        if data.startswith(b"\x89PNG\r\n\x1a\n")
                        else "image/jpeg"
                        if data.startswith(b"\xff\xd8\xff")
                        else "image/gif"
                        if data.startswith((b"GIF87a", b"GIF89a"))
                        else "image/webp"
                        if data.startswith(b"RIFF") and data[8:12] == b"WEBP"
                        else None
                    )
                    if not mime:
                        raise ValueError("unsupported outcome image format")
                    content = [
                        {
                            "type": "inputImage",
                            "imageUrl": "data:"
                            + mime
                            + ";base64,"
                            + base64.b64encode(data).decode(),
                        }
                    ]
                elif worker and params.get("tool") in {spec["name"] for spec in workspace.specs()}:
                    try:
                        result = workspace.dispatch(params["tool"], args)
                    except ValueError:
                        # A synchronous read/list validation failure is local. This
                        # marker never classifies RPC errors or failed mutations.
                        if (
                            params["tool"] in {"workspace_read", "workspace_list"}
                            and not workspace.broken
                        ):
                            emit(
                                {
                                    "type": "worker.local_failure",
                                    "failure_kind": "workspace_read_validation",
                                    "refusal_state": "no_refusal_reported",
                                }
                            )
                        raise
                    content = [
                        {"type": "inputText", "text": policy().encoded_json(result).decode()}
                    ]
                else:
                    raise ValueError("unsupported outcome tool")
                DIAGNOSTIC_STAGE = "tool_response"
                server.send(
                    {"id": message["id"], "result": {"success": True, "contentItems": content}}
                )
                # Workspace edit contents and repository paths are not copied into host audit.
                if params["tool"] in {"outcome_run", "outcome_image"}:
                    emit(
                        {
                            "type": "outcome.inspected",
                            "tool": params["tool"],
                            "arguments": args,
                            "result": result
                            if params["tool"] == "outcome_run"
                            else {"image_inspected": True},
                        }
                    )
            elif (
                method == "item/completed"
                and params.get("threadId") == thread_id
                and params.get("turnId") == turn_id
            ):
                item = params.get("item", {})
                DIAGNOSTIC_STAGE = "agent_output"
                if item.get("type") == "agentMessage":
                    emit(
                        {
                            "type": "item.completed",
                            "item": {"type": "agent_message", "text": item.get("text", "")},
                        }
                    )
            elif method == "turn/completed" and params.get("threadId") == thread_id:
                DIAGNOSTIC_STAGE = "turn_completion"
                completed = params.get("turn", {})
                if (
                    completed.get("id") != turn_id
                    or completed.get("status") != "completed"
                    or completed.get("error")
                ):
                    raise ValueError("review turn did not complete")
                if any("id" in item for item in server.notifications):
                    raise ValueError("review completed with unanswered tool requests")
                verify_manifest(snapshot, workspace.manifest_digest if worker else manifest_digest)
                server.runtime_thread = None
                DIAGNOSTIC_STAGE = "thread_close"
                server.call("thread/unsubscribe", {"threadId": thread_id})
                while True:
                    closed = (
                        server.notifications.pop(0) if server.notifications else server.receive()
                    )
                    if (
                        closed.get("method") == "thread/closed"
                        and closed.get("params", {}).get("threadId") == thread_id
                    ):
                        break
                    if "id" in closed:
                        raise ValueError("unexpected request while persisting review session")
                # Controller evidence, independent of the model's verdict/JSON.
                # This certifies only the admitted read-only outcome scope.
                if not worker:
                    emit(
                        {
                            "type": "effects.reconciled",
                            "scope": "read_only_outcome",
                            "outcome_manifest_sha256": manifest_digest,
                            "thread_id": thread_id,
                            "thread_closed": True,
                        }
                    )
                emit({"type": "turn.completed"})
                return
    finally:
        # No asynchronous broker dispatch exists. Closing admission here means
        # even failed turns cannot issue another repository mutation. The outer
        # supervisor owns process custody on abrupt exit/timeout; this receipt
        # makes no claim about arbitrary external host effects.
        if workspace is not None:
            workspace.close()
        try:
            server.close()
        finally:
            if worker and admitted:
                emit(
                    {
                        "type": "effects.unreconciled"
                        if workspace.broken
                        else "effects.reconciled",
                        "scope": "controlled_workspace",
                        "requires_reconciliation": workspace.broken,
                        "thread_id": thread_id,
                        "broker_closed": True,
                        "mutation_owner": "synchronous_main_process_broker",
                        "custody": "outer_existing_execute",
                        "manifest_sha256": workspace.manifest_digest,
                    }
                )


def emit_failure(message):
    worker = sys.argv[1:2] == ["--worker"]
    event = {
        "type": "worker.failed" if worker else "review.failed", "message": message,
        "stage": DIAGNOSTIC_STAGE
        if type(DIAGNOSTIC_STAGE) is str and DIAGNOSTIC_STAGE in STAGES
        else "unknown",
    }
    if worker and not TURN_START_ATTEMPTED:
        event.update({"dispatch_state": "not_started", "worker_launched": False})
    try:
        emit(event)
    except Exception:
        # No unchecked fallback or traceback on a rejected/poisoned output.
        os._exit(2)


if __name__ == "__main__":
    try:
        main()
    except UnsupportedHost as exc:
        emit_failure(str(exc))
        sys.exit(2)
    except HostRefusal:
        # Preserve the separate typed refusal, and add positive no-launch
        # evidence only when the turn/start RPC was never attempted.
        if sys.argv[1:2] == ["--worker"]:
            emit_failure("Host policy or authorization refused this operation.")
        sys.exit(2)
    except Exception:
        # Do not disclose app-server errors or credential/configuration values.
        emit_failure("Host isolation or execution failed; no fallback was used.")
        sys.exit(2)
