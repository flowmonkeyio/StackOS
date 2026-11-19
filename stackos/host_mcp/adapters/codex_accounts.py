"""Codex-owned native account profiles and persistent credential selection."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import webbrowser
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event, Thread
from urllib.parse import urlsplit

from pydantic import ValidationError

from stackos.host_mcp import subscription_native as native
from stackos.host_mcp.accounts import (
    HostAccountError,
    NativeAccountProfile,
    NativeIdentity,
    NativeIdentityIssue,
    _private_directory,
    _private_fd,
)
from stackos.host_mcp.adapters.codex_app_server import open_app_server
from stackos.host_mcp.agent_execution import ManagedProcessControl
from stackos.host_mcp.discovery import subprocess_env_for_cli

Principal = tuple[str, str | None]
FileIdentity = tuple[int, int, int, int]


class CodexAccountError(Exception):
    """An operator-safe Codex account diagnostic."""


_INIT = (
    "initialize",
    {
        "clientInfo": {"name": "stackos_codex_accounts", "version": "1"},
        "capabilities": {"experimentalApi": True},
    },
)
_ACCOUNT = ("account/read", {"refreshToken": False})
_SESSIONS = ("accountSession/list", {"refreshWorkspaceMetadata": False})


def _rpc(binary, env, *calls, overrides=()):
    return native.read_rpc(
        binary, ["app-server", "--listen", "stdio://", *overrides], [_INIT, *calls], env=env
    )


def _identity(account_result: dict, sessions: dict) -> NativeIdentity | NativeIdentityIssue:
    account = account_result.get("account")
    if account is None:
        return (
            "needs_sign_in" if account_result.get("requiresOpenaiAuth") is True else "unavailable"
        )
    if not isinstance(account, dict) or account.get("type") != "chatgpt":
        return "conflicting_credentials"
    if not account.get("email"):
        return "missing_email"
    principal = account_result.get("userId")
    if not isinstance(principal, str) or not principal:
        return "unavailable"
    routing = account_result.get("workspaceRouting") or {}
    workspace = routing.get("chatgptAccountId") if isinstance(routing, dict) else None
    if not workspace:
        active = sessions.get("activeSessionId")
        rows = sessions.get("sessions")
        if active and isinstance(rows, list):
            matches = [
                row.get("selectedWorkspaceAccountId")
                for row in rows
                if isinstance(row, dict)
                and row.get("sessionId") == active
                and row.get("userId") == principal
            ]
            if len(matches) == 1:
                workspace = matches[0]
    if not workspace:
        return "unavailable"
    try:
        return NativeIdentity(principal=principal, workspace=workspace, email=account["email"])
    except ValidationError:
        return "unavailable"


def native_identity(
    binary: str, env: dict[str, str], *, overrides=()
) -> NativeIdentity | NativeIdentityIssue:
    responses = _rpc(binary, env, _ACCOUNT, _SESSIONS, overrides=overrides)
    if len(responses) > 1:
        error = responses[1].get("error")
        detail = error.get("message") if isinstance(error, dict) else None
        if isinstance(detail, str) and "expired" in detail.lower():
            return "expired"
    return _identity(native.rpc_result(responses, 1), native.rpc_result(responses, 2))


def _check_environment(env: dict[str, str]) -> None:
    if any(
        env.get(key, "").strip()
        for key in (
            "CODEX_API_KEY",
            "OPENAI_API_KEY",
            "CODEX_ACCESS_TOKEN",
            "CODEX_FORCE_AUTH_MODE",
        )
    ):
        raise CodexAccountError("A competing Codex credential is configured for this invocation.")


def _home(env: dict[str, str]) -> Path:
    value = env.get("CODEX_HOME")
    return Path(value).expanduser().resolve() if value else (Path.home() / ".codex").resolve()


def _config(binary, env, cwd=None, *, overrides=()):
    args = {"includeLayers": False}
    if cwd is not None:
        args["cwd"] = str(cwd)
    responses = _rpc(binary, env, ("config/read", args), overrides=overrides)
    native_home = native.rpc_result(responses, 0).get("codexHome")
    if not isinstance(native_home, str) or Path(native_home).resolve() != _home(env):
        raise CodexAccountError("Codex did not confirm the intended native home.")
    result = native.rpc_result(responses, 1)
    config = result.get("config")
    if not isinstance(config, dict) or "cli_auth_credentials_store" not in config:
        raise CodexAccountError("Codex credential mode could not be verified.")
    return config


def _profile_env(env, home):
    return {**env, "CODEX_HOME": str(home)}


def _storage(config: dict) -> str:
    value = config["cli_auth_credentials_store"]
    mode = value if value is not None else "file"
    if mode not in ("file", "keyring", "auto", "ephemeral"):
        raise CodexAccountError("Codex credential mode is unsupported.")
    return mode


class CodexAccounts:
    """First-party Codex enrollment and verified identity for the host registry."""

    def __init__(self, executable: Callable[[], str | None]):
        self.executable = executable
        self.logins: dict[Path, tuple[ManagedProcessControl, Event]] = {}

    def _resolve(self):
        binary = self.executable()
        if binary is None:
            raise CodexAccountError("Codex CLI is unavailable.")
        env = subprocess_env_for_cli(binary)
        _check_environment(env)
        return binary, env

    def _file_home(self) -> Path:
        binary, env = self._resolve()
        if _storage(_config(binary, env)) != "file":
            raise CodexAccountError("Codex switching requires file-backed native credentials.")
        home = _home(env)
        info = home.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise CodexAccountError("The regular Codex home is unsafe for account switching.")
        return home

    def capture(self, profile: NativeAccountProfile) -> None:
        home = self._file_home()
        if profile.directory is None:
            raise CodexAccountError("Codex adoption requires a private saved profile.")
        _private_directory(profile.directory, "unsafe_profile")
        with _native_lock(home):
            _write_credential(profile.directory, _read_credential(home))

    def activate(
        self, profile: NativeAccountProfile | None, outgoing: NativeAccountProfile | None
    ) -> None:
        home = self._file_home()
        target = profile.directory if profile is not None else None
        if profile is not None and target is None:
            raise CodexAccountError("Select a saved Codex account.")
        if target is not None:
            _private_directory(target, "unsafe_profile")
        if outgoing is not None:
            if outgoing.directory is None:
                raise CodexAccountError("Adopt the current Codex login before switching accounts.")
            _private_directory(outgoing.directory, "unsafe_profile")
        binary, env = self._resolve()
        expected = self.read_identity(profile) if profile is not None else None
        previous = self.read_identity(outgoing) if outgoing is not None else None
        active = native_identity(binary, env)
        if profile is not None and not isinstance(expected, NativeIdentity):
            raise CodexAccountError("Codex could not verify the account before switching.")
        original = _credential(home)
        if outgoing is None:
            if active != "needs_sign_in" or original is not None or target is None:
                raise CodexAccountError("Adopt the current Codex login before switching accounts.")
        elif (
            not isinstance(previous, NativeIdentity)
            or not isinstance(active, NativeIdentity)
            or previous.principal_key != active.principal_key
        ):
            raise CodexAccountError("The native Codex login changed outside account selection.")
        selected = _credential(target) if target is not None else None
        if target is not None and selected is None:
            raise CodexAccountError("Codex native credentials are unavailable.")
        with _native_lock(home):
            if _credential(home) != original or (
                target is not None and _credential(target) != selected
            ):
                raise CodexAccountError("Codex credentials changed during selection. Retry.")
            if outgoing is not None and outgoing.directory is not None:
                _write_credential(outgoing.directory, _read_credential(home))
            if target is None:
                (home / "auth.json").unlink()
            # Reselecting the active identity must retain its renewed credentials.
            elif not isinstance(active, NativeIdentity) or expected != active:
                _write_credential(home, _read_credential(target))

    def read_identity(self, profile: NativeAccountProfile) -> NativeIdentity | NativeIdentityIssue:
        try:
            binary, env = self._resolve()
        except CodexAccountError as exc:
            return "conflicting_credentials" if "competing" in str(exc) else "unavailable"
        try:
            config = _config(binary, env)
            mode = _storage(config)
            if profile.directory is not None and mode != "file":
                return "unavailable"
            regular = _home(env)
            home = profile.directory or regular
        except (CodexAccountError, HostAccountError, OSError):
            return "unavailable"
        return native_identity(
            binary,
            _profile_env(env, home),
            overrides=("-c", f'cli_auth_credentials_store="{mode}"'),
        )

    def start_login(self, profile: NativeAccountProfile) -> None:
        binary, env = self._resolve()
        directory = profile.directory
        if directory is None:
            raise CodexAccountError("Codex enrollment requires a private isolated home.")
        config = _config(binary, env)
        mode = _storage(config)
        if mode != "file":
            raise CodexAccountError(
                "The configured Codex credential mode cannot enroll a switchable login."
            )
        if config.get("forced_login_method") not in (None, "chatgpt"):
            raise CodexAccountError("The configured Codex login policy disallows ChatGPT sign-in.")
        overrides: tuple[str, ...] = ("-c", 'cli_auth_credentials_store="file"')
        workspaces = config.get("forced_chatgpt_workspace_id")
        if workspaces is not None:
            overrides += ("-c", "forced_chatgpt_workspace_id=" + json.dumps(workspaces))
        marker = directory / ".stackos-native-login"
        fd = _private_fd(marker, os.O_RDWR | os.O_CREAT | os.O_EXCL)
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, b"owner-fenced-v1")
        control = ManagedProcessControl()
        settled = Event()
        ready = Event()
        done = Event()
        self.logins[directory] = (control, done)

        def run_login() -> None:
            try:
                with open_app_server(
                    binary,
                    directory,
                    _profile_env(env, directory),
                    control,
                    overrides=overrides,
                    pass_fds=(fd,),
                ) as session:
                    result = session.call("account/login/start", {"type": "chatgpt"})
                    url = result.get("authUrl")
                    login_id = result.get("loginId")
                    if not isinstance(url, str):
                        return
                    parsed = urlsplit(url)
                    if (
                        parsed.scheme != "https"
                        or parsed.hostname not in {"auth.openai.com", "chatgpt.com"}
                        or parsed.username is not None
                        or parsed.password is not None
                        or not isinstance(login_id, str)
                        or not login_id
                        or not webbrowser.open(url)
                    ):
                        return
                    ready.set()
                    settled.set()
                    session.wait_for_login(login_id, timeout=14 * 60)
            except Exception:
                # The runtime marker, not private native responses, controls recovery.
                pass
            finally:
                if control.retirement_status() in ("stopped", "prevented"):
                    marker.unlink(missing_ok=True)
                    self.logins.pop(directory, None)
                os.close(fd)
                done.set()
                settled.set()

        try:
            Thread(target=run_login, daemon=True, name="stackos-codex-login").start()
        except BaseException:
            if control.stop(timeout=5) in ("stopped", "prevented"):
                marker.unlink(missing_ok=True)
                self.logins.pop(directory, None)
                os.close(fd)
            raise
        if not settled.wait(15) or not ready.is_set():
            control.stop(timeout=5)
            raise CodexAccountError("Could not start the first-party Codex sign-in.")

    def discard_login(self, profile: NativeAccountProfile) -> bool:
        directory = profile.directory
        if directory is None:
            return False
        marker = directory / ".stackos-native-login"
        running = self.logins.get(directory)
        if running is not None:
            control, done = running
            if control.stop(timeout=5) not in ("stopped", "prevented") or not done.wait(2):
                return False
        try:
            fd = _private_fd(marker, os.O_RDONLY)
        except FileNotFoundError:
            pass
        else:
            try:
                if os.read(fd, 32) != b"owner-fenced-v1":
                    return False
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return False
                marker.unlink()
            finally:
                os.close(fd)
        # Removing the isolated file does not revoke or mutate any other
        # native session for a duplicate principal.
        _credential(directory)
        (directory / "auth.json").unlink(missing_ok=True)
        return True


def _credential(home: Path) -> FileIdentity | None:
    try:
        contents = (home / "auth.json").lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(contents.st_mode)
        or contents.st_uid != os.geteuid()
        or contents.st_mode & 0o077
        or contents.st_nlink != 1
    ):
        raise CodexAccountError("Codex requires an owner-private native credential file.")
    return contents.st_dev, contents.st_ino, contents.st_size, contents.st_mtime_ns


@contextmanager
def _native_lock(home: Path) -> Iterator[None]:
    # Coordinate only the credential update with native auth writes.
    fd = os.open(home / "auth.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise CodexAccountError("Codex native auth lock is unsafe.")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CodexAccountError(
                "Codex is updating its native credentials. Retry when idle."
            ) from None
        yield
    finally:
        os.close(fd)


def _read_credential(home: Path) -> bytes:
    if _credential(home) is None:
        raise CodexAccountError("Codex native credentials are unavailable.")
    fd = _private_fd(home / "auth.json", os.O_RDONLY)
    with os.fdopen(fd, "rb") as source:
        return source.read()


def _write_credential(home: Path, data: bytes) -> None:
    """Atomically replace auth.json; a failed staging/write preserves the old file."""
    _credential(home)
    with NamedTemporaryFile(dir=home, delete=False) as temp:
        staged = Path(temp.name)
        try:
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
            staged.replace(home / "auth.json")
        finally:
            staged.unlink(missing_ok=True)
