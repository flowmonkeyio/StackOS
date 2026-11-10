"""Closed server REST admission over the shared persisted project boundary."""

from __future__ import annotations

import re
import time

from fastapi import HTTPException, Request
from sqlmodel import Session

from stackos.config import Settings
from stackos.repositories.base import RepositoryError
from stackos.repositories.project_access import authorize_project_refs, require_project

_PROJECT_READ = re.compile(
    r"/api/v1/projects/(?P<project_id>[1-9][0-9]*)(?:/"
    r"(?:runs|run-plans|resource-records|cost|budgets(?:/[^/]+)?|"
    r"context/(?:timeline(?:/stream)?|snapshots)|learnings|experiments(?:/observations)?|decisions))?"
)
_CHILD_READ = re.compile(
    r"/api/v1/(?:(?:runs/(?P<run_id>[1-9][0-9]*)(?:/children)?)|"
    r"(?:run-plans/(?P<run_plan_id>[1-9][0-9]*))|"
    r"(?:resource-records/(?P<record_id>[1-9][0-9]*)))"
)
_OPERATION_CALL = re.compile(r"/api/v1/operations/[^/]+/call")


def bind_user_session(session: Session, request: Request, settings: Settings) -> None:
    session.info["user_principal"] = getattr(request.state, "principal", None)
    if settings.process_role == "server":
        session.info["server_environment_id"] = settings.environment_id


def authorize_request(session: Session, request: Request, settings: Settings) -> None:
    from stackos.auth import _ui_scope_for_request

    bind_user_session(session, request, settings)
    path, method = request.url.path, request.method
    if path == "/api/v1/projects" and method in {"GET", "POST"}:
        return
    # The dispatcher checks the exact operation and its persisted identifiers;
    # verified login never creates local-admin or general mutation authority.
    if _OPERATION_CALL.fullmatch(path) and _ui_scope_for_request(path, method) is not None:
        return
    match = (
        (_PROJECT_READ.fullmatch(path) or _CHILD_READ.fullmatch(path)) if method == "GET" else None
    )
    if match is None:
        raise HTTPException(403, {"code": "owner_boundary_required"})
    try:
        values = dict(request.query_params)
        # Reject duplicate control IDs rather than choosing a different value
        # from FastAPI's query parser. Path identity always owns its field.
        for key in request.query_params:
            if key.endswith("_id") and len(request.query_params.getlist(key)) != 1:
                raise HTTPException(422, {"code": "ambiguous_identifier"})
        values.update({key: value for key, value in match.groupdict().items() if value is not None})
        authorize_project_refs(session, values)
    except RepositoryError as exc:
        raise HTTPException(exc.http_status, {"code": "project_access_denied"}) from None


async def authorize_stream_poll(session: Session, request: Request, project_id: int | None) -> bool:
    """Recheck current session, provider revocation, and owner before each poll."""
    settings = request.app.state.settings
    if settings.process_role != "server":
        return True
    from stackos.auth import ServerAuth
    from stackos.db.model_auth import UserSession

    bind_user_session(session, request, settings)
    try:
        row = session.get(UserSession, request.state.user_session_digest)
        if row is None or row.expires_at <= time.time() or row.recovery:
            return False
        principal = request.state.principal
        if (
            row.user_id != principal.user_id
            or row.environment_id != principal.environment_id
            or row.server_origin != settings.server_url
        ):
            return False
        auth = ServerAuth(settings, session)
        if await auth.principal(auth.tokens(row)["access_token"]) != principal:
            return False
        require_project(session, project_id)
        return True
    except (HTTPException, RepositoryError):
        return False
