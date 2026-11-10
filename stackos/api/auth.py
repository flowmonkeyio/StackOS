"""User auth endpoints and standalone UI console token bootstrap.

Selected servers use the registration/session routes below and reject the
standalone bootstrap. The remaining bootstrap description applies to local mode.

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

from html import escape
from typing import Annotated, Literal
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import delete
from sqlmodel import Session, col, select

from stackos.api.deps import get_session, get_settings
from stackos.auth import ServerAuth, check_server_origin, session_cookie_name
from stackos.config import Settings
from stackos.db.model_auth import UserSession

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
    if request.app.state.settings.process_role == "server":
        raise HTTPException(401, {"code": "sign_in_required"})
    token: str = request.app.state.ui_token
    return UiTokenResponse(token=token)


def get_server_auth(
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
) -> ServerAuth:
    return ServerAuth(settings, session)


Auth = Annotated[ServerAuth, Depends(get_server_auth)]


class EmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)


class LoginInput(EmailInput):
    password: SecretStr


class VerificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token_hash: SecretStr
    type: Literal["signup", "recovery"]


class PasswordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr


def _password(value: SecretStr) -> str:
    password = value.get_secret_value()
    if not 8 <= len(password) <= 1024:
        raise HTTPException(400, {"code": "password_length", "minimum": 8, "maximum": 1024})
    return password


@router.post("/register")
async def register(body: LoginInput, auth: Auth) -> dict:
    await auth.call(
        "POST", "signup", payload={"email": body.email, "password": _password(body.password)}
    )
    # Signup never adopts existing StackOS records and never silently signs in,
    # including when an incorrectly configured provider auto-confirms email.
    return {"status": "verification_pending"}


@router.post("/login")
async def login(body: LoginInput, request: Request, response: Response, auth: Auth) -> dict:
    tokens = await auth.call(
        "POST",
        "token?grant_type=password",
        payload={
            "email": body.email,
            "password": _password(body.password),
        },
    )
    return await auth.create(
        tokens, response, previous_cookie=request.cookies.get(session_cookie_name(auth.settings))
    )


@router.post("/resend")
async def resend(body: EmailInput, auth: Auth) -> dict:
    await auth.call("POST", "resend", payload={"email": body.email, "type": "signup"})
    return {"status": "verification_pending"}


@router.post("/recover")
async def recover(body: EmailInput, auth: Auth) -> dict:
    await auth.call("POST", "recover", payload={"email": body.email})
    return {"status": "recovery_email_sent"}


@router.post("/verify")
async def verify(body: VerificationInput, request: Request, response: Response, auth: Auth) -> dict:
    tokens = await auth.call(
        "POST",
        "verify",
        payload={
            "token_hash": body.token_hash.get_secret_value(),
            "type": body.type,
        },
    )
    return await auth.create(
        tokens,
        response,
        recovery=body.type == "recovery",
        previous_cookie=request.cookies.get(session_cookie_name(auth.settings)),
    )


@router.get("/callback", response_class=HTMLResponse)
async def verification_callback(request: Request, auth: Auth) -> HTMLResponse:
    # Email links only display a confirmation form. A scanner or cross-origin
    # navigation cannot consume the one-time code or replace a browser login.
    token = request.query_params.get("token_hash", "")
    kind = request.query_params.get("type", "")
    if not token or len(token) > 1024 or kind not in {"signup", "recovery"}:
        raise HTTPException(400, {"code": "verification_invalid"})
    return HTMLResponse(
        "<!doctype html><html><head><title>Confirm email</title></head><body>"
        "<h1>Confirm your StackOS email</h1>"
        '<form method="post" action="/api/v1/auth/callback">'
        f'<input type="hidden" name="token_hash" value="{escape(token, quote=True)}">'
        f'<input type="hidden" name="type" value="{escape(kind, quote=True)}">'
        '<button type="submit">Confirm email</button></form></body></html>',
        headers={
            "Content-Security-Policy": (
                "default-src 'none'; form-action 'self'; frame-ancestors 'none'"
            )
        },
    )


@router.post("/callback")
async def consume_callback(request: Request, auth: Auth) -> Response:
    check_server_origin(request, auth.settings, required=True)
    raw = await request.body()
    if len(raw) > 4096:
        raise HTTPException(400, {"code": "verification_invalid"})
    values = parse_qs(raw.decode("utf-8", errors="replace"))
    token, kind = values.get("token_hash", [""])[0], values.get("type", [""])[0]
    if not token or len(token) > 1024 or kind not in {"signup", "recovery"}:
        raise HTTPException(400, {"code": "verification_invalid"})
    response = RedirectResponse(
        f"/?auth={'recovery' if kind == 'recovery' else 'verified'}", status_code=303
    )
    tokens = await auth.call("POST", "verify", payload={"token_hash": token, "type": kind})
    await auth.create(
        tokens,
        response,
        recovery=kind == "recovery",
        previous_cookie=request.cookies.get(session_cookie_name(auth.settings)),
    )
    return response


@router.get("/session")
async def user_session(request: Request, auth: Auth) -> dict:
    row = auth.resolve(request)
    return {
        "status": "recovery_required" if row.recovery else "authenticated",
        "user_id": row.user_id,
        "environment_id": row.environment_id,
        "csrf_token": auth.tokens(row)["csrf"],
    }


@router.post("/refresh")
async def refresh(request: Request, auth: Auth) -> dict:
    row = auth.resolve(request, lock=True)
    auth.csrf(request, row)
    saved = auth.tokens(row)
    tokens = await auth.call(
        "POST", "token?grant_type=refresh_token", payload={"refresh_token": saved["refresh_token"]}
    )
    principal = await auth.principal(tokens.get("access_token", ""))
    if principal.user_id != row.user_id:
        raise HTTPException(401, {"code": "session_invalid"})
    auth.save_tokens(
        row,
        {
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "csrf": saved["csrf"],
        },
    )
    return {"status": "authenticated", "csrf_token": saved["csrf"]}


@router.post("/logout")
async def logout(request: Request, response: Response, auth: Auth) -> dict:
    row = auth.resolve(request, lock=True)
    auth.csrf(request, row)
    saved = auth.tokens(row)
    auth.session.delete(row)
    auth.session.commit()
    response.delete_cookie(
        session_cookie_name(auth.settings), path="/", httponly=True, samesite="strict"
    )
    remote_revoked = True
    try:
        await auth.call("POST", "logout?scope=local", access_token=saved["access_token"])
    except HTTPException:
        remote_revoked = False
    return {"status": "signed_out", "provider_revoked": remote_revoked}


@router.post("/password")
async def reset_password(
    body: PasswordInput, request: Request, response: Response, auth: Auth
) -> dict:
    row = auth.resolve(request, lock=True)
    auth.csrf(request, row)
    if not row.recovery:
        raise HTTPException(403, {"code": "recovery_required"})
    auth.lock_rows(
        select(UserSession).where(
            UserSession.user_id == row.user_id,
            UserSession.environment_id == row.environment_id,
        )
    )
    await auth.call(
        "PUT",
        "user",
        payload={"password": _password(body.password)},
        access_token=auth.tokens(row)["access_token"],
    )
    auth.session.exec(
        delete(UserSession).where(
            col(UserSession.user_id) == row.user_id,
            col(UserSession.environment_id) == row.environment_id,
        )
    )
    auth.session.commit()
    response.delete_cookie(
        session_cookie_name(auth.settings), path="/", httponly=True, samesite="strict"
    )
    return {"status": "password_reset_sign_in_required"}
