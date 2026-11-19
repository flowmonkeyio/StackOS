"""Claude Code profile selection, native identity, and local transcript ownership."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from uuid import UUID

from stackos import claude_mcp
from stackos.host_mcp.accounts import (
    HostAccountError,
    NativeAccountProfile,
    NativeIdentity,
    NativeIdentityIssue,
    ResolvedAccountSelection,
    _private_directory,
    _private_fd,
)
from stackos.host_mcp.agent_execution import ManagedProcessControl
from stackos.host_mcp.discovery import subprocess_env_for_cli
from stackos.host_mcp.subscription_native import run_capture

_COMPETING_ENV = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_FEDERATION_RULE_ID",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
    "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_GATEWAY",
    "CLAUDE_CODE_PROXY_RESOLVES_AUTH",
    "CLAUDE_CODE_REMOTE",
    "CLAUDE_CODE_SKIP_AUTH",
}
_USER_FILES = (
    "settings.json",
    "CLAUDE.md",
    "keybindings.json",
    "agents",
    "commands",
    "skills",
    "rules",
    "plugins",
    "output-styles",
)
_LOGIN_LEASE = ".stackos-claude-login"
_MAX_CONFIG_BYTES = 8 * 1024 * 1024


class ClaudeAccountError(Exception):
    """Safe diagnostic for a selected Claude subscription or conversation."""


@dataclass(frozen=True)
class ClaudeProfile:
    config: Path
    projects: Path
    metadata: Path


def _uuid(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected a UUID")
    parsed = str(UUID(value))
    if parsed != value:
        raise ValueError("Expected a canonical UUID")
    return parsed


def _identity_env(binary: str, profile: NativeAccountProfile) -> dict[str, str]:
    env = subprocess_env_for_cli(binary)
    if profile.directory is not None:
        _private_directory(profile.directory, "unsafe_profile")
        env["CLAUDE_CONFIG_DIR"] = str(profile.directory)
    return env


def _status(binary: str, env: dict[str, str], cwd: Path | None = None) -> dict:
    check_auth_settings(env, cwd)
    result = run_capture(binary, ["auth", "status", "--json"], env=env, cwd=cwd)
    if result is None:
        raise ClaudeAccountError("Claude Code could not report its native authentication status.")
    try:
        value = json.loads(result.stdout)
        expected = value["loggedIn"]
        if type(expected) is not bool or result.returncode != (0 if expected else 1):
            raise ValueError
        return value
    except (KeyError, TypeError, ValueError, RecursionError):
        raise ClaudeAccountError(
            "Claude Code returned an unreadable authentication status."
        ) from None


def check_auth_settings(env: dict[str, str], cwd: Path | None) -> None:
    if any(env.get(key) for key in _COMPETING_ENV):
        raise ClaudeAccountError("Competing Claude Code authentication is configured.")
    home = Path(env.get("HOME") or Path.home())
    config = Path(env.get("CLAUDE_CONFIG_DIR") or home / ".claude").expanduser()
    files = [config / "settings.json"]
    if cwd is not None:
        files.extend(cwd / ".claude" / name for name in ("settings.json", "settings.local.json"))
    for file in files:
        try:
            settings = read_native_object(file.resolve(), _MAX_CONFIG_BYTES)
        except FileNotFoundError:
            continue
        except (OSError, ValueError, RecursionError, RuntimeError):
            raise ClaudeAccountError(
                "Claude Code authentication settings could not be verified."
            ) from None
        overrides = settings.get("env")
        if settings.get("apiKeyHelper") or (
            isinstance(overrides, dict)
            and any(overrides.get(key) for key in _COMPETING_ENV | {"CLAUDE_CONFIG_DIR"})
        ):
            raise ClaudeAccountError("Competing Claude Code authentication is configured.")


def _paths(status: dict, env: dict[str, str]) -> ClaudeProfile:
    try:
        config = Path(status["configDirectory"])
        projects = Path(status["projectsDirectory"])
        if not config.is_absolute() or not projects.is_absolute():
            raise ValueError
        if projects != config / "projects":
            raise ValueError
        home = Path(env.get("HOME") or Path.home())
        explicit = env.get("CLAUDE_CONFIG_DIR")
        expected = Path(explicit).expanduser() if explicit else home / ".claude"
        if config.resolve() != expected.resolve():
            raise ValueError
        return ClaudeProfile(config, projects, (config if explicit else home) / ".claude.json")
    except (KeyError, TypeError, ValueError, OSError, RuntimeError):
        raise ClaudeAccountError(
            "Claude Code did not confirm its local profile directories."
        ) from None


def _read_oauth_account_metadata(path: Path) -> tuple[str, str, str]:
    # Native auth status verifies the login and reports the email and organization.
    # It omits the person UUID, which Claude stores in this separate metadata object.
    # Do not open .credentials.json, query Keychain, or retain the config payload.
    row = read_native_object(path, _MAX_CONFIG_BYTES)["oauthAccount"]
    return _uuid(row["accountUuid"]), _uuid(row["organizationUuid"]), row["emailAddress"]


def read_native_object(path: Path, limit: int = 4096) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or info.st_size > limit
        ):
            raise ValueError("Unreadable native metadata")
        value = json.loads(file.read(limit + 1))
    if not isinstance(value, dict):
        raise ValueError("Expected native metadata object")
    return value


def _seed_tool_config(default: ClaudeProfile, directory: Path) -> None:
    # Claude keeps user/local MCP definitions and workspace trust in the same
    # native file as its account metadata. Carry over only tool configuration
    # and existing local choices. OAuth and other native state stay behind.
    try:
        original = read_native_object(default.metadata, _MAX_CONFIG_BYTES)
    except FileNotFoundError:
        return
    result = {key: original[key] for key in ("mcpServers",) if key in original}
    if original.get("hasCompletedOnboarding") is True:
        result["hasCompletedOnboarding"] = True
    if isinstance(projects := original.get("projects"), dict):
        keys = {
            "mcpServers",
            "hasTrustDialogAccepted",
            "enabledMcpjsonServers",
            "disabledMcpjsonServers",
        }
        result["projects"] = {
            path: {key: row[key] for key in keys & row.keys()}
            for path, row in projects.items()
            if isinstance(row, dict) and keys & row.keys()
        }
    if result:
        fd = _private_fd(directory / ".claude.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "w") as stream:
            json.dump(result, stream)


def _identity(status: dict, paths: ClaudeProfile) -> NativeIdentity | NativeIdentityIssue:
    method = status.get("authMethod")
    expires = status.get("loginExpiresAt")
    if expires is not None:
        try:
            if datetime.fromisoformat(expires) <= datetime.now(UTC):
                return "expired"
        except (TypeError, ValueError):
            return "unavailable"
    if method == "none" and not status["loggedIn"]:
        return "needs_sign_in"
    if method != "claude.ai" or status.get("apiProvider") != "firstParty" or not status["loggedIn"]:
        return "conflicting_credentials"
    email = status.get("email")
    if not isinstance(email, str) or not email:
        return "missing_email"
    try:
        principal, org, native_email = _read_oauth_account_metadata(paths.metadata)
        if status.get("orgId") != org or native_email != email:
            return "unavailable"
        return NativeIdentity(principal=principal, workspace=org, email=email)
    except (KeyError, TypeError, ValueError, OSError, RecursionError):
        return "unavailable"


def selected_profile(
    binary: str,
    selection: ResolvedAccountSelection,
    cwd: Path | None = None,
) -> tuple[dict[str, str], ClaudeProfile]:
    selected = selection.desired.account_ref is not None
    profile = selection.profile
    if selected and (profile is None or selection.principal_key is None):
        raise ClaudeAccountError("Claude Code has no verified profile for this account.")
    env = _identity_env(binary, profile or NativeAccountProfile(None, None))
    status = _status(binary, env, cwd)
    paths = _paths(status, env)
    identity = _identity(status, paths)
    if not isinstance(identity, NativeIdentity) or (
        selected and identity.principal_key != selection.principal_key
    ):
        raise ClaudeAccountError("Claude Code did not confirm the selected subscription account.")
    # An implicit default and an explicit override of the same directory use
    # different native global metadata. Preserve the original selection mode.
    return env, paths


def _transcript_matches(path: Path, session_id: str, cwd: Path) -> bool:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o022
            or info.st_nlink != 1
        ):
            return False
        remaining = _MAX_CONFIG_BYTES
        while remaining > 0 and (line := file.readline(remaining + 1)):
            remaining -= len(line)
            if remaining < 0:
                return False
            try:
                value = json.loads(line)
                if value.get("sessionId") == session_id and value.get("type") in {
                    "user",
                    "assistant",
                }:
                    origin = Path(value["cwd"])
                    return origin.is_absolute() and origin.resolve() == cwd
            except (AttributeError, KeyError, TypeError, ValueError, RecursionError):
                continue
        return False


def resume_transcript(paths: ClaudeProfile, session_id: str, cwd: Path) -> Path:
    try:
        _uuid(session_id)
        root = paths.projects.resolve(strict=True)
        matches = [
            path for path in root.glob(f"*/{session_id}.jsonl") if not path.parent.is_symlink()
        ]
        if len(matches) == 1 and _transcript_matches(matches[0], session_id, cwd.resolve()):
            return matches[0]
    except (OSError, ValueError, RuntimeError):
        pass
    raise ClaudeAccountError(
        "The exact local Claude Code transcript is missing, ambiguous, or outside this workspace. "
        "Resume a saved local conversation from its original workspace."
    )


@contextmanager
def transcript_writer(session_id: str) -> Iterator[int]:
    """Keep one shared writer lease across StackOS Claude profiles and processes."""
    try:
        _uuid(session_id)
        directory = Path(f"/tmp/stackos-claude-leases-{os.getuid()}")
        directory.mkdir(mode=0o700, exist_ok=True)
        _private_directory(directory, "unsafe_profile")
        fd = _private_fd(directory / session_id, os.O_RDWR | os.O_CREAT)
    except (HostAccountError, OSError, ValueError):
        raise ClaudeAccountError("The Claude Code conversation lease is unavailable.") from None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ClaudeAccountError(
                "This Claude Code conversation already has a writer."
            ) from None
        yield fd
    finally:
        os.close(fd)


class ClaudeAccounts:
    """Native first-party sign-in in a separate, owner-private config directory."""

    def __init__(self) -> None:
        self._logins: dict[Path, ManagedProcessControl] = {}

    def read_identity(self, profile: NativeAccountProfile) -> NativeIdentity | NativeIdentityIssue:
        binary = claude_mcp.resolve_claude_bin()
        if binary is None:
            return "unavailable"
        env = _identity_env(binary, profile)
        if any(env.get(key) for key in _COMPETING_ENV):
            return "conflicting_credentials"
        try:
            status = _status(binary, env)
            return _identity(status, _paths(status, env))
        except ClaudeAccountError:
            return "unavailable"

    def start_login(self, profile: NativeAccountProfile) -> None:
        binary = claude_mcp.resolve_claude_bin()
        directory = profile.directory
        if binary is None or directory is None:
            raise ClaudeAccountError("Claude Code requires an isolated profile for sign-in.")
        env = _identity_env(binary, profile)
        default_env = subprocess_env_for_cli(binary)
        default = _paths(_status(binary, default_env), default_env)
        if default.config.resolve() == directory.resolve():
            raise ClaudeAccountError("Claude Code enrollment requires a distinct profile.")
        # Share the local transcript tree and user configuration files. The native
        # global metadata, authentication/keychain entry, and caches stay profile-local.
        default.projects.mkdir(mode=0o700, parents=True, exist_ok=True)
        (directory / "projects").symlink_to(
            default.projects.resolve(strict=True), target_is_directory=True
        )
        for name in _USER_FILES:
            source = default.config / name
            if source.exists():
                (directory / name).symlink_to(source, target_is_directory=source.is_dir())
        _seed_tool_config(default, directory)
        _paths(_status(binary, env), env)
        fd = _private_fd(directory / _LOGIN_LEASE, os.O_RDWR | os.O_CREAT | os.O_EXCL)
        control = ManagedProcessControl()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            process = control.spawn(
                [binary, "auth", "login", "--claudeai"],
                cwd=directory,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=(fd,),
            )
            self._logins[directory] = control

            def retire() -> None:
                try:
                    process.wait(timeout=14 * 60)
                except subprocess.TimeoutExpired:
                    pass
                finally:
                    if control.stop() == "stopped":
                        self._logins.pop(directory, None)
                    os.close(fd)

            Thread(target=retire, daemon=True, name="stackos-claude-login").start()
        except BaseException:
            control.stop()
            os.close(fd)
            raise

    def discard_login(self, profile: NativeAccountProfile) -> bool:
        directory = profile.directory
        if directory is None:
            return False
        if (control := self._logins.get(directory)) is not None and control.stop() != "stopped":
            return False
        if not directory.exists():
            return True
        binary = claude_mcp.resolve_claude_bin()
        if binary is None:
            return False
        env = _identity_env(binary, profile)
        fd = _private_fd(directory / _LOGIN_LEASE, os.O_RDWR | os.O_CREAT)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            status = _status(binary, env)
            _paths(status, env)
            if status["loggedIn"]:
                result = run_capture(binary, ["auth", "logout"], env=env)
                if result is None or result.returncode or _status(binary, env)["loggedIn"]:
                    return False
            shutil.rmtree(directory)
            return True
        finally:
            os.close(fd)


accounts = ClaudeAccounts()
