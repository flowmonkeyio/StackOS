"""Claude Code native sign-in, identity and persistent subscription selection."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import subprocess
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
    _private_directory,
    _private_fd,
)
from stackos.host_mcp.adapters import claude_credentials as credentials
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
        env["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = str(profile.directory)
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
            and any(
                overrides.get(key)
                for key in _COMPETING_ENV | {"CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR"}
            )
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


class ClaudeAccounts:
    """Native first-party sign-in in a separate, owner-private config directory."""

    def __init__(self) -> None:
        self._logins: dict[Path, ManagedProcessControl] = {}

    @staticmethod
    def _native() -> tuple[str, dict[str, str], ClaudeProfile]:
        binary = claude_mcp.resolve_claude_bin()
        if binary is None:
            raise ClaudeAccountError("Claude Code CLI is unavailable.")
        env = subprocess_env_for_cli(binary)
        return binary, env, _paths(_status(binary, env), env)

    def capture(self, profile: NativeAccountProfile) -> None:
        binary, env, paths = self._native()
        if profile.directory is None:
            raise ClaudeAccountError("Claude adoption requires a private saved profile.")
        saved_env = _identity_env(binary, profile)
        self._save_login(env, paths.metadata, saved_env, profile.directory / ".claude.json")

    @staticmethod
    def _save_login(source_env, source_metadata, target_env, target_metadata) -> None:
        source, _ = credentials.read_store(source_env)
        oauth = source.get("claudeAiOauth")
        account = read_native_object(source_metadata, _MAX_CONFIG_BYTES).get("oauthAccount")
        if not isinstance(oauth, dict) or not oauth or not isinstance(account, dict):
            raise ClaudeAccountError("Claude subscription credentials are unavailable.")
        ClaudeAccounts._write_login(target_env, target_metadata, oauth, account)

    @staticmethod
    def _write_login(target_env, target_metadata, oauth, account) -> None:
        target, backend = credentials.read_store(target_env)
        try:
            metadata = read_native_object(target_metadata, _MAX_CONFIG_BYTES)
        except FileNotFoundError:
            metadata = {}
        updated_store = {key: value for key, value in target.items() if key != "claudeAiOauth"}
        updated_metadata = {key: value for key, value in metadata.items() if key != "oauthAccount"}
        if oauth is not None:
            updated_store["claudeAiOauth"] = oauth
            updated_metadata["oauthAccount"] = account
        credentials.write_store(target_env, updated_store, backend)
        try:
            credentials.write_object(target_metadata, updated_metadata)
        except Exception:
            # Restore the prior secure-store object if the separate metadata write fails.
            try:
                credentials.write_store(target_env, target, backend)
            except Exception:
                raise HostAccountError("selection_restore_failed") from None
            raise

    def activate(
        self, profile: NativeAccountProfile | None, outgoing: NativeAccountProfile | None
    ) -> None:
        binary, env, paths = self._native()
        active = _identity(_status(binary, env), paths)
        if outgoing is None:
            if active != "needs_sign_in" or profile is None:
                raise ClaudeAccountError(
                    "Adopt the current Claude login before switching accounts."
                )
        else:
            previous = self.read_identity(outgoing)
            if (
                outgoing.directory is None
                or not isinstance(active, NativeIdentity)
                or not isinstance(previous, NativeIdentity)
                or active.principal_key != previous.principal_key
            ):
                raise ClaudeAccountError(
                    "The native Claude login changed outside account selection."
                )
            self._save_login(
                env,
                paths.metadata,
                _identity_env(binary, outgoing),
                outgoing.directory / ".claude.json",
            )
        if profile is None:
            self._write_login(env, paths.metadata, None, None)
            return
        selected = self.read_identity(profile)
        if profile.directory is None or not isinstance(selected, NativeIdentity):
            raise ClaudeAccountError("Claude could not verify the selected native account.")
        if not isinstance(active, NativeIdentity) or selected.principal_key != active.principal_key:
            self._save_login(
                _identity_env(binary, profile),
                profile.directory / ".claude.json",
                env,
                paths.metadata,
            )

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
