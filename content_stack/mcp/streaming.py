"""Progress-event helper for streaming MCP tools per audit M-21.

Some bulk tools stream interim progress to the caller, for example resource
imports, artifact ingestion, or connector calls that process many provider
objects.

The MCP SDK's lowlevel ``Server`` owns the wire transport; tool handlers
get a ``ServerSession`` reference via the ``request_ctx`` ContextVar set
by the dispatch layer. ``ProgressEmitter`` wraps that session reference
into a small, testable surface so tools call ``await emitter.emit(...)``
rather than reach into SDK internals.

The "progress token" used by the MCP progress protocol is the JSON-RPC
``request id`` of the originating call. The SDK stores it on
``request_ctx.meta.progressToken`` when the client opts in — if the
client did not opt in, the token is ``None`` and we silently no-op so
non-streaming clients still get a normal request/response.
"""

from __future__ import annotations

from typing import Any

from mcp.server.session import ServerSession


class ProgressEmitter:
    """Send ``progress`` notifications during a streaming tool call.

    The class is intentionally trivial: it owns a ``(session, token)``
    pair and exposes ``emit`` / ``done``. Both methods accept
    ``partial_data`` as a free-form dict — the SDK forwards it inside
    the notification's ``message`` field so clients that subscribe see
    incremental partial data.
    """

    def __init__(
        self,
        session: ServerSession | None,
        progress_token: str | int | None,
        request_id: str | int | None = None,
    ) -> None:
        """Capture the wire-level handles needed to emit progress events."""
        self._session = session
        self._token = progress_token
        self._request_id = request_id

    @property
    def is_active(self) -> bool:
        """Return ``True`` if both the session and the progress token are set.

        When ``False``, the emitter silently no-ops — the client did not
        request progress streaming, so the tool just returns its final
        result on the JSON-RPC response.
        """
        return self._session is not None and self._token is not None

    async def emit(
        self,
        step: int,
        total: int,
        message: str,
        partial_data: Any | None = None,
    ) -> None:
        """Emit one ``progress`` notification.

        Wraps ``ServerSession.send_progress_notification``. The SDK's
        notification carries ``progress``, ``total`` and ``message``;
        partial data piggybacks on ``message`` as a JSON suffix when set
        (the spec doesn't reserve a partial-data field). Non-active
        emitters silently no-op.
        """
        if not self.is_active or self._session is None or self._token is None:
            return
        if partial_data is not None:
            message = f"{message} | partial={partial_data}"
        await self._session.send_progress_notification(
            progress_token=self._token,
            progress=float(step),
            total=float(total) if total else None,
            message=message,
            related_request_id=str(self._request_id) if self._request_id is not None else None,
        )

    async def done(self, final_message: str = "complete") -> None:
        """Emit a final ``progress`` event marking 100 % completion.

        Convenience wrapper used by streaming tools to signal "no more
        progress events follow this response". The actual final response
        body still rides on the JSON-RPC reply; this notification is
        purely a UX hint for clients with a progress UI.
        """
        if not self.is_active or self._session is None or self._token is None:
            return
        # progress=total signals 100 %. We don't know ``total`` at this
        # call site, so we emit progress=1.0 and total=1.0 — clients
        # interpret ratio.
        await self._session.send_progress_notification(
            progress_token=self._token,
            progress=1.0,
            total=1.0,
            message=final_message,
            related_request_id=str(self._request_id) if self._request_id is not None else None,
        )


__all__ = ["ProgressEmitter"]
