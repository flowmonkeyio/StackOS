"""Per-install bearer-token authentication.

Single token at `state_dir/auth.token`. Generated on first daemon start.
Required on every REST and MCP request. The `/api/v1/health` endpoint is
explicitly whitelisted so `doctor` can probe liveness *before* it has the
token resolved (e.g., when diagnosing token-related failures).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import stat
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

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
# - ``/api/v1/ingress/telegram``: Telegram webhooks and local relay processes
#   cannot carry the daemon bearer token. The route validates Telegram's
#   `X-Telegram-Bot-Api-Secret-Token` against the encrypted provider
#   credential before it writes resources or agent requests. HostHeaderMiddleware
#   permits tunnel/deployed Host headers only on provider ingress paths; every
#   non-ingress API path remains loopback-host guarded.
# - ``/api/v1/ingress/slack``: Slack Events API and Interactivity requests
#   cannot carry the daemon bearer token. The route verifies Slack's
#   `X-Slack-Signature` HMAC against the encrypted Slack signing secret and
#   checks the replay-window timestamp before it writes communication resources
#   or agent requests. HostHeaderMiddleware permits tunnel/deployed Host headers
#   only on provider ingress paths; every non-ingress API path remains
#   loopback-host guarded.
WHITELIST_PREFIXES: tuple[str, ...] = (
    "/api/v1/health",
    "/api/v1/auth/ui-token",
    "/api/v1/ingress/telegram",
    "/api/v1/ingress/slack",
)

_TOKEN_BYTES = 32
_REQUIRED_MODE = 0o600
_UI_TOKEN_MESSAGE = b"stackos-ui-console-v1"
_UI_SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})
_UI_AUTH_SETUP_ACTIONS: frozenset[str] = frozenset({"test", "revoke"})
_UI_AUTH_PROVIDER_ACTIONS: frozenset[str] = frozenset({"credentials", "start"})
_UI_SETUP_OPERATION_CALLS: frozenset[str] = frozenset(
    {
        "communicationProfile.upsert",
        "ingressEndpoint.configure",
        "ingressEndpoint.refresh",
        "ingressEndpoint.sync",
    }
)
_UI_LOCAL_CONSOLE_OPERATION_CALLS: frozenset[str] = frozenset(
    {
        "tracker.updateTask",
    }
)


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
    """Return True for explicitly browser-safe local console operation writes."""
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
    return (
        segments[3] in _UI_SETUP_OPERATION_CALLS
        or segments[3] in _UI_LOCAL_CONSOLE_OPERATION_CALLS
    )


def _allows_ui_auth_setup(path: str, method: str) -> bool:
    """Return True for the only local-admin writes the browser may perform.

    The browser never receives the daemon token and cannot access MCP. The
    console token can only manage provider credential setup for a concrete
    project: store a local secret, run a sanitized health probe, start a local
    setup flow, or revoke an opaque credential ref.
    """
    if method.upper() != "POST":
        return False
    segments = path.strip("/").split("/")
    if (
        len(segments) < 6
        or segments[:3] != ["api", "v1", "projects"]
        or not segments[3].isdigit()
        or segments[4] != "auth"
    ):
        return False
    if len(segments) == 6:
        return segments[5] in _UI_AUTH_SETUP_ACTIONS
    if len(segments) == 7:
        return bool(segments[5]) and segments[6] in _UI_AUTH_PROVIDER_ACTIONS
    return False


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
