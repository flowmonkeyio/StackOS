"""Grok-owned credential snapshots; selection retains the ordinary native home."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import subprocess
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Thread

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
from stackos.host_mcp.agent_execution import ManagedProcessControl
from stackos.host_mcp.discovery import subprocess_env_for_cli


class GrokAccountError(Exception):
    """Safe account diagnostic, never a native response or credential."""


def _check_environment(env: dict[str, str]) -> None:
    if any(env.get(key) for key in ("GROK_AUTH", "XAI_API_KEY", "GROK_CODE_XAI_API_KEY")):
        raise GrokAccountError("A competing Grok credential is configured for this invocation.")
    if any(
        env.get(key) and not Path(env[key]).is_absolute() for key in ("GROK_AUTH_PATH", "GROK_HOME")
    ):
        raise GrokAccountError(
            "Grok account switching requires absolute native auth and home paths."
        )


def _auth_path(env: dict[str, str]) -> Path:
    # Native precedence: a nonempty explicit auth path wins; empty falls back.
    value = env.get("GROK_AUTH_PATH")
    if value:
        return Path(value)
    home = env.get("GROK_HOME")
    return (Path(home) if home else Path.home() / ".grok") / "auth.json"


def _profile_env(env: dict[str, str], profile: NativeAccountProfile) -> dict[str, str]:
    if profile.directory is None:
        return env
    _private_directory(profile.directory, "unsafe_profile")
    return {**env, "GROK_AUTH_PATH": str(profile.directory / "auth.json")}


def native_identity(binary: str, env: dict[str, str]) -> NativeIdentity | NativeIdentityIssue:
    responses = native.read_rpc(
        binary,
        ["--no-auto-update", "agent", "stdio"],
        [
            (
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {},
                    "clientInfo": {"name": "stackos_accounts", "version": "1"},
                },
            ),
            ("_x.ai/auth/info", {}),
        ],
        env=env,
    )
    info = native.rpc_result(responses, 1)
    if not info:
        return "unavailable"
    if info.get("methodId") not in ("cached_token", "grok.com", "oidc"):
        return "conflicting_credentials" if info.get("methodId") else "needs_sign_in"
    if info.get("methodId") in ("grok.com", "oidc") and not any(
        info.get(key) for key in ("principalId", "principalType", "email")
    ):
        return "needs_sign_in"
    if not info.get("email"):
        return "missing_email"
    if any(
        not isinstance(info.get(key), str) or not info[key].strip()
        for key in ("principalId", "principalType")
    ) or any(
        info.get(key) is not None and not isinstance(info[key], str)
        for key in ("organizationId", "teamId")
    ):
        return "unavailable"
    # Keep both native scope dimensions, and the principal namespace, unambiguous.
    try:
        return NativeIdentity(
            principal=json.dumps([info["principalType"], info["principalId"]]),
            workspace=json.dumps([info.get("organizationId"), info.get("teamId")]),
            email=info["email"],
        )
    except (ValidationError, TypeError, ValueError):
        return "unavailable"


def _read(path: Path) -> bytes:
    fd = _private_fd(path, os.O_RDONLY)
    with os.fdopen(fd, "rb") as source:
        return source.read()


def _write(path: Path, data: bytes) -> None:
    # Reject unsafe existing destinations before staging; preserve opaque native bytes.
    try:
        fd = _private_fd(path, os.O_RDONLY)
    except FileNotFoundError:
        pass
    else:
        os.close(fd)
    with NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        staged = Path(temp.name)
        try:
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)


class GrokAccounts:
    def __init__(self, executable: Callable[[], str | None]):
        self.executable = executable
        self._logins: dict[Path, ManagedProcessControl] = {}

    def _resolve(self):
        binary = self.executable()
        if binary is None:
            raise GrokAccountError("Grok CLI is unavailable.")
        env = subprocess_env_for_cli(binary)
        _check_environment(env)
        return binary, env

    def capture(self, profile: NativeAccountProfile) -> None:
        _, env = self._resolve()
        if profile.directory is None:
            raise GrokAccountError("Grok adoption requires a private saved profile.")
        _private_directory(profile.directory, "unsafe_profile")
        _write(profile.directory / "auth.json", _read(_auth_path(env)))

    def read_identity(self, profile: NativeAccountProfile) -> NativeIdentity | NativeIdentityIssue:
        try:
            binary, env = self._resolve()
            selected_env = _profile_env(env, profile)
            # Fail before native invocation on unsafe credentials.
            with suppress(FileNotFoundError):
                _read(_auth_path(selected_env))
            return native_identity(binary, selected_env)
        except GrokAccountError as exc:
            return "conflicting_credentials" if "competing" in str(exc) else "unavailable"
        except (HostAccountError, OSError):
            return "unavailable"

    def activate(
        self, profile: NativeAccountProfile | None, outgoing: NativeAccountProfile | None
    ) -> None:
        binary, env = self._resolve()
        for item in (profile, outgoing):
            if item is not None:
                if item.directory is None:
                    raise GrokAccountError("Grok selection requires a private saved profile.")
                _private_directory(item.directory, "unsafe_profile")
        path = _auth_path(env)
        parent = path.parent.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.geteuid()
            or parent.st_mode & 0o022
        ):
            raise GrokAccountError("The native Grok credential directory is unsafe.")
        expected = self.read_identity(profile) if profile is not None else None
        previous = self.read_identity(outgoing) if outgoing is not None else None
        try:
            original = _read(path)
        except FileNotFoundError:
            original = None
        active = native_identity(binary, env)
        if outgoing is None:
            if profile is None or not isinstance(expected, NativeIdentity):
                raise GrokAccountError("Grok could not verify the selected account.")
            if original is not None or active != "needs_sign_in":
                raise GrokAccountError("Adopt the current Grok login before switching accounts.")
            # No native auth existed. Do not overwrite a file created since the check.
            if path.exists() or path.is_symlink():
                raise GrokAccountError(
                    "Grok credentials changed during selection. Retry when idle."
                )
            assert profile.directory is not None
            _write(path, _read(profile.directory / "auth.json"))
            return
        if profile is None:
            # Internal rollback of a first activation, never a public logout.
            if not isinstance(previous, NativeIdentity) or not isinstance(active, NativeIdentity):
                raise GrokAccountError("Grok could not verify the account before rollback.")
            if previous.principal_key != active.principal_key or original is None:
                raise GrokAccountError("The native Grok login changed outside account selection.")
            if _read(path) != original:
                raise GrokAccountError("Grok credentials changed during rollback. Retry when idle.")
            assert outgoing.directory is not None
            _write(outgoing.directory / "auth.json", original)
            path.unlink()
            return
        if (
            not isinstance(expected, NativeIdentity)
            or not isinstance(previous, NativeIdentity)
            or not isinstance(active, NativeIdentity)
            or original is None
        ):
            raise GrokAccountError("Grok could not verify the account before switching.")
        if previous.principal_key != active.principal_key:
            raise GrokAccountError("The native Grok login changed outside account selection.")
        assert profile.directory is not None and outgoing.directory is not None
        selected = _read(profile.directory / "auth.json")
        # HostAccounts serializes selections. Compare again after identity reads, which
        # may refresh native auth; do not pretend to lock independently running CLIs.
        if _read(path) != original:
            raise GrokAccountError("Grok credentials changed during selection. Retry when idle.")
        _write(outgoing.directory / "auth.json", original)
        if expected.principal_key != active.principal_key:
            _write(path, selected)

    def start_login(self, profile: NativeAccountProfile) -> None:
        binary, env = self._resolve()
        if profile.directory is None:
            raise GrokAccountError("Grok enrollment requires a private saved profile.")
        env = _profile_env(env, profile)
        directory = profile.directory
        marker = directory / ".stackos-native-login"
        fd = _private_fd(marker, os.O_RDWR | os.O_CREAT | os.O_EXCL)
        fcntl.flock(fd, fcntl.LOCK_EX)
        control = ManagedProcessControl()
        try:
            process = control.spawn(
                [binary, "login", "--oauth"],
                cwd=directory,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=(fd,),
            )
            self._logins[directory] = control

            def retire():
                try:
                    process.wait(timeout=14 * 60)
                except subprocess.TimeoutExpired:
                    pass
                finally:
                    if control.stop() == "stopped":
                        self._logins.pop(directory, None)
                        marker.unlink(missing_ok=True)
                    os.close(fd)

            Thread(target=retire, daemon=True, name="stackos-grok-login").start()
        except BaseException:
            if control.stop() in ("stopped", "prevented"):
                marker.unlink(missing_ok=True)
            os.close(fd)
            raise

    def discard_login(self, profile: NativeAccountProfile) -> bool:
        directory = profile.directory
        if directory is None:
            return False
        if not directory.exists():
            return True
        _private_directory(directory, "unsafe_profile")
        control = self._logins.get(directory)
        if control is not None and control.stop() != "stopped":
            return False
        marker = directory / ".stackos-native-login"
        fd = _private_fd(marker, os.O_RDWR | os.O_CREAT)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            path = directory / "auth.json"
            with suppress(FileNotFoundError):
                _read(path)
            path.unlink(missing_ok=True)
            marker.unlink(missing_ok=True)
            return True
        finally:
            os.close(fd)
