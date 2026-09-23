"""Daemon-owned TDLib session coordination and ordered update dispatch.

This private integration service owns native client lifetime only.  The Account
repository persists authorization state through an injected sink, and the
delivery owner persists final message receipts through its own injected sink.
Neither credentials nor account policy are stored here.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from stackos.integrations.telegram_tdlib.native import TelegramTdlibClosedError
from stackos.integrations.telegram_tdlib.sessions import (
    TelegramTdlibSession,
    TelegramTdlibSessionConfig,
    TelegramTdlibSessionReceipt,
    TelegramTdlibSessionRegistration,
)

AuthorizationSink = Callable[[str, int, dict[str, Any]], Awaitable[None]]
AuthorizationSettledSink = Callable[[str, int, dict[str, Any]], Awaitable[None]]
MessageReceiptSink = Callable[[str, int, dict[str, Any]], Awaitable[None]]
IngressSink = Callable[[str, int, dict[str, Any]], Awaitable[None]]


class TelegramTdlibServiceError(RuntimeError):
    """The daemon-local TDLib service cannot satisfy an internal request."""


class TelegramTdlibMessageReceiptTimeout(TelegramTdlibServiceError):
    """A terminal send outcome wasn't observed; durable delivery must treat it as unknown."""

    outcome_unknown = True


class _TdlibSession(Protocol):
    async def next_update(self) -> dict[str, Any]: ...

    async def _request(
        self,
        request: Mapping[str, Any],
        *,
        timeout_seconds: float = 30.0,
        correlation_id: str | None = None,
    ) -> dict[str, Any]: ...

    async def close(self, *, timeout_seconds: float = 10.0) -> None: ...


class _TdlibSessionRegistry(Protocol):
    async def configure(
        self,
        *,
        account_ref: str,
        config: TelegramTdlibSessionConfig,
        startup_timeout_seconds: float = 30.0,
    ) -> TelegramTdlibSessionRegistration: ...

    async def get(self, *, account_ref: str) -> TelegramTdlibSession: ...

    async def close(self, *, account_ref: str, timeout_seconds: float = 10.0) -> None: ...

    async def close_all(self, *, timeout_seconds: float = 10.0) -> None: ...


async def _ignore_authorization(
    _account_ref: str, _generation: int, _state: dict[str, Any]
) -> None:
    return None


async def _ignore_message_receipt(
    _account_ref: str, _generation: int, _update: dict[str, Any]
) -> None:
    return None


async def _ignore_ingress(_account_ref: str, _generation: int, _update: dict[str, Any]) -> None:
    return None


@dataclass
class _RuntimeSession:
    account_ref: str
    generation: int
    session: _TdlibSession
    files_directory: Path
    bootstrap_receipt: TelegramTdlibSessionReceipt
    state_changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    authorization_state: dict[str, Any] | None = None
    authorization_revision: int = 0
    receiver: asyncio.Task[None] | None = None
    failure: TelegramTdlibServiceError | None = None
    retired: bool = False
    message_receipts: OrderedDict[tuple[int, int], dict[str, Any]] = field(
        default_factory=OrderedDict
    )
    message_waiters: dict[tuple[int, int], list[asyncio.Future[dict[str, Any]]]] = field(
        default_factory=dict
    )


class TelegramTdlibService:
    """Keep exactly one ordered TDLib update consumer per configured Account.

    The daemon constructs this service with a managed client registry and
    callbacks that acquire a fresh database session.  Generation fencing makes
    a late task from a retired or reconfigured Account unable to write a newer
    Account's authorization or delivery state.
    """

    def __init__(
        self,
        *,
        session_registry: _TdlibSessionRegistry,
        authorization_sink: AuthorizationSink = _ignore_authorization,
        authorization_settled_sink: AuthorizationSettledSink = _ignore_authorization,
        message_receipt_sink: MessageReceiptSink = _ignore_message_receipt,
        ingress_sink: IngressSink = _ignore_ingress,
    ) -> None:
        self._registry = session_registry
        self._authorization_sink = authorization_sink
        self._authorization_settled_sink = authorization_settled_sink
        self._message_receipt_sink = message_receipt_sink
        self._ingress_sink = ingress_sink
        self._sessions: dict[str, _RuntimeSession] = {}
        self._configuring: dict[tuple[str, int], int] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._account_transition_locks: dict[str, asyncio.Lock] = {}
        self._account_transition_owners: dict[str, asyncio.Task[Any]] = {}
        self._retirement_tasks: set[asyncio.Task[None]] = set()

    @asynccontextmanager
    async def account_transition(self, account_ref: str):
        """Hold one Account's native lifetime while a repository transition runs.

        Repository operations may call configure/close inside this lease. Those
        methods take the same lease for their registry mutation, while normal
        concurrent connects still join one in-flight bootstrap after that
        brief registry transition.
        """
        if not isinstance(account_ref, str) or not account_ref:
            raise TelegramTdlibServiceError("TDLib account reference is required")
        task = asyncio.current_task()
        if task is None:
            raise TelegramTdlibServiceError("TDLib transition requires an async task")
        if self._account_transition_owners.get(account_ref) is task:
            yield
            return
        lock = self._account_transition_locks.setdefault(account_ref, asyncio.Lock())
        async with lock:
            self._account_transition_owners[account_ref] = task
            try:
                yield
            finally:
                self._account_transition_owners.pop(account_ref, None)

    def active_generation(self, *, account_ref: str) -> int | None:
        """Report the native generation while its Account transition is held."""
        entry = self._sessions.get(account_ref)
        return entry.generation if entry is not None and not entry.retired else None

    async def configure(
        self,
        *,
        account_ref: str,
        generation: int,
        config: TelegramTdlibSessionConfig,
        startup_timeout_seconds: float = 30.0,
        authorization_timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Start one account session and await its first post-bootstrap auth state."""
        self._validate_identity(account_ref=account_ref, generation=generation)
        if authorization_timeout_seconds <= 0:
            raise ValueError("authorization_timeout_seconds must be positive")
        key = (account_ref, generation)
        self._configuring[key] = self._configuring.get(key, 0) + 1
        try:
            async with self.account_transition(account_ref), self._lifecycle_lock:
                previous = self._sessions.get(account_ref)
                if (
                    previous is not None
                    and previous.generation == generation
                    and not previous.retired
                ):
                    entry = previous
                else:
                    entry = None
                if previous is not None and entry is None:
                    await self._retire(previous)
                if entry is None:
                    registration = await self._registry.configure(
                        account_ref=account_ref,
                        config=config,
                        startup_timeout_seconds=startup_timeout_seconds,
                    )
                    entry = _RuntimeSession(
                        account_ref=account_ref,
                        generation=generation,
                        session=registration.session,
                        files_directory=config.files_directory,
                        bootstrap_receipt=registration.receipt,
                    )
                    self._sessions[account_ref] = entry
                    entry.receiver = asyncio.create_task(
                        self._receive_updates(entry),
                        name=f"telegram-tdlib-updates-{account_ref}",
                    )
            try:
                return await self._await_authorization_state(
                    entry,
                    after_revision=0,
                    timeout_seconds=authorization_timeout_seconds,
                )
            except Exception:
                await self.close(account_ref=account_ref, generation=generation)
                raise
        finally:
            remaining = self._configuring[key] - 1
            if remaining:
                self._configuring[key] = remaining
            else:
                self._configuring.pop(key, None)

    def is_connecting(self, *, account_ref: str, generation: int) -> bool:
        """Report an in-flight bootstrap so duplicate connects can join it."""
        return self._configuring.get((account_ref, generation), 0) > 0

    async def request(
        self,
        account_ref: str,
        request: Mapping[str, Any],
        *,
        generation: int | None = None,
        correlation_id: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Send one typed internal TDLib request through the current session."""
        entry = self._current(account_ref=account_ref, generation=generation)
        kwargs: dict[str, Any] = {}
        if timeout_seconds != 30.0:
            kwargs["timeout_seconds"] = timeout_seconds
        if correlation_id is not None:
            kwargs["correlation_id"] = correlation_id
        return await entry.session._request(request, **kwargs)

    async def request_authorization(
        self,
        *,
        account_ref: str,
        generation: int,
        request: Mapping[str, Any],
        correlation_id: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Run one typed auth request and await the subsequent auth-state update."""
        entry = self._current(account_ref=account_ref, generation=generation)
        revision = entry.authorization_revision
        await self.request(
            account_ref,
            request,
            generation=generation,
            correlation_id=correlation_id,
            timeout_seconds=timeout_seconds,
        )
        return await self._await_authorization_state(
            entry,
            after_revision=revision,
            timeout_seconds=timeout_seconds,
        )

    async def wait_message(
        self,
        account_ref: str,
        chat_id: int,
        temporary_message_id: int,
        *,
        generation: int | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Await one terminal send update without resending on a timeout.

        The delivery owner persists unknown outcomes and decides recovery.  This
        method only joins the ordered receiver's receipt cache, so an update
        that arrived before the caller starts waiting isn't lost.
        """
        entry = self._current(account_ref=account_ref, generation=generation)
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        key = self._message_key(chat_id=chat_id, temporary_message_id=temporary_message_id)
        cached = entry.message_receipts.pop(key, None)
        if cached is not None:
            return dict(cached)
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
        entry.message_waiters.setdefault(key, []).append(waiter)
        try:
            async with asyncio.timeout(timeout_seconds):
                return dict(await waiter)
        except TimeoutError as exc:
            raise TelegramTdlibMessageReceiptTimeout(
                "TDLib final send receipt timed out; delivery outcome is unknown"
            ) from exc
        finally:
            pending = entry.message_waiters.get(key)
            if pending is not None:
                entry.message_waiters[key] = [item for item in pending if item is not waiter]
                if not entry.message_waiters[key]:
                    entry.message_waiters.pop(key, None)

    def state(self, *, account_ref: str, generation: int) -> dict[str, Any] | None:
        """Return the daemon-internal current authorization state for this generation."""
        entry = self._current(account_ref=account_ref, generation=generation)
        return dict(entry.authorization_state) if entry.authorization_state is not None else None

    def files_directory(self, account_ref: str, *, generation: int | None = None) -> Path:
        """Return the configured private TDLib files root for a daemon adapter."""
        return self._current(account_ref=account_ref, generation=generation).files_directory

    def bootstrap_receipt(
        self, *, account_ref: str, generation: int
    ) -> TelegramTdlibSessionReceipt:
        """Return the safe proxy bootstrap receipt for Account persistence only."""
        return self._current(account_ref=account_ref, generation=generation).bootstrap_receipt

    async def session(self, *, account_ref: str, generation: int) -> TelegramTdlibSession:
        """Return the current private session for typed daemon adapters only."""
        self._current(account_ref=account_ref, generation=generation)
        return await self._registry.get(account_ref=account_ref)

    async def close(
        self,
        *,
        account_ref: str,
        generation: int,
        timeout_seconds: float = 10.0,
    ) -> None:
        """Retire one exact generation without allowing late callback writes."""
        self._validate_identity(account_ref=account_ref, generation=generation)
        async with self.account_transition(account_ref), self._lifecycle_lock:
            entry = self._sessions.get(account_ref)
            if entry is None:
                return
            if entry.generation != generation:
                raise TelegramTdlibServiceError("TDLib session generation is stale")
            await self._retire(entry, timeout_seconds=timeout_seconds)

    async def close_all(self, *, timeout_seconds: float = 10.0) -> None:
        """Retire all managed sessions during daemon shutdown."""
        async with self._lifecycle_lock:
            for entry in tuple(self._sessions.values()):
                await self._retire(entry, timeout_seconds=timeout_seconds)
        if self._retirement_tasks:
            await asyncio.gather(*tuple(self._retirement_tasks), return_exceptions=True)

    async def _receive_updates(self, entry: _RuntimeSession) -> None:
        try:
            while self._is_current(entry):
                update = await entry.session.next_update()
                if not self._is_current(entry):
                    return
                update_type = update.get("@type")
                if update_type == "updateAuthorizationState":
                    state = update.get("authorization_state")
                    if not isinstance(state, Mapping) or not isinstance(state.get("@type"), str):
                        raise TelegramTdlibServiceError(
                            "TDLib returned an invalid authorization state"
                        )
                    await self._record_authorization_state(entry, dict(state))
                elif update_type in {"updateMessageSendSucceeded", "updateMessageSendFailed"}:
                    await self._dispatch(entry, self._message_receipt_sink, update)
                    self._record_message_receipt(entry, update)
                else:
                    await self._dispatch(entry, self._ingress_sink, update)
        except asyncio.CancelledError:
            raise
        except TelegramTdlibClosedError:
            if self._is_current(entry):
                await self._fail(entry, "TDLib session closed before its lifecycle was retired")
        except Exception:
            if self._is_current(entry):
                await self._fail(entry, "TDLib update dispatch failed")

    async def _record_authorization_state(
        self, entry: _RuntimeSession, state: dict[str, Any]
    ) -> None:
        if not self._is_current(entry):
            return
        await self._authorization_sink(entry.account_ref, entry.generation, dict(state))
        if not self._is_current(entry):
            return
        async with entry.state_changed:
            entry.authorization_state = dict(state)
            entry.authorization_revision += 1
            entry.state_changed.notify_all()
        if self._is_current(entry):
            await self._authorization_settled_sink(entry.account_ref, entry.generation, dict(state))

    async def _dispatch(
        self,
        entry: _RuntimeSession,
        sink: MessageReceiptSink | IngressSink,
        update: dict[str, Any],
    ) -> None:
        if self._is_current(entry):
            await sink(entry.account_ref, entry.generation, dict(update))

    async def _await_authorization_state(
        self,
        entry: _RuntimeSession,
        *,
        after_revision: int,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        try:
            async with asyncio.timeout(timeout_seconds):
                async with entry.state_changed:
                    await entry.state_changed.wait_for(
                        lambda: (
                            entry.failure is not None
                            or entry.retired
                            or entry.authorization_revision > after_revision
                        )
                    )
                    if entry.failure is not None:
                        raise entry.failure
                    if entry.authorization_revision > after_revision:
                        assert entry.authorization_state is not None
                        return dict(entry.authorization_state)
                    raise TelegramTdlibServiceError("TDLib session was retired")
        except TimeoutError as exc:
            raise TelegramTdlibServiceError("TDLib authorization state timed out") from exc

    async def _fail(self, entry: _RuntimeSession, message: str) -> None:
        async with entry.state_changed:
            entry.failure = TelegramTdlibServiceError(message)
            entry.state_changed.notify_all()
        self._fail_message_waiters(entry, entry.failure)

    async def _retire(self, entry: _RuntimeSession, *, timeout_seconds: float = 10.0) -> None:
        entry.retired = True
        if self._sessions.get(entry.account_ref) is entry:
            self._sessions.pop(entry.account_ref, None)
        async with entry.state_changed:
            entry.state_changed.notify_all()
        self._fail_message_waiters(entry, TelegramTdlibServiceError("TDLib session was retired"))
        receiver = entry.receiver
        if receiver is asyncio.current_task():
            # An authorization sink can reject a just-Ready identity and close
            # its account.  That sink runs in this receiver task, so awaiting
            # cancellation/joining it here would self-await.  The entry is
            # already retired and fenced; finish native close independently.
            retirement = asyncio.create_task(
                self._close_registry_later(entry.account_ref, timeout_seconds=timeout_seconds),
                name=f"telegram-tdlib-close-{entry.account_ref}",
            )
            self._retirement_tasks.add(retirement)
            retirement.add_done_callback(self._retirement_tasks.discard)
            return
        if receiver is not None:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
        await self._close_registry(entry.account_ref, timeout_seconds=timeout_seconds)

    async def _close_registry(self, account_ref: str, *, timeout_seconds: float) -> None:
        await self._registry.close(account_ref=account_ref, timeout_seconds=timeout_seconds)

    async def _close_registry_later(self, account_ref: str, *, timeout_seconds: float) -> None:
        try:
            await self._close_registry(account_ref, timeout_seconds=timeout_seconds)
        except Exception:
            # Registry retirement cannot restore a fenced entry. The public
            # close caller already receives a retired session state and daemon
            # shutdown will make one bounded final close attempt.
            return

    def _current(self, *, account_ref: str, generation: int | None) -> _RuntimeSession:
        if generation is not None:
            self._validate_identity(account_ref=account_ref, generation=generation)
        elif not isinstance(account_ref, str) or not account_ref:
            raise TelegramTdlibServiceError("TDLib account reference is required")
        entry = self._sessions.get(account_ref)
        if entry is None:
            raise TelegramTdlibServiceError("TDLib session is not configured")
        if (generation is not None and entry.generation != generation) or entry.retired:
            raise TelegramTdlibServiceError("TDLib session generation is stale")
        if entry.failure is not None:
            raise entry.failure
        return entry

    def _is_current(self, entry: _RuntimeSession) -> bool:
        return not entry.retired and self._sessions.get(entry.account_ref) is entry

    def _record_message_receipt(self, entry: _RuntimeSession, update: Mapping[str, Any]) -> None:
        key = self._receipt_key(update)
        if key is None or not self._is_current(entry):
            return
        receipt = dict(update)
        waiters = entry.message_waiters.pop(key, [])
        if waiters:
            for waiter in waiters:
                if not waiter.done():
                    waiter.set_result(receipt)
            return
        entry.message_receipts[key] = receipt
        entry.message_receipts.move_to_end(key)
        while len(entry.message_receipts) > 256:
            entry.message_receipts.popitem(last=False)

    @staticmethod
    def _receipt_key(update: Mapping[str, Any]) -> tuple[int, int] | None:
        old_message_id = update.get("old_message_id")
        message = update.get("message")
        if (
            not isinstance(old_message_id, int)
            or isinstance(old_message_id, bool)
            or not isinstance(message, Mapping)
        ):
            return None
        chat_id = message.get("chat_id")
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            return None
        return chat_id, old_message_id

    @staticmethod
    def _message_key(*, chat_id: int, temporary_message_id: int) -> tuple[int, int]:
        for label, value in (("chat id", chat_id), ("temporary message id", temporary_message_id)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise TelegramTdlibServiceError(f"TDLib {label} must be an integer")
        return chat_id, temporary_message_id

    @staticmethod
    def _fail_message_waiters(entry: _RuntimeSession, error: TelegramTdlibServiceError) -> None:
        for waiters in entry.message_waiters.values():
            for waiter in waiters:
                if not waiter.done():
                    waiter.set_exception(error)
        entry.message_waiters.clear()

    @staticmethod
    def _validate_identity(*, account_ref: str, generation: int) -> None:
        if not isinstance(account_ref, str) or not account_ref:
            raise TelegramTdlibServiceError("TDLib account reference is required")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation <= 0:
            raise TelegramTdlibServiceError("TDLib session generation must be a positive integer")


__all__ = [
    "TelegramTdlibMessageReceiptTimeout",
    "TelegramTdlibService",
    "TelegramTdlibServiceError",
]
