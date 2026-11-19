"""User-local harness account metadata and desired selection."""

from __future__ import annotations

import fcntl
import os
import stat
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, Protocol
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints
from pydantic import ValidationError as ModelError

AccountRef = Annotated[
    str, StringConstraints(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
]
_IdentityPart = Annotated[
    str,
    StringConstraints(
        max_length=512,
        pattern=r"^[^\x00-\x1f\x7f]*[^\s\x00-\x1f\x7f][^\x00-\x1f\x7f]*$",
    ),
]
_Email = Annotated[
    str,
    StringConstraints(
        max_length=320,
        pattern=r"^[^\s@\x00-\x1f\x7f]+@[^\s@\x00-\x1f\x7f]+\.[^\s@\x00-\x1f\x7f]+$",
    ),
]
NativeIdentityIssue = Literal[
    "needs_sign_in", "expired", "missing_email", "conflicting_credentials", "unavailable"
]
IdentityStatus = Literal[NativeIdentityIssue, "verified", "identity_changed"]
EnrollmentStatus = Literal["pending", "enrolled", "duplicate", "cancelled", "expired", "failed"]
_ENROLLMENT_LIFETIME = timedelta(minutes=15)


class NativeIdentity(BaseModel):
    """Identity verified by the native subscription path, with email for display."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    principal: _IdentityPart = Field(repr=False)
    workspace: _IdentityPart | None = Field(default=None, repr=False)
    email: _Email = Field(repr=False)

    @property
    def principal_key(self) -> tuple[str, str | None]:
        return self.principal, self.workspace


@dataclass(frozen=True)
class NativeAccountProfile:
    """Trusted internal profile context. A null directory means the normal native profile."""

    ref: str | None
    directory: Path | None = field(repr=False)


class NativeAccountAdapter(Protocol):
    """Provider-owned native login and identity checks. Calls must be bounded."""

    def capture(self, profile: NativeAccountProfile) -> None:
        """Save the current native login in this private profile before adoption."""
        ...

    def activate(
        self, profile: NativeAccountProfile, outgoing: NativeAccountProfile | None
    ) -> None:
        """Persistently select the native login, saving outgoing credentials first.

        Raise on failure; selection metadata is committed only after return.
        """
        ...

    def read_identity(self, profile: NativeAccountProfile) -> NativeIdentity | NativeIdentityIssue:
        """Check the settled native subscription identity without starting login."""
        ...

    def start_login(self, profile: NativeAccountProfile) -> None:
        """Start native operator sign-in in this isolated profile and return promptly."""
        ...

    def discard_login(self, profile: NativeAccountProfile) -> bool:
        """Idempotently retire by profile ref, even if its directory is gone.

        Return True only when native cleanup is confirmed.
        """
        ...


class DesiredAccount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    account_ref: AccountRef | None = None
    revision: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class ResolvedAccountSelection:
    """One selection snapshot for native launch or quota work."""

    desired: DesiredAccount
    profile: NativeAccountProfile | None = None
    principal_key: tuple[str, str | None] | None = field(default=None, repr=False)
    enrolled_directories: tuple[Path, ...] = field(default=(), repr=False, compare=False)


class AccountView(BaseModel):
    account_ref: AccountRef
    email: _Email
    identity_status: IdentityStatus


class EnrollmentView(BaseModel):
    enrollment_ref: AccountRef
    status: EnrollmentStatus
    expires_at: AwareDatetime
    account_ref: AccountRef | None = None
    identity_status: IdentityStatus | None = None
    cleanup_pending: bool = False


class HostAccountsView(BaseModel):
    desired: DesiredAccount
    accounts: list[AccountView]
    pending_enrollment: EnrollmentView | None = None
    pending_cleanups: list[EnrollmentView] = Field(default_factory=list)


class HostAccountInventory(BaseModel):
    hosts: dict[str, HostAccountsView]


class HostAccountError(Exception):
    """Only closed, safe error codes cross the service boundary."""

    def __init__(self, code: str, *, status: IdentityStatus | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class _Account(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_ref: AccountRef
    native_default: bool
    identity: NativeIdentity
    identity_status: IdentityStatus = "verified"

    def observe_identity(self, identity: NativeIdentity | NativeIdentityIssue) -> IdentityStatus:
        if isinstance(identity, NativeIdentity):
            if self.identity.principal_key == identity.principal_key:
                self.identity = identity
                self.identity_status = "verified"
            else:
                self.identity_status = "identity_changed"
        else:
            self.identity_status = identity
        return self.identity_status

    def view(self, ref: str) -> AccountView:
        return AccountView(
            account_ref=ref, email=self.identity.email, identity_status=self.identity_status
        )


class _Enrollment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_ref: AccountRef
    status: EnrollmentStatus = "pending"
    expires_at: AwareDatetime
    account_ref: AccountRef | None = None
    identity_status: IdentityStatus | None = None
    cleanup_pending: bool = False

    def is_expired(self, now: datetime) -> bool:
        return self.status == "pending" and now >= self.expires_at

    def view(self, ref: str, now: datetime) -> EnrollmentView:
        return EnrollmentView(
            enrollment_ref=ref,
            status="expired" if self.is_expired(now) else self.status,
            expires_at=self.expires_at,
            account_ref=self.account_ref,
            identity_status=self.identity_status,
            cleanup_pending=self.cleanup_pending or self.is_expired(now),
        )


class _HostState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    desired: DesiredAccount = Field(default_factory=DesiredAccount)
    accounts: dict[AccountRef, _Account] = Field(default_factory=dict)
    enrollments: dict[AccountRef, _Enrollment] = Field(default_factory=dict)


class _State(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    hosts: dict[str, _HostState] = Field(default_factory=dict)


def _private_directory(path: Path, code: str) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise HostAccountError(code)


def _private_fd(path: Path, flags: int, *, atomic_snapshot: bool = False) -> int:
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            # An atomic writer may replace an opened read-only snapshot before
            # this stat. A retired inode stays readable through our descriptor.
            or (info.st_nlink != 1 and not (atomic_snapshot and info.st_nlink == 0))
        ):
            raise HostAccountError("account_store_unavailable")
    except BaseException:
        os.close(fd)
        raise
    return fd


class HostAccounts:
    def __init__(self, data_dir: Path, adapters: Mapping[str, NativeAccountAdapter]) -> None:
        self._root = data_dir / "host-accounts"
        self._state_path = self._root / "accounts.json"
        self._adapters = adapters

    def inventory(self) -> HostAccountInventory:
        state = self._load()
        now = datetime.now(UTC)
        hosts = {}
        for host_key in self._adapters:
            host = state.hosts.get(host_key, _HostState())
            pending = next(
                (
                    row.view(ref, now)
                    for ref, row in host.enrollments.items()
                    if row.status == "pending"
                ),
                None,
            )
            hosts[host_key] = HostAccountsView(
                desired=host.desired,
                accounts=[row.view(ref) for ref, row in host.accounts.items()],
                pending_enrollment=pending,
                pending_cleanups=[
                    row.view(ref, now)
                    for ref, row in host.enrollments.items()
                    if row.cleanup_pending
                ],
            )
        return HostAccountInventory(hosts=hosts)

    def resolve(self, host_key: str) -> ResolvedAccountSelection:
        """Snapshot the desired generation. An unenrolled host keeps its native defaults."""
        if host_key not in self._adapters:
            return ResolvedAccountSelection(DesiredAccount())
        host = self._load().hosts.get(host_key, _HostState())
        enrolled = tuple(
            self._root / f"profile-{row.profile_ref}" for row in host.accounts.values()
        )
        ref = host.desired.account_ref
        if ref is None:
            return ResolvedAccountSelection(host.desired, enrolled_directories=enrolled)
        account = host.accounts.get(ref)
        if account is None:
            raise HostAccountError("account_store_unavailable")
        return ResolvedAccountSelection(
            host.desired, self._account_profile(account), account.identity.principal_key, enrolled
        )

    def adopt(self, host_key: str) -> AccountView:
        adapter = self._adapter(host_key)
        with self._locked_state() as state:
            host = state.hosts.setdefault(host_key, _HostState())
            adopted = next(
                (
                    (ref, account)
                    for ref, account in host.accounts.items()
                    if account.native_default
                ),
                None,
            )
            profile = NativeAccountProfile(None, None)
            identity = self._read_identity(adapter, profile)
            if adopted is not None:
                ref, account = adopted
                status = account.observe_identity(identity)
                self._save(state)
                if status == "identity_changed":
                    raise HostAccountError("default_profile_changed")
                if status != "verified":
                    raise HostAccountError("identity_unverified", status=status)
                return account.view(ref)
            if not isinstance(identity, NativeIdentity):
                raise HostAccountError("identity_unverified", status=identity)
            matched = self._matching_account(host, identity)
            if matched is not None and self._refresh(adapter, matched[1]) == "verified":
                matched[1].native_default = True
                self._save(state)
                return matched[1].view(matched[0])
            ref = matched[0] if matched else str(uuid4())
            profile_ref = str(uuid4())
            try:
                saved = self._isolated_profile(profile_ref, create=True)
                adapter.capture(saved)
                captured = self._read_identity(adapter, saved)
                if (
                    not isinstance(captured, NativeIdentity)
                    or captured.principal_key != identity.principal_key
                ):
                    raise HostAccountError("identity_unverified")
            except Exception:
                raise HostAccountError("activation_failed") from None
            account = self._bind_verified_profile(
                host, ref, profile_ref, identity, native_default=True
            )
            self._save(state)
            return account.view(ref)

    def add(self, host_key: str, enrollment_ref: str) -> EnrollmentView:
        adapter = self._adapter(host_key)
        with self._locked_state() as state:
            host = state.hosts.setdefault(host_key, _HostState())
            now = datetime.now(UTC)
            for row in host.enrollments.values():
                if row.is_expired(now):
                    self._finish(state, adapter, row, "expired")
            if enrollment_ref in host.enrollments:
                return host.enrollments[enrollment_ref].view(enrollment_ref, now)
            if any(row.status == "pending" for row in host.enrollments.values()):
                raise HostAccountError("enrollment_pending")
            row = _Enrollment(profile_ref=str(uuid4()), expires_at=now + _ENROLLMENT_LIFETIME)
            profile = self._isolated_profile(row.profile_ref, create=True)
            host.enrollments[enrollment_ref] = row
            self._save(state)
            try:
                adapter.start_login(profile)
            except Exception:
                row.identity_status = "unavailable"
                self._finish(state, adapter, row, "failed")
            else:
                if row.is_expired(datetime.now(UTC)):
                    self._finish(state, adapter, row, "expired")
            return row.view(enrollment_ref, datetime.now(UTC))

    def complete(self, host_key: str, enrollment_ref: str) -> EnrollmentView:
        adapter = self._adapter(host_key)
        with self._locked_state() as state:
            host = state.hosts.get(host_key, _HostState())
            row = self._enrollment(host, enrollment_ref)
            if row.is_expired(datetime.now(UTC)):
                self._finish(state, adapter, row, "expired")
            if row.status != "pending":
                return row.view(enrollment_ref, datetime.now(UTC))
            identity = self._read_identity(adapter, self._isolated_profile(row.profile_ref))
            if row.is_expired(datetime.now(UTC)):
                self._finish(state, adapter, row, "expired")
            elif isinstance(identity, NativeIdentity):
                matched = self._matching_account(host, identity)
                healthy = matched is not None and self._refresh(adapter, matched[1]) == "verified"
                if row.is_expired(datetime.now(UTC)):
                    self._finish(state, adapter, row, "expired")
                    return row.view(enrollment_ref, datetime.now(UTC))
                row.identity_status = "verified"
                row.account_ref = matched[0] if matched else str(uuid4())
                if healthy:
                    self._finish(state, adapter, row, "duplicate")
                else:
                    self._bind_verified_profile(
                        host, row.account_ref, row.profile_ref, identity, native_default=False
                    )
                    row.status = "enrolled"
                    self._save(state)
            elif row.identity_status != identity:
                row.identity_status = identity
                self._save(state)
            return row.view(enrollment_ref, datetime.now(UTC))

    def cancel(self, host_key: str, enrollment_ref: str) -> EnrollmentView:
        adapter = self._adapter(host_key)
        with self._locked_state() as state:
            row = self._enrollment(state.hosts.get(host_key, _HostState()), enrollment_ref)
            now = datetime.now(UTC)
            if row.status == "pending":
                self._finish(state, adapter, row, "expired" if row.is_expired(now) else "cancelled")
            elif row.cleanup_pending:
                self._cleanup(state, adapter, row)
            return row.view(enrollment_ref, now)

    def select(self, host_key: str, account_ref: str, expected_revision: int) -> DesiredAccount:
        adapter = self._adapter(host_key)
        with self._locked_state() as state:
            host = state.hosts.get(host_key, _HostState())
            if host.desired.revision != expected_revision:
                raise HostAccountError("stale_selection")
            account = self._account(host, account_ref)
            status = self._refresh(adapter, account)
            if status != "verified":
                self._save(state)
                raise HostAccountError("identity_unverified", status=status)
            outgoing = host.accounts.get(host.desired.account_ref or "")
            if outgoing is None:
                outgoing = next((row for row in host.accounts.values() if row.native_default), None)
            previous = host.desired
            try:
                adapter.activate(
                    self._account_profile(account),
                    self._account_profile(outgoing) if outgoing is not None else None,
                )
            except Exception:
                raise HostAccountError("activation_failed") from None
            if host.desired.account_ref != account_ref:
                host.desired = DesiredAccount(
                    account_ref=account_ref, revision=expected_revision + 1
                )
            try:
                self._save(state)
            except (OSError, HostAccountError):
                # Activation saved the outgoing native credentials. Restore them
                # if committing the selection fails, including a post-rename error.
                try:
                    if outgoing is None:
                        raise HostAccountError("selection_restore_failed")
                    adapter.activate(
                        self._account_profile(outgoing), self._account_profile(account)
                    )
                    host.desired = previous
                    if self._load().hosts[host_key].desired != previous:
                        self._save(state)
                except Exception:
                    raise HostAccountError("selection_restore_failed") from None
                raise HostAccountError("account_store_unavailable") from None
            return host.desired

    def refresh(self, host_key: str, account_ref: str) -> AccountView:
        adapter = self._adapter(host_key)
        with self._locked_state() as state:
            account = self._account(state.hosts.get(host_key, _HostState()), account_ref)
            self._refresh(adapter, account)
            self._save(state)
            return account.view(account_ref)

    def _refresh(self, adapter: NativeAccountAdapter, account: _Account) -> IdentityStatus:
        try:
            profile = self._account_profile(account)
        except HostAccountError as exc:
            if exc.code != "profile_missing":
                raise
            return account.observe_identity("needs_sign_in")
        return account.observe_identity(self._read_identity(adapter, profile))

    @staticmethod
    def _matching_account(
        host: _HostState, identity: NativeIdentity
    ) -> tuple[str, _Account] | None:
        return next(
            (
                (ref, account)
                for ref, account in host.accounts.items()
                if account.identity.principal_key == identity.principal_key
            ),
            None,
        )

    @staticmethod
    def _bind_verified_profile(
        host: _HostState,
        account_ref: str,
        profile_ref: str,
        identity: NativeIdentity,
        *,
        native_default: bool,
    ) -> _Account:
        if host.desired.account_ref == account_ref:
            # The same selection now resolves to a new native profile.
            host.desired = DesiredAccount(
                account_ref=account_ref, revision=host.desired.revision + 1
            )
        account = _Account(
            profile_ref=profile_ref, native_default=native_default, identity=identity
        )
        host.accounts[account_ref] = account
        return account

    def _finish(
        self,
        state: _State,
        adapter: NativeAccountAdapter,
        row: _Enrollment,
        status: Literal["duplicate", "cancelled", "expired", "failed"],
    ) -> None:
        row.status = status
        row.cleanup_pending = True
        self._save(state)
        self._cleanup(state, adapter, row)

    def _cleanup(self, state: _State, adapter: NativeAccountAdapter, row: _Enrollment) -> None:
        try:
            retired = adapter.discard_login(
                self._isolated_profile(row.profile_ref, allow_missing=True)
            )
        except Exception:
            return
        if retired is True:
            row.cleanup_pending = False
            self._save(state)

    @staticmethod
    def _read_identity(
        adapter: NativeAccountAdapter, profile: NativeAccountProfile
    ) -> NativeIdentity | NativeIdentityIssue:
        try:
            result = adapter.read_identity(profile)
        except Exception:
            return "unavailable"
        if isinstance(result, NativeIdentity):
            return result
        if result in ("needs_sign_in", "expired", "missing_email", "conflicting_credentials"):
            return result
        return "unavailable"

    def _adapter(self, host_key: str) -> NativeAccountAdapter:
        adapter = self._adapters.get(host_key)
        if adapter is None:
            raise HostAccountError("unsupported_host")
        return adapter

    @staticmethod
    def _account(host: _HostState, ref: str) -> _Account:
        if ref not in host.accounts:
            raise HostAccountError("unknown_account")
        return host.accounts[ref]

    @staticmethod
    def _enrollment(host: _HostState, ref: str) -> _Enrollment:
        if ref not in host.enrollments:
            raise HostAccountError("unknown_enrollment")
        return host.enrollments[ref]

    def _account_profile(self, account: _Account) -> NativeAccountProfile:
        return self._isolated_profile(account.profile_ref)

    def _isolated_profile(
        self, ref: str, *, create: bool = False, allow_missing: bool = False
    ) -> NativeAccountProfile:
        directory = self._root / f"profile-{ref}"
        try:
            _private_directory(self._root, "unsafe_profile")
            if create:
                directory.mkdir(mode=0o700)
            try:
                _private_directory(directory, "unsafe_profile")
            except FileNotFoundError:
                if not allow_missing:
                    raise HostAccountError("profile_missing") from None
        except OSError:
            raise HostAccountError("unsafe_profile") from None
        return NativeAccountProfile(ref, directory)

    def _load(self) -> _State:
        try:
            _private_directory(self._root, "account_store_unavailable")
            fd = _private_fd(self._state_path, os.O_RDONLY, atomic_snapshot=True)
        except FileNotFoundError:
            return _State()
        except OSError:
            raise HostAccountError("account_store_unavailable") from None
        try:
            with os.fdopen(fd, "rb") as file:
                return _State.model_validate_json(file.read())
        except (OSError, ModelError):
            raise HostAccountError("account_store_unavailable") from None

    @contextmanager
    def _locked_state(self) -> Iterator[_State]:
        try:
            self._root.mkdir(mode=0o700, exist_ok=True)
            _private_directory(self._root, "account_store_unavailable")
            fd = _private_fd(self._root / ".lock", os.O_RDWR | os.O_CREAT)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield self._load()
            finally:
                os.close(fd)
        except OSError:
            raise HostAccountError("account_store_unavailable") from None

    def _save(self, state: _State) -> None:
        with tempfile.NamedTemporaryFile(dir=self._root, delete=False) as file:
            try:
                file.write(state.model_dump_json().encode())
                file.flush()
                os.fsync(file.fileno())
                os.replace(file.name, self._state_path)
                directory_fd = os.open(self._root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                with suppress(FileNotFoundError):
                    os.unlink(file.name)
