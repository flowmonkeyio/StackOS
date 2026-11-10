"""Standalone bearer authentication and selected-server user sessions.

Standalone uses a token at `state_dir/auth.token`, generated on first start.
Selected servers use provider-verified, environment-bound opaque sessions and
never accept standalone bootstrap/admin authority. Public health probes need
neither credential.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, col, select
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from stackos.config import Settings
from stackos.crypto import CryptoError, decrypt, encrypt, load_seed
from stackos.db.model_auth import UserSession

# Bearer-token enforcement is scoped to API surfaces only. The browser must
# be able to load the static UI bundle (`/`, `/assets/*`, `/favicon.ico`)
# *without* a token — the HTML/JS shell is harmless on its own; subsequent
# `/api/v1/*` calls from the loaded UI carry the token via fetch headers
# (token plumbing per D5; full UI flow lands in M2/M3). OpenAPI/docs paths
# (`/api/openapi.json`, `/api/docs`) are also public for local-dev ergonomics
# — they expose schema only, never grant access.
PROTECTED_PREFIXES: tuple[str, ...] = ("/api/v1", "/mcp")

# Inside the protected surfaces, these paths bypass the bearer-token check.
# Each entry is justified below; do NOT add to this list without an explicit
# threat-model note and same-origin/Host-check coverage.
#
# - ``/api/v1/health``: ``doctor`` probes liveness *before* it has resolved
#   the token (e.g., when diagnosing token-related failures themselves).
# - ``/api/v1/auth/ui-token``: the same-origin Vue UI cannot read the token
#   file from the browser, so it fetches a derived console token at boot via
#   this endpoint. The HostHeaderMiddleware (loopback-only) and CORSMiddleware
#   (same-origin) form the upstream guard. Trade-off documented in
#   ``docs/security.md`` and in ``stackos/api/auth.py``.
# - ``/api/v1/ingress/slack``: Slack Events API and Interactivity requests
#   cannot carry the daemon bearer token. The route verifies Slack's
#   `X-Slack-Signature` HMAC against the encrypted Slack signing secret and
#   checks the replay-window timestamp before it writes communication resources
#   or agent requests. HostHeaderMiddleware permits tunnel/deployed Host headers
#   only on provider ingress paths; every non-ingress API path remains
#   loopback-host guarded.
# - ``/api/v1/ingress/hubspot``: HubSpot webhooks and custom workflow-action
#   requests cannot carry the daemon token. The route validates HubSpot's v3
#   timestamped HMAC or documented workflow-action v2 signature against the
#   encrypted OAuth app secret before writing events or agent requests.
WHITELIST_PREFIXES: tuple[str, ...] = (
    "/api/v1/health",
    "/api/v1/auth/ui-token",
    "/api/v1/ingress/slack",
    "/api/v1/ingress/hubspot",
)
_OAUTH_CALLBACK_PATH = "/api/v1/auth/oauth/callback"

_TOKEN_BYTES = 32
_REQUIRED_MODE = 0o600
_UI_TOKEN_MESSAGE = b"stackos-ui-console-v1"
_UI_SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})


class TokenFileError(RuntimeError):
    """Raised when the auth token file exists but has the wrong permissions.

    We refuse to start the daemon rather than silently fix the mode — a
    too-wide token file is a "wrong machine, wrong user, or wrong umask"
    signal worth surfacing to the operator.
    """


def _file_mode_bits(path: Path) -> int:
    """Return the permission bits of `path`, masked to standard rwx triplets."""
    return stat.S_IMODE(path.stat().st_mode)


def ensure_token(token_path: Path) -> str:
    """Return the bearer token at `token_path`, generating it if absent.

    On generation: 32 bytes from `os.urandom`, urlsafe-base64 encoded, written
    with mode 0600 via `os.open(O_CREAT|O_EXCL, 0o600)` so we never race a
    co-tenant into reading a freshly-created world-readable file.

    On existing file: refuse if the mode is anything other than 0600 — the
    operator has likely run a `chmod` or restored from a sloppy backup; the
    daemon must not paper over that.
    """
    if token_path.exists():
        mode = _file_mode_bits(token_path)
        if mode != _REQUIRED_MODE:
            raise TokenFileError(
                f"auth token at {token_path} has mode {oct(mode)}; expected {oct(_REQUIRED_MODE)}"
            )
        return token_path.read_text(encoding="utf-8").strip()

    # Atomic create-and-restrict; defends against TOCTOU.
    token_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        token_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        _REQUIRED_MODE,
    )
    try:
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        os.write(fd, token.encode("utf-8"))
    finally:
        os.close(fd)
    # `os.open` honours umask on some platforms — re-chmod to be sure.
    os.chmod(token_path, _REQUIRED_MODE)
    return token


def derive_ui_token(token: str) -> str:
    """Derive the browser's console bearer token from the daemon token.

    The daemon token remains the write-capable local-admin token stored on disk.
    The derived UI token is what ``/api/v1/auth/ui-token`` returns; middleware
    accepts it only for safe REST reads, read-only operation calls, project
    creation setup, narrow provider-auth/setup writes, and explicit local
    console operations such as task status updates.
    """
    digest = hmac.new(token.encode("utf-8"), _UI_TOKEN_MESSAGE, hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"ui_console_{encoded}"


def requires_auth(path: str) -> bool:
    """Return True if `path` must carry a valid bearer token.

    True only for protected API surfaces (`/api/v1/*`, `/mcp/*`) minus the
    explicit whitelist. Everything else — static UI, openapi.json, docs UI —
    passes through without auth.
    """
    for whitelisted in WHITELIST_PREFIXES:
        if path == whitelisted or path.startswith(whitelisted + "/"):
            return False
    for protected in PROTECTED_PREFIXES:
        if path == protected or path.startswith(protected + "/"):
            return True
    return False


def _allows_ui_read(path: str, method: str) -> bool:
    """Return True when a UI token may read this request."""
    if method.upper() not in _UI_SAFE_METHODS:
        return False
    return path == "/api/v1" or path.startswith("/api/v1/")


def _allows_ui_read_operation_call(path: str, method: str) -> bool:
    """Return True for POST transport calls whose operation spec is read-only."""
    if method.upper() != "POST":
        return False
    segments = path.strip("/").split("/")
    if (
        len(segments) != 5
        or segments[:3] != ["api", "v1", "operations"]
        or segments[4] != "call"
        or not segments[3]
    ):
        return False

    from stackos.operations.registry import build_operation_registry
    from stackos.repositories.base import NotFoundError

    try:
        spec = build_operation_registry().get(segments[3], surface="rest")
    except NotFoundError:
        return False
    return spec.read_only


def _allows_ui_console_operation_call(path: str, method: str) -> bool:
    """Return True when the operation's REST contract is explicitly browser-safe."""
    if method.upper() != "POST":
        return False
    segments = path.strip("/").split("/")
    if (
        len(segments) != 5
        or segments[:3] != ["api", "v1", "operations"]
        or segments[4] != "call"
        or not segments[3]
    ):
        return False
    from stackos.operations.registry import build_operation_registry
    from stackos.repositories.base import NotFoundError

    try:
        spec = build_operation_registry().get(segments[3], surface="rest")
    except NotFoundError:
        return False
    return spec.surfaces.rest.browser_safe


def _allows_ui_auth_setup(path: str, method: str) -> bool:
    """Return True for the only local-admin writes the browser may perform.

    The browser never receives the daemon token and cannot access MCP. The
    console token can manage global Account setup and explicit project
    Connection attachments. This closed method/path matcher must change with
    the registered auth operation surface; namespace-prefix authorization is
    deliberately forbidden.
    """
    method = method.upper()
    segments = path.strip("/").split("/")
    if segments[:4] == ["api", "v1", "auth", "accounts"]:
        if len(segments) == 5:
            return method in {"POST", "PATCH"}
        if len(segments) == 6 and segments[5] in {"start", "test", "revoke"}:
            return method == "POST"
        if len(segments) == 6 and segments[5] == "authorization":
            return method in {"POST", "DELETE"}
        return False
    if (
        len(segments) == 9
        and segments[:3] == ["api", "v1", "projects"]
        and segments[3].isdigit()
        and segments[4:6] == ["connections", "accounts"]
        and bool(segments[6])
        and segments[7] == "session"
        and segments[8] in {"connect", "disconnect"}
    ):
        return method == "POST"
    return (
        len(segments) == 7
        and segments[:3] == ["api", "v1", "projects"]
        and segments[3].isdigit()
        and segments[4:6] == ["connections", "accounts"]
        and method in {"POST", "DELETE"}
    )


def _allows_ui_project_setup(path: str, method: str) -> bool:
    """Return True for the local browser's project creation setup mutation."""
    return method.upper() == "POST" and path == "/api/v1/projects"


def _ui_scope_for_request(path: str, method: str) -> str | None:
    if _allows_ui_read(path, method):
        return "ui-read"
    if _allows_ui_read_operation_call(path, method):
        return "ui-read-operation"
    if _allows_ui_console_operation_call(path, method):
        return "ui-console-operation"
    if _allows_ui_project_setup(path, method):
        return "ui-project-setup"
    if _allows_ui_auth_setup(path, method):
        return "ui-auth-setup"
    return None


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Reject requests whose bearer token does not match an allowed scope.

    The daemon token is write-capable local-admin authority used by REST callers
    and the local MCP bridge process. The bridge does not reveal that token to
    agents; agent workflow execution is gated inside MCP by run-plan grants.
    The UI token is REST-only and limited to reads, read-only operation calls,
    setup writes, and explicit local-console writes needed by the product UI.
    It cannot access MCP or general mutation routes.
    Token values are supplied at construction time so middleware setup never
    re-reads the file at request time. Token rotation requires a daemon restart,
    which matches the spec (rotation runs via `make install`).
    """

    def __init__(self, app: object, *, token: str, ui_token: str | None = None) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._token = token
        self._ui_token = ui_token or derive_ui_token(token)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Compare the bearer token in constant time before forwarding."""
        settings = getattr(request.app.state, "settings", None)
        if settings is not None and settings.process_role == "server":
            return await _server_dispatch(request, call_next, settings)
        if request.method == "GET" and request.url.path == _OAUTH_CALLBACK_PATH:
            # This exact route is authorized by the short-lived, bound,
            # one-time OAuth transaction. No other method/path inherits it.
            return await call_next(request)
        if not requires_auth(request.url.path):
            return await call_next(request)

        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not value:
            return JSONResponse(
                {"detail": "missing bearer token"},
                status_code=401,
                headers={"www-authenticate": 'Bearer realm="stackos"'},
            )
        if secrets.compare_digest(value, self._token):
            request.state.auth_scope = "daemon-admin"
            return await call_next(request)

        if secrets.compare_digest(value, self._ui_token):
            scope = _ui_scope_for_request(request.url.path, request.method)
            if scope is None:
                return JSONResponse(
                    {
                        "detail": (
                            "UI token can only read REST data, call read-only or explicitly "
                            "browser-safe operations, create projects, and manage provider "
                            "auth setup; use the daemon token for other mutations"
                        )
                    },
                    status_code=403,
                )
            request.state.auth_scope = scope
            return await call_next(request)

        return JSONResponse(
            {"detail": "invalid bearer token"},
            status_code=401,
            headers={"www-authenticate": 'Bearer realm="stackos"'},
        )


@dataclass(frozen=True)
class UserPrincipal:
    """Provider-verified identity, never a caller-supplied owner ID."""

    user_id: str
    environment_id: str


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def session_cookie_name(settings: Settings) -> str:
    # Cookies ignore ports. Bind both the name and persisted credential to the
    # exact selected origin, even when two environments share 127.0.0.1.
    return "stackos_session_" + _digest(f"{settings.environment_id}:{settings.server_url}")[:24]


class ServerAuth:
    """Supabase transport and minimal server session ownership."""

    def __init__(self, settings: Settings, session: Session) -> None:
        if settings.process_role != "server":
            raise HTTPException(404, "user authentication requires the selected server")
        self.settings = settings
        self.session = session

    async def call(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        assert self.settings.supabase_anon_key is not None
        headers = {"apikey": self.settings.supabase_anon_key.get_secret_value()}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        try:
            async with httpx.AsyncClient(
                timeout=10, follow_redirects=False, trust_env=False
            ) as client:
                response = await client.request(
                    method,
                    f"{self.settings.supabase_url}/auth/v1/{path}",
                    headers=headers,
                    json=payload,
                )
            if not response.is_success:
                code = "authentication_failed"
                if response.status_code == 429:
                    raise HTTPException(429, {"code": "retry_later", "retryable": True})
                if response.status_code >= 500:
                    raise HTTPException(503, {"code": "auth_unavailable", "retryable": True})
                if path.startswith("token"):
                    code = "sign_in_failed_or_email_unverified"
                if path == "verify":
                    code = "verification_expired_or_invalid"
                raise HTTPException(401, {"code": code, "retryable": False})
            result = response.json() if response.content else {}
            if not isinstance(result, dict):
                raise ValueError("unexpected Auth response")
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(503, {"code": "auth_unavailable", "retryable": True}) from exc

    async def principal(self, access_token: str) -> UserPrincipal:
        user = await self.call("GET", "user", access_token=access_token)
        try:
            user_id = str(UUID(user["id"]))
            if not user.get("email_confirmed_at"):
                raise ValueError("email not verified")
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(401, {"code": "email_verification_required"}) from exc
        return UserPrincipal(user_id, self.settings.environment_id or "")

    def tokens(self, row: UserSession) -> dict[str, Any]:
        try:
            return json.loads(
                decrypt(
                    row.encrypted_payload,
                    nonce=row.nonce,
                    project_id=None,
                    kind=f"user-session:{row.environment_id}:{row.server_origin}:{row.token_digest}",
                    seed=load_seed(self.settings.seed_path),
                )
            )
        except (CryptoError, ValueError) as exc:
            raise HTTPException(401, {"code": "session_invalid_sign_in_required"}) from exc

    def save_tokens(self, row: UserSession, tokens: dict[str, Any]) -> None:
        row.encrypted_payload, row.nonce = encrypt(
            json.dumps(tokens).encode(),
            project_id=None,
            kind=f"user-session:{row.environment_id}:{row.server_origin}:{row.token_digest}",
            seed=load_seed(self.settings.seed_path),
        )
        self.session.add(row)
        self.session.commit()

    async def create(
        self,
        tokens: dict[str, Any],
        response: Response,
        *,
        recovery: bool = False,
        previous_cookie: str | None = None,
    ) -> dict[str, Any]:
        principal = await self.principal(tokens.get("access_token", ""))
        if not tokens.get("refresh_token"):
            raise HTTPException(401, {"code": "session_missing"})
        opaque, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        row = UserSession(
            token_digest=_digest(opaque),
            environment_id=principal.environment_id,
            server_origin=self.settings.server_url or "",
            user_id=principal.user_id,
            csrf_digest=_digest(csrf),
            encrypted_payload=b"",
            nonce=b"",
            expires_at=int(time.time()) + (900 if recovery else 604800),
            recovery=recovery,
        )
        if previous_cookie:
            for previous in self.lock_rows(
                select(UserSession).where(
                    col(UserSession.token_digest) == _digest(previous_cookie),
                    col(UserSession.environment_id) == principal.environment_id,
                    col(UserSession.server_origin) == row.server_origin,
                )
            ):
                self.session.delete(previous)
        self.save_tokens(
            row,
            {
                "access_token": tokens["access_token"],
                "refresh_token": tokens["refresh_token"],
                "csrf": csrf,
            },
        )
        response.set_cookie(
            session_cookie_name(self.settings),
            opaque,
            httponly=True,
            secure=(self.settings.server_url or "").startswith("https:"),
            samesite="strict",
            max_age=900 if recovery else 604800,
            path="/",
        )
        return {
            "status": "recovery_required" if recovery else "authenticated",
            "user_id": principal.user_id,
            "environment_id": principal.environment_id,
            "csrf_token": csrf,
        }

    def resolve(self, request: Request, *, lock: bool = False) -> UserSession:
        value = request.cookies.get(session_cookie_name(self.settings), "")
        query = select(UserSession).where(UserSession.token_digest == _digest(value))
        rows = self.lock_rows(query) if lock else list(self.session.exec(query).all())
        row = rows[0] if rows else None
        if (
            row is None
            or row.environment_id != self.settings.environment_id
            or row.server_origin != self.settings.server_url
            or row.expires_at <= time.time()
        ):
            raise HTTPException(401, {"code": "session_expired_or_invalid"})
        return row

    def lock_rows(self, query: Any) -> list[UserSession]:
        # No synchronous lock wait while this async caller holds a lock over a
        # provider await. The same rule covers refresh, logout, login replacement
        # and recovery revocation, rather than only the first observed caller.
        try:
            return list(self.session.exec(query.with_for_update(nowait=True)).all())
        except OperationalError as exc:
            self.session.rollback()
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                raise HTTPException(409, {"code": "session_busy", "retryable": True}) from exc
            raise

    def csrf(self, request: Request, row: UserSession) -> None:
        check_server_origin(request, self.settings, required=True)
        if not secrets.compare_digest(
            _digest(request.headers.get("x-csrf-token", "")), row.csrf_digest
        ):
            raise HTTPException(403, {"code": "csrf_failed"})


def check_server_origin(request: Request, settings: Settings, *, required: bool) -> None:
    origin = request.headers.get("origin")
    if (
        (required or origin is not None) and origin != settings.server_url
    ) or f"{request.url.scheme}://{request.url.netloc}" != settings.server_url:
        raise HTTPException(403, {"code": "origin_failed"})


async def _server_dispatch(
    request: Request,
    call_next: RequestResponseEndpoint,
    settings: Settings,
) -> Response:
    """No local-admin/bootstrap fallback in a selected online environment."""
    path, method = request.url.path, request.method
    if not (path.startswith(("/api/", "/mcp", "/generated-assets"))):
        return await call_next(request)
    try:
        check_server_origin(request, settings, required=method not in {"GET", "HEAD", "OPTIONS"})
        public = {
            ("GET", "/api/v1/health"),
            ("GET", "/api/v1/auth/callback"),
            ("GET", "/api/v1/auth/oauth/callback"),
        }
        public.update(
            ("POST", f"/api/v1/auth/{name}")
            for name in (
                "register",
                "login",
                "verify",
                "callback",
                "resend",
                "recover",
            )
        )
        if (method, path) in public:
            response = await call_next(request)
        elif request.headers.get("Authorization", "").startswith("Bearer stackos_runtime_"):
            # A computer credential is never a user session or local-admin token.
            # Admit only its finite operation set, even when cookies are also sent.
            from stackos.operations.local_runtime import RUNTIME_OPERATIONS
            from stackos.repositories.base import RepositoryError
            from stackos.repositories.workspaces import LocalRuntimeConnectionRepository

            allowed_paths = {f"/api/v1/operations/{name}/call" for name in RUNTIME_OPERATIONS}
            from stackos.operations.desktop import AVAILABILITY_PATH

            allowed_paths.add(AVAILABILITY_PATH)
            if method != "POST" or path not in allowed_paths:
                raise HTTPException(403, {"code": "runtime_scope_denied"})
            with Session(request.app.state.engine) as session:
                try:
                    runtime_connection = LocalRuntimeConnectionRepository(session).authenticate(
                        request.headers["Authorization"][7:],
                        environment_id=settings.environment_id or "",
                        server_origin=settings.server_url or "",
                    )
                except RepositoryError:
                    raise HTTPException(401, {"code": "connection_unavailable"}) from None
                request.state.principal = UserPrincipal(
                    runtime_connection.owner_user_id, runtime_connection.environment_id
                )
                request.state.local_runtime_connection_id = runtime_connection.id
                request.state.auth_scope = "local-runtime"
            response = await call_next(request)
        else:
            if path == "/api/v1/auth/ui-token":
                raise HTTPException(401, {"code": "sign_in_required"})
            with Session(request.app.state.engine) as session:
                auth = ServerAuth(settings, session)
                row = auth.resolve(request)
                if method not in {"GET", "HEAD", "OPTIONS"}:
                    auth.csrf(request, row)
                # Refresh/logout must remain usable after the access JWT expires.
                if path not in {"/api/v1/auth/refresh", "/api/v1/auth/logout"}:
                    request.state.principal = await auth.principal(auth.tokens(row)["access_token"])
                    if request.state.principal.user_id != row.user_id:
                        raise HTTPException(401, {"code": "session_invalid"})
                request.state.auth_scope = "verified-user"
                if row.recovery and path not in {
                    "/api/v1/auth/password",
                    "/api/v1/auth/logout",
                    "/api/v1/auth/session",
                }:
                    raise HTTPException(403, {"code": "password_reset_required"})
                request.state.user_session_digest = row.token_digest
                if path not in {
                    f"/api/v1/auth/{name}"
                    for name in (
                        "session",
                        "refresh",
                        "logout",
                        "password",
                    )
                }:
                    from stackos.api.project_access import authorize_request

                    authorize_request(session, request, settings)
            response = await call_next(request)
        if path.startswith("/api/v1/"):
            response.headers.update(
                {
                    "Cache-Control": "no-store",
                    # Chromium makes form Origin null under no-referrer. Sending
                    # only the origin preserves CSRF enforcement without the code.
                    "Referrer-Policy": "strict-origin"
                    if (method, path) == ("GET", "/api/v1/auth/callback")
                    else "no-referrer",
                }
            )
        return response
    except HTTPException as exc:
        return JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
            headers={"Cache-Control": "no-store"},
        )
