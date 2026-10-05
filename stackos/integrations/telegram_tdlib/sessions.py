"""Host account registry and serialized native session replacement."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from stackos_connectors.connectors.telegram.tdlib.sessions import (
    TelegramTdlibSession,
    TelegramTdlibSessionConfig,
    TelegramTdlibSessionError,
    TelegramTdlibSessionReceipt,
    _TdlibClient,
)


@dataclass(frozen=True)
class TelegramTdlibSessionRegistration:
    """One account's active native session and safe bootstrap receipt."""

    account_ref: str
    session: TelegramTdlibSession = field(repr=False)
    receipt: TelegramTdlibSessionReceipt


class TelegramTdlibSessionRegistry:
    """Serialize one TDLib client lifecycle per daemon-owned account reference.

    The Account owner calls :meth:`configure` after it has acquired its own
    delivery/auth lease.  A changed account setup replaces the prior client
    only after that client has been closed; the registry deliberately contains
    no delivery queue, rate control, credential storage, or policy decisions.
    """

    def __init__(self, *, client_factory: Callable[[], _TdlibClient]) -> None:
        self._client_factory = client_factory
        self._sessions: dict[str, TelegramTdlibSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def configure(
        self,
        *,
        account_ref: str,
        config: TelegramTdlibSessionConfig,
        startup_timeout_seconds: float = 30.0,
    ) -> TelegramTdlibSessionRegistration:
        """Close and replace the account's one session with the supplied setup."""
        lock = self._lock_for(account_ref)
        async with lock:
            previous = self._sessions.pop(account_ref, None)
            if previous is not None:
                await previous.close()
            session = TelegramTdlibSession(client_factory=self._client_factory)
            try:
                receipt = await session.start(
                    config, startup_timeout_seconds=startup_timeout_seconds
                )
            except Exception:
                await session.close()
                raise
            self._sessions[account_ref] = session
            return TelegramTdlibSessionRegistration(
                account_ref=account_ref, session=session, receipt=receipt
            )

    async def get(self, *, account_ref: str) -> TelegramTdlibSession:
        """Return the current session for an account or a repairable absence error."""
        lock = self._lock_for(account_ref)
        async with lock:
            session = self._sessions.get(account_ref)
            if session is None:
                raise TelegramTdlibSessionError("TDLib session isn't configured for this account")
            return session

    async def close(self, *, account_ref: str, timeout_seconds: float = 10.0) -> None:
        """Retire an account's session, for auth revoke, shutdown, or reconfiguration."""
        lock = self._lock_for(account_ref)
        async with lock:
            session = self._sessions.pop(account_ref, None)
            if session is not None:
                await session.close(timeout_seconds=timeout_seconds)

    async def close_all(self, *, timeout_seconds: float = 10.0) -> None:
        """Retire all registered clients during daemon shutdown."""
        for account_ref in tuple(self._sessions):
            await self.close(account_ref=account_ref, timeout_seconds=timeout_seconds)

    def _lock_for(self, account_ref: str) -> asyncio.Lock:
        if not isinstance(account_ref, str) or not account_ref:
            raise TelegramTdlibSessionError("TDLib account reference is required")
        lock = self._locks.get(account_ref)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[account_ref] = lock
        return lock
