"""UI console token bootstrap endpoint — ``GET /api/v1/auth/ui-token``.

The Vue UI ships in the same origin as the daemon, but JavaScript in the
browser cannot read the token file at ``~/.local/state/stackos/auth.token``
directly (file system access is blocked). This endpoint is the bridge: the
SPA fetches a derived console bearer token at boot via a same-origin GET and
then attaches ``Authorization: Bearer <token>`` to subsequent REST requests.

**Whitelist note (security trade-off; documented in docs/security.md):**

This endpoint is added to ``WHITELIST_PREFIXES`` in
``stackos.auth`` so the unauthenticated browser can reach it. The
upstream defenders are:

1. ``HostHeaderMiddleware`` rejects any request whose ``Host:`` header is
   not loopback (``localhost``, ``127.0.0.1``, ``[::1]``) with 421 — so a
   cross-origin browser fetch with a forged Host header is blocked.
2. ``CORSMiddleware`` is configured ``same-origin`` only — a different
   site cannot read the response from JavaScript even if it triggered the
   request.
3. The daemon binds to ``127.0.0.1`` only (the CLI rejects ``--host
   0.0.0.0``), so off-machine callers can't reach the port at all.

The residual exposure is *another local process on the same machine* that
can connect to ``127.0.0.1:5180`` — those processes can fetch the token
just by GETting this endpoint. This is a regression from the file-mode-0600
posture in that narrow case, accepted as the cost of admitting a browser
client. The token returned here is REST-only and limited to reads, read-only
operation calls, and provider-auth setup writes; the disk-backed daemon token
remains the agent/MCP token for all other mutations.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class UiTokenResponse(BaseModel):
    """Wire shape for ``GET /api/v1/auth/ui-token``."""

    token: str


@router.get("/ui-token", response_model=UiTokenResponse)
async def get_ui_token(request: Request) -> UiTokenResponse:
    """Return the browser's console bearer token to the same-origin Vue UI.

    The token is derived once at app boot in ``server.create_app`` and stored
    on ``request.app.state.ui_token``; we just hand it back. No I/O,
    no allocation, no logging of the token value.
    """
    token: str = request.app.state.ui_token
    return UiTokenResponse(token=token)
