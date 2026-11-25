"""Closed server REST admission over the shared persisted project boundary."""

from __future__ import annotations

import re
import time
from collections.abc import Iterable

from fastapi import HTTPException, Request
from sqlmodel import Session

from stackos.config import Settings
from stackos.repositories.base import RepositoryError
from stackos.repositories.project_access import (
    PROJECT_PREPARATION_OPERATIONS,
    authorize_project_refs,
    require_project,
)

_PROJECT_READ = re.compile(
    r"/api/v1/projects/(?P<project_id>[1-9][0-9]*)(?:/"
    r"(?:runs|run-plans|resource-records|schedules|action-calls|workflow-template-extensions|"
    r"workflow-templates(?:/[^/]+(?:/extension)?)?|cost|budgets(?:/[^/]+)?|"
    r"context/(?:timeline(?:/stream)?|snapshots)|learnings|experiments(?:/observations)?|decisions))?"
)
_CHILD_READ = re.compile(
    r"/api/v1/(?:(?:runs/(?P<run_id>[1-9][0-9]*)(?:/children)?)|"
    r"(?:run-plans/(?P<run_plan_id>[1-9][0-9]*))|"
    r"(?:resource-records/(?P<record_id>[1-9][0-9]*)))"
)
_OPERATION_CALL = re.compile(r"/api/v1/operations/[^/]+/call")
_OPERATION_CATALOG = re.compile(r"/api/v1/operations(?:/[A-Za-z][A-Za-z0-9_.]*)?")
_ACCOUNT_ROUTE = re.compile(
    r"/api/v1/(?:auth/providers|auth/accounts(?:/[^/]+(?:/(?:start|test|revoke))?)?"
    r"|projects/[1-9][0-9]*/connections/accounts(?:/[^/]+)?)"
)
_FILE_READ = re.compile(
    r"/api/v1/projects/(?P<project_id>[1-9][0-9]*)/files/"
    r"(?:(?:artifact/(?P<artifact_id>[1-9][0-9]*))|"
    r"(?:action-output/(?P<action_call_id>[1-9][0-9]*)))"
)
_ARTIFACT_READ = re.compile(r"/api/v1/artifacts(?:/(?P<artifact_id>[1-9][0-9]*))?")


def bind_user_session(session: Session, request: Request, settings: Settings) -> None:
    session.info["user_principal"] = getattr(request.state, "principal", None)
    session.info["local_runtime_connection_id"] = getattr(
        request.state, "local_runtime_connection_id", None
    )
    if settings.process_role == "server":
        session.info["server_environment_id"] = settings.environment_id


def authorize_request(session: Session, request: Request, settings: Settings) -> None:
    from stackos.auth import _ui_scope_for_request

    bind_user_session(session, request, settings)
    path, method = request.url.path, request.method
    from stackos.operations.backups import operation_specs as backup_specs

    if method == "POST" and path in {spec.surfaces.rest.path for spec in backup_specs()}:
        from stackos.backups.models import BackupError, require_standalone_backup

        try:
            require_standalone_backup(settings)
        except BackupError as exc:
            raise HTTPException(403, exc.as_dict()) from exc
    if path in {"/mcp", "/mcp/"}:
        return  # MCP binds this verified request to the shared operation dispatcher.
    if method in {"GET", "HEAD"} and _OPERATION_CATALOG.fullmatch(path):
        return  # Contract metadata, also consumed by existing CLI discovery.
    if _ACCOUNT_ROUTE.fullmatch(path) or path == "/api/v1/auth/oauth/complete":
        return  # Exact auth routes dispatch; repository owns both Account and project checks.
    from stackos.operations.local_runtime import CONNECTION_OPERATIONS

    if method == "POST" and path in {
        f"/api/v1/operations/{name}/call" for name in CONNECTION_OPERATIONS
    }:
        return  # Dispatcher requires a verified owner and refuses runtime escalation.
    from stackos.operations.browser import operation_specs as browser_specs

    if method == "POST" and path in {
        f"/api/v1/operations/{spec.name}/call" for spec in browser_specs()
    }:
        # Server handlers own metadata only; dispatcher retains owner/grant checks.
        return
    if path == "/api/v1/projects" and method in {"GET", "POST"}:
        return
    if path == "/api/v1/context/timeline/stream" and method == "GET":
        return  # Each poll rechecks login and scopes events/usage to owned projects.
    if method == "POST" and path in {
        f"/api/v1/operations/schedule.{name}/call" for name in ("set", "toggle", "remove")
    }:
        return  # Shared schedule repository checks owner, target and existing grants.
    if method == "POST" and path == "/api/v1/operations/tracker.startDelivery/call":
        return  # Verified-owner admission is distinct from standalone UI-token policy.
    if method == "POST" and path in {
        f"/api/v1/operations/{name}/call" for name in PROJECT_PREPARATION_OPERATIONS
    }:
        # Existing setup and direct/step action policies, with no admin bypass.
        # secret.set remains MCP-only and computer credentials remain restricted.
        return
    # The dispatcher checks the exact operation and its persisted identifiers;
    # verified login never creates local-admin or general mutation authority.
    if (
        _OPERATION_CALL.fullmatch(path)
        and _ui_scope_for_request(
            path, method, registry=getattr(request.app.state, "operation_registry", None)
        )
        is not None
    ):
        return
    match = (
        (
            _PROJECT_READ.fullmatch(path)
            or _CHILD_READ.fullmatch(path)
            or _FILE_READ.fullmatch(path)
            or _ARTIFACT_READ.fullmatch(path)
        )
        if method in {"GET", "HEAD"}
        else None
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


async def authorize_stream_poll(
    session: Session, request: Request, project_id: int | None, *, project_ids: Iterable[int] = ()
) -> bool:
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
        required = set(project_ids)
        if project_id is not None:
            required.add(project_id)
        for required_id in required:
            require_project(session, required_id)
        return True
    except (HTTPException, RepositoryError):
        return False
