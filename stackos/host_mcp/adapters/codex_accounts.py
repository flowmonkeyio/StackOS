"""Codex-owned native account profiles and same-home file credential leases."""

from __future__ import annotations

import ctypes
import fcntl
import json
import os
import stat
import sys
import webbrowser
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event, Thread
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import ValidationError

from stackos.host_mcp import subscription_native as native
from stackos.host_mcp.accounts import (
    HostAccountError,
    NativeAccountProfile,
    NativeIdentity,
    NativeIdentityIssue,
    ResolvedAccountSelection,
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


def selected_quota_responses(binary: str, selection: ResolvedAccountSelection) -> list[dict]:
    """Read quota only from the same native process that confirmed the selection."""
    if selection.profile is None or selection.principal_key is None:
        return []
    env = subprocess_env_for_cli(binary)
    try:
        _check_environment(env)
        regular = _home(env)
        mode = _storage(_config(binary, env))
        if selection.profile.directory is not None and mode != "file":
            return []
        home = AuthExchange(regular, lambda _: None).profile_home(selection.profile.directory)
        responses = _rpc(
            binary,
            _profile_env(env, home),
            _ACCOUNT,
            _SESSIONS,
            ("account/rateLimits/read", None),
            overrides=("-c", f'cli_auth_credentials_store="{mode}"'),
        )
        identity = _identity(native.rpc_result(responses, 1), native.rpc_result(responses, 2))
        attributed_workspace = native.rpc_result(responses, 3).get("accountId")
        return (
            [responses[0], responses[1], responses[3]]
            if isinstance(identity, NativeIdentity)
            and identity.principal_key == selection.principal_key
            and len(responses) == 4
            and attributed_workspace in (None, identity.workspace)
            else []
        )
    except (CodexAccountError, HostAccountError, OSError):
        return []


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


@contextmanager
def ordinary_launch(
    binary: str, *, env: dict[str, str] | None = None
) -> Iterator[tuple[dict[str, str], int]]:
    """Keep native defaults on the shared side of a managed credential lease."""
    env = subprocess_env_for_cli(binary) if env is None else env
    exchange = AuthExchange(_home(env), lambda _: None)
    try:
        with exchange.lease(shared=True) as fd:
            if exchange.journal.exists():
                raise CodexAccountError("The previous Codex credential exchange needs recovery.")
            yield env, fd
    except (HostAccountError, OSError):
        raise CodexAccountError("The private Codex execution lease is unavailable.") from None


def _storage(config: dict) -> str:
    value = config["cli_auth_credentials_store"]
    mode = value if value is not None else "file"
    if mode not in ("file", "keyring", "auto", "ephemeral"):
        raise CodexAccountError("Codex credential mode is unsupported.")
    return mode


def exact_thread(binary: str, env: dict[str, str], thread: str, *, overrides=()) -> None:
    try:
        exact = str(UUID(thread)) == thread
    except (ValueError, AttributeError, TypeError):
        exact = False
    if not exact:
        raise CodexAccountError("An exact Codex conversation UUID is required.")
    responses = _rpc(
        binary,
        env,
        ("thread/read", {"threadId": thread, "includeTurns": False}),
        overrides=overrides,
    )
    record = native.rpc_result(responses, 1).get("thread")
    if not isinstance(record, dict) or record.get("id") != thread:
        raise CodexAccountError("The exact Codex conversation was not found in the regular home.")
    if record.get("modelProvider") != "openai":
        raise CodexAccountError("The saved Codex conversation uses a different model provider.")


@contextmanager
def selected_launch(
    binary: str,
    cwd: Path,
    selection: ResolvedAccountSelection,
    *,
    provider: str | None = None,
    overrides=(),
    allowed: Callable[[], bool] | None = None,
) -> Iterator[tuple[dict[str, str], int]]:
    env = subprocess_env_for_cli(binary)
    _check_environment(env)
    home = _home(env)
    config = _config(binary, env, cwd, overrides=overrides)
    if (provider or config.get("model_provider") or "openai") != "openai":
        raise CodexAccountError("Managed Codex accounts require the native OpenAI provider.")
    custom_providers = config.get("model_providers")
    if isinstance(custom_providers, dict) and "openai" in custom_providers:
        raise CodexAccountError("A custom provider conflicts with the managed Codex account.")
    mode = _storage(config)
    if selection.profile is None or selection.principal_key is None:
        raise CodexAccountError("The Codex account selection is incomplete.")
    isolated = selection.profile.directory
    if isolated is not None and mode != "file":
        raise CodexAccountError("The configured Codex credential backend cannot switch this login.")

    def principal(path: Path) -> Principal | None:
        identity = native_identity(
            binary,
            _profile_env(env, path),
            overrides=("-c", f'cli_auth_credentials_store="{mode}"'),
        )
        return identity.principal_key if isinstance(identity, NativeIdentity) else None

    try:
        exchange = AuthExchange(home, principal)
        if mode != "file" and exchange.journal.exists():
            raise CodexAccountError(
                "The previous file-backed Codex exchange needs operator recovery."
            )
        with exchange.selected(isolated, selection.principal_key, allowed=allowed) as fd:
            if principal(home) != selection.principal_key:
                raise CodexAccountError(
                    "The regular Codex home did not confirm the selected account."
                )
            yield env, fd
    except (HostAccountError, OSError):
        raise CodexAccountError("The private Codex credential store is unavailable.") from None


class CodexAccounts:
    """First-party Codex enrollment and verified identity for the host registry."""

    def __init__(self, executable: Callable[[], str | None]):
        self.executable = executable
        self.logins: dict[Path, ManagedProcessControl] = {}

    def _resolve(self):
        binary = self.executable()
        if binary is None:
            raise CodexAccountError("Codex CLI is unavailable.")
        env = subprocess_env_for_cli(binary)
        _check_environment(env)
        return binary, env

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
            home = AuthExchange(regular, lambda _: None).profile_home(profile.directory)
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
        fd = _private_fd(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        os.close(fd)
        control = ManagedProcessControl()
        settled = Event()
        ready = Event()
        self.logins[directory] = control

        def run_login() -> None:
            try:
                with open_app_server(
                    binary,
                    directory,
                    _profile_env(env, directory),
                    control,
                    overrides=overrides,
                ) as (session, _):
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
                settled.set()

        Thread(target=run_login, daemon=True, name="stackos-codex-login").start()
        if not settled.wait(15) or not ready.is_set():
            control.stop()
            raise CodexAccountError("Could not start the first-party Codex sign-in.")

    def discard_login(self, profile: NativeAccountProfile) -> bool:
        directory = profile.directory
        if directory is None:
            return False
        marker = directory / ".stackos-native-login"
        control = self.logins.get(directory)
        if control is not None:
            if control.stop() != "stopped":
                return False
            marker.unlink(missing_ok=True)
        if marker.exists():
            return False
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
    # Codex 0.170 serializes native writes on auth.lock. Unlike the StackOS
    # execution lease, this lock is released before any native process runs.
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


def _exchange(first: Path, second: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        swap = libc.renamex_np
        swap.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = swap(os.fsencode(first), os.fsencode(second), 0x00000002)
    elif sys.platform == "linux" and hasattr(libc, "renameat2"):
        swap = libc.renameat2
        swap.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        result = swap(-100, os.fsencode(first), -100, os.fsencode(second), 2)
    else:
        raise CodexAccountError(
            "This filesystem has no supported atomic Codex credential exchange."
        )
    if result != 0:
        raise CodexAccountError(
            "Could not exchange Codex credentials atomically on this filesystem."
        )


def _sync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class AuthExchange:
    """Borrow the selected native credential while preserving refresh and recovery."""

    def __init__(self, home: Path, principal: Callable[[Path], Principal | None]):
        self.home = home
        self.principal = principal
        self.state = home / ".stackos-accounts"
        self.journal = self.state / "exchange.json"

    def profile_home(self, isolated: Path | None) -> Path:
        """Resolve the physical home of a logical profile during an active lease."""
        journal = self._read_journal()
        ordinary = isolated or self.home
        if journal is None:
            return ordinary
        parked_home, original, _selected, _expected = journal
        if isolated is not None and isolated.resolve() != parked_home.resolve():
            return isolated
        active, parked = _credential(self.home), _credential(parked_home)
        if active == original:
            return ordinary
        if parked != original or active is None:
            raise CodexAccountError("A native Codex credential changed during the exchange.")
        return self.home if isolated is not None else parked_home

    @contextmanager
    def lease(self, *, shared: bool = False) -> Iterator[int]:
        # Ordinary Stock Codex can start with no existing native home, and its
        # home configuration may be read-only. Keep the process fence separate
        # from credential storage and use the same owner-local path in every
        # StackOS entry point, independent of inherited TMPDIR.
        directory = Path(f"/tmp/stackos-codex-leases-{os.geteuid()}")
        directory.mkdir(mode=0o700, exist_ok=True)
        _private_directory(directory, "unsafe_profile")
        key = sha256(os.fsencode(self.home.resolve())).hexdigest()
        fd = _private_fd(directory / key, os.O_RDWR | os.O_CREAT)
        try:
            try:
                mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
                fcntl.flock(fd, mode | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CodexAccountError("The managed Codex home is in use.") from None
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def selected(
        self,
        isolated: Path | None,
        expected: Principal | None,
        *,
        allowed: Callable[[], bool] | None = None,
    ) -> Iterator[int]:
        try:
            with self.lease() as fd:
                self._prepare_state()
                self._recover()
                if allowed is not None and not allowed():
                    raise CodexAccountError("Codex account selection changed before launch.")
                if isolated is None:
                    yield fd
                    return
                _private_directory(isolated, "unsafe_profile")
                if isolated.resolve() == self.home.resolve():
                    raise CodexAccountError(
                        "The enrolled and regular Codex homes must be distinct."
                    )
                if expected is None:
                    raise CodexAccountError("Codex has no verified principal for this selection.")
                original = _credential(self.home)
                selected = _credential(isolated)
                if selected is None or self.principal(isolated) != expected:
                    raise CodexAccountError(
                        "Codex could not confirm the selected enrolled account."
                    )
                if _credential(isolated) != selected:
                    raise CodexAccountError(
                        "The selected Codex credential changed during preflight."
                    )
                self._swap_in(isolated, original, selected, expected, allowed)
                try:
                    if self.principal(self.home) != expected:
                        raise CodexAccountError(
                            "The regular Codex home did not confirm the selected account."
                        )
                    if allowed is not None and not allowed():
                        raise CodexAccountError("Codex account selection changed before launch.")
                    yield fd
                finally:
                    self._recover()
        except OSError:
            raise CodexAccountError(
                "Could not access the private Codex credential transaction."
            ) from None

    def _prepare_state(self) -> None:
        # The regular home may be readable by other users. Its transaction state
        # and both native credential files must themselves remain private.
        info = self.home.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise CodexAccountError("The regular Codex home is unsafe for account switching.")
        self.state.mkdir(mode=0o700, exist_ok=True)
        _private_directory(self.state, "unsafe_profile")

    def _write_journal(
        self,
        isolated: Path,
        original: FileIdentity | None,
        selected: FileIdentity,
        expected: Principal,
    ) -> None:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.state, delete=False) as temp:
            staged = Path(temp.name)
            try:
                json.dump(
                    {
                        "profile": str(isolated),
                        "original": original,
                        "selected": selected,
                        "principal": expected,
                    },
                    temp,
                )
                temp.flush()
                os.fsync(temp.fileno())
                staged.replace(self.journal)
            finally:
                staged.unlink(missing_ok=True)
        _sync_directory(self.state)

    def _swap_in(
        self,
        isolated: Path,
        original: FileIdentity | None,
        selected: FileIdentity,
        expected: Principal,
        allowed: Callable[[], bool] | None,
    ) -> None:
        with _native_lock(self.home), _native_lock(isolated):
            if allowed is not None and not allowed():
                raise CodexAccountError("Codex account selection changed before launch.")
            if _credential(self.home) != original or _credential(isolated) != selected:
                raise CodexAccountError("A native Codex credential changed before launch.")
            self._write_journal(isolated, original, selected, expected)
            active = self.home / "auth.json"
            parked = isolated / "auth.json"
            if original is None:
                parked.replace(active)
            else:
                _exchange(active, parked)
            _sync_directory(self.home)
            _sync_directory(isolated)

    def _read_journal(self):
        try:
            fd = _private_fd(self.journal, os.O_RDONLY)
        except FileNotFoundError:
            return None
        try:
            with os.fdopen(fd, encoding="utf-8") as file:
                data = json.loads(file.read(4096))
            return (
                Path(data["profile"]),
                tuple(data["original"]) if data["original"] else None,
                tuple(data["selected"]),
                tuple(data["principal"]),
            )
        except (KeyError, TypeError, ValueError):
            raise CodexAccountError(
                "The Codex credential exchange needs operator recovery."
            ) from None

    def _recover(self) -> None:
        journal = self._read_journal()
        if journal is None:
            return
        isolated, original, selected, expected = journal
        _private_directory(isolated, "unsafe_profile")
        active_now = _credential(self.home)
        parked_now = _credential(isolated)
        before_swap = active_now == original and parked_now is not None and parked_now != original
        if not before_swap:
            if parked_now != original or active_now is None:
                raise CodexAccountError("A native Codex credential changed during the exchange.")
            if active_now != selected:
                if self.principal(self.home) != expected:
                    raise CodexAccountError(
                        "The active Codex credential changed during the exchange."
                    )
                if _credential(self.home) != active_now:
                    raise CodexAccountError(
                        "The active Codex credential changed during the exchange."
                    )
        with _native_lock(self.home), _native_lock(isolated):
            if _credential(self.home) != active_now or _credential(isolated) != parked_now:
                raise CodexAccountError("A native Codex credential changed during recovery.")
            if not before_swap:
                active = self.home / "auth.json"
                parked = isolated / "auth.json"
                if original is None:
                    active.replace(parked)
                else:
                    _exchange(active, parked)
                _sync_directory(self.home)
                _sync_directory(isolated)
            self.journal.unlink()
            _sync_directory(self.state)
