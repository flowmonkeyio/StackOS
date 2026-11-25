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

from typing import Annotated, Any, Literal, cast
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
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


CallbackPurpose = Literal["signup", "recovery", "unknown"]
CallbackOutcome = Literal[
    "pending", "success", "expired_or_used", "invalid", "retry", "sign_in_required"
]


def _callback_result(purpose: CallbackPurpose, outcome: CallbackOutcome) -> dict:
    """Safe presentation facts; the shared client owns rendering and app return."""
    if outcome == "sign_in_required":
        action = "sign_in"
    elif outcome == "success":
        action = "set_password" if purpose == "recovery" else "continue"
    elif outcome == "pending":
        action = "continue_reset" if purpose == "recovery" else "confirm_email"
    elif outcome == "retry":
        action = "retry_verification"
    else:
        action = {
            "signup": "resend_verification",
            "recovery": "request_recovery",
            "unknown": "sign_in",
        }[purpose]
    return {
        "purpose": purpose,
        "outcome": outcome,
        "next_action": action,
        "retryable": outcome == "retry",
    }


def _callback_redirect(
    purpose: CallbackPurpose,
    outcome: CallbackOutcome,
    token: str = "",
) -> RedirectResponse:
    query = {"auth": "callback", "purpose": purpose, "outcome": outcome}
    if token and outcome in {"pending", "retry"}:
        query["token_hash"] = token
    # No caller-controlled destination or credential handoff to another app.
    return RedirectResponse(
        "/sign-in?" + urlencode(query),
        status_code=303,
        headers={"Cache-Control": "no-store", "Content-Security-Policy": "frame-ancestors 'none'"},
    )


def _callback_error(
    exc: HTTPException, purpose: CallbackPurpose, *, verification_completed: bool
) -> dict:
    code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
    outcome: CallbackOutcome = "invalid"
    if verification_completed and purpose == "signup":
        # Email confirmation succeeded; another confirmation email is not
        # needed just because this browser session could not be established.
        outcome = "sign_in_required"
    elif (
        verification_completed
        or exc.status_code == 503
        or code == "verification_expired_or_invalid"
    ):
        # GoTrue's otp_expired covers expiration and replay; do not invent a
        # separate token ledger or probe accounts to guess which happened.
        # A completed exchange consumes the link even if local session creation
        # fails. Provider unavailability can also hide a completed exchange.
        outcome = "expired_or_used"
    elif exc.status_code in {409, 429}:
        outcome = "retry"
    return _callback_result(purpose, outcome)


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
    verification_completed = False
    try:
        if not 1 <= len(body.token_hash.get_secret_value()) <= 1024:
            raise HTTPException(400, {"code": "verification_invalid"})
        tokens = await auth.call(
            "POST",
            "verify",
            payload={
                "token_hash": body.token_hash.get_secret_value(),
                "type": body.type,
            },
        )
        verification_completed = True
        result = await auth.create(
            tokens,
            response,
            recovery=body.type == "recovery",
            previous_cookie=request.cookies.get(session_cookie_name(auth.settings)),
        )
    except HTTPException as exc:
        detail: dict[str, Any] = (
            dict(exc.detail) if isinstance(exc.detail, dict) else {"code": "verification_invalid"}
        )
        detail["callback"] = _callback_error(
            exc, body.type, verification_completed=verification_completed
        )
        detail["retryable"] = detail["callback"]["retryable"]
        raise HTTPException(exc.status_code, detail) from exc
    return {**result, "callback": _callback_result(body.type, "success")}


@router.get("/callback", response_class=RedirectResponse, status_code=303)
async def verification_callback(request: Request, auth: Auth) -> RedirectResponse:
    # A scanner may follow this redirect and load the shared UI, but GET never
    # exchanges the one-time code, creates a session or performs a password reset.
    token = request.query_params.get("token_hash", "")
    kind = request.query_params.get("type", "")
    if kind not in {"signup", "recovery"}:
        return _callback_redirect("unknown", "invalid")
    purpose: Literal["signup", "recovery"] = "signup" if kind == "signup" else "recovery"
    if (
        not token
        or len(token) > 1024
        or len(request.query_params.getlist("type")) != 1
        or len(request.query_params.getlist("token_hash")) != 1
    ):
        return _callback_redirect(purpose, "invalid")
    return _callback_redirect(purpose, "pending", token)


@router.post("/callback")
async def consume_callback(request: Request, auth: Auth) -> Response:
    check_server_origin(request, auth.settings, required=True)
    raw = await request.body()
    if len(raw) > 4096:
        return _callback_redirect("unknown", "invalid")
    values = parse_qs(raw.decode("utf-8", errors="replace"))
    token, kind = values.get("token_hash", [""])[0], values.get("type", [""])[0]
    if kind not in {"signup", "recovery"}:
        return _callback_redirect("unknown", "invalid")
    purpose: Literal["signup", "recovery"] = "signup" if kind == "signup" else "recovery"
    if (
        not token
        or len(token) > 1024
        or len(values.get("token_hash", [])) != 1
        or len(values.get("type", [])) != 1
    ):
        return _callback_redirect(purpose, "invalid")
    response = _callback_redirect(purpose, "success")
    try:
        await verify(
            VerificationInput(token_hash=SecretStr(token), type=purpose), request, response, auth
        )
    except HTTPException as exc:
        # Preserve the producer's exchange-phase classification, rather than
        # inferring retryability again from the local failure's HTTP status.
        result = cast(dict[str, Any], exc.detail)["callback"]
        return _callback_redirect(purpose, result["outcome"], token)
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
    await auth.renew(row)
    return {"status": "authenticated", "csrf_token": auth.tokens(row)["csrf"]}


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
