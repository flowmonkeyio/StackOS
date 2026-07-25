"""StackOS generic auth provider REST routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Body, Depends, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from stackos.api.deps import get_session, get_settings
from stackos.api.envelopes import WriteResponse
from stackos.auth_providers import (
    AccountOut,
    AuthCredentialEditOut,
    AuthCredentialSetOut,
    AuthProviderOut,
    AuthRepository,
    AuthRevokeOut,
    AuthStartOut,
    AuthStatusOut,
    AuthTestOut,
)
from stackos.config import Settings
from stackos.operations.dispatcher import OperationDispatcher
from stackos.operations.registry import build_operation_registry
from stackos.repositories.base import RepositoryError

router = APIRouter(prefix="/api/v1", tags=["auth-providers"])


async def _dispatch_auth_operation(
    name: str,
    arguments: dict[str, Any],
    *,
    session: Session,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Execute an exact auth REST route through its registered operation contract."""

    result = await OperationDispatcher(build_operation_registry()).dispatch(
        name,
        {**arguments, "response_mode": "raw"},
        session=session,
        surface="rest",
        settings=settings,
        trusted_local_admin=True,
    )
    return result.payload


class AuthStartRequest(BaseModel):
    """Local-admin setup request. It never carries a secret."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"auth_method_key": "oauth2", "credential_ref": "cred_..."}},
    )

    auth_method_key: str | None = None
    credential_ref: str | None = None
    attach_project_id: int | None = None
    return_surface: str = Field(
        default="accounts",
        pattern="^(accounts|project-connections)$",
    )


class AuthCredentialSetRequest(BaseModel):
    """Global Account write. The response never includes secrets."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "auth_method_key": "api_key",
                "display_name": "Production",
                "fields": {"api_key": "provider-secret"},
            }
        },
    )

    auth_method_key: str | None = None
    display_name: str = Field(min_length=1, max_length=200)
    fields: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None
    attach_project_id: int | None = None


class AuthCredentialUpdateRequest(BaseModel):
    """Credential fields for the existing provider auth method."""

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, Any] = Field(default_factory=dict)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)


@router.get("/auth/providers", response_model=list[AuthProviderOut])
async def list_auth_providers(
    provider_key: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[AuthProviderOut]:
    """List provider auth metadata synced from StackOS plugin manifests."""
    return AuthRepository(session).list_providers(provider_key=provider_key)


@router.get("/auth/accounts", response_model=AuthStatusOut)
async def list_accounts(
    provider_key: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> AuthStatusOut:
    """Return the sanitized global Account inventory."""
    payload = await _dispatch_auth_operation(
        "account.list",
        {"provider_key": provider_key},
        session=session,
    )
    return AuthStatusOut.model_validate(payload)


@router.get(
    "/projects/{project_id}/connections/accounts",
    response_model=AuthStatusOut,
)
async def list_project_connections(
    project_id: int,
    provider_key: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> AuthStatusOut:
    """Return Accounts explicitly attached to one project."""
    payload = await _dispatch_auth_operation(
        "connection.list",
        {"project_id": project_id, "provider_key": provider_key},
        session=session,
    )
    return AuthStatusOut.model_validate(payload)


@router.post(
    "/auth/accounts/{provider_key}/start",
    response_model=WriteResponse[AuthStartOut],
    status_code=status.HTTP_200_OK,
)
async def auth_start(
    provider_key: str,
    body: AuthStartRequest | None = Body(default=None),
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> WriteResponse[AuthStartOut]:
    """Start a local-human setup flow without accepting or returning secrets."""
    payload = await _dispatch_auth_operation(
        "account.start",
        {
            "provider_key": provider_key,
            "auth_method_key": body.auth_method_key if body is not None else None,
            "credential_ref": body.credential_ref if body is not None else None,
            "attach_project_id": body.attach_project_id if body is not None else None,
            "return_surface": body.return_surface if body is not None else "accounts",
        },
        session=session,
        settings=settings,
    )
    return WriteResponse[AuthStartOut].model_validate(payload)


@router.get(
    "/auth/oauth/callback",
    response_class=RedirectResponse,
    status_code=status.HTTP_303_SEE_OTHER,
)
async def auth_oauth_callback(
    state_value: str | None = Query(default=None, alias="state", max_length=512),
    code: str | None = Query(default=None, max_length=4096),
    provider_error: str | None = Query(default=None, alias="error", max_length=200),
    _error_description: str | None = Query(
        default=None,
        alias="error_description",
        max_length=4096,
    ),
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    """Complete one bound OAuth transaction and immediately leave the callback URL."""

    result = None
    if state_value is not None:
        try:
            result = await AuthRepository(session).complete_oauth_callback(
                state=state_value,
                code=code,
                provider_error=provider_error,
                settings=settings,
            )
        except RepositoryError:
            session.rollback()
        except Exception:
            # Provider/library exceptions are deliberately collapsed here.
            session.rollback()
    callback_status = result.status if result is not None else "error"
    if callback_status not in {
        "connected",
        "authorization-denied",
        "repair-required",
        "stale-attempt",
    }:
        callback_status = "error"
    status_label = {
        "authorization-denied": "denied",
        "repair-required": "repair-required",
        "stale-attempt": "expired",
    }.get(callback_status, callback_status)
    query: dict[str, str] = {"oauth_status": status_label}
    if result is not None and result.provider_key is not None:
        query["provider_key"] = result.provider_key
    if (
        result is not None
        and result.return_surface == "project-connections"
        and result.attach_project_id is not None
    ):
        destination = (
            f"http://{settings.host}:{settings.port}/projects/"
            f"{result.attach_project_id}/connections?{urlencode(query)}"
        )
    else:
        destination = f"http://{settings.host}:{settings.port}/accounts?{urlencode(query)}"
    return RedirectResponse(
        destination,
        status_code=status.HTTP_303_SEE_OTHER,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'",
        },
    )


@router.post(
    "/auth/accounts/{provider_key}",
    response_model=WriteResponse[AuthCredentialSetOut],
    status_code=status.HTTP_201_CREATED,
)
async def auth_store_credential(
    provider_key: str,
    body: AuthCredentialSetRequest,
    session: Session = Depends(get_session),
) -> WriteResponse[AuthCredentialSetOut]:
    """Create a reusable Account through the local-admin auth surface."""
    payload = await _dispatch_auth_operation(
        "account.create",
        {
            "provider_key": provider_key,
            "auth_method_key": body.auth_method_key,
            "display_name": body.display_name,
            "fields": body.fields,
            "expires_at": body.expires_at,
            "attach_project_id": body.attach_project_id,
        },
        session=session,
    )
    return WriteResponse[AuthCredentialSetOut].model_validate(payload)


@router.get(
    "/auth/accounts/{credential_ref}",
    response_model=AuthCredentialEditOut,
)
async def auth_get_credential(
    credential_ref: str,
    session: Session = Depends(get_session),
) -> AuthCredentialEditOut:
    """Return editable non-secret values and secret-presence flags."""
    payload = await _dispatch_auth_operation(
        "account.get",
        {"credential_ref": credential_ref},
        session=session,
    )
    return AuthCredentialEditOut.model_validate(payload)


@router.patch(
    "/auth/accounts/{credential_ref}",
    response_model=WriteResponse[AuthCredentialSetOut],
)
async def auth_update_credential(
    credential_ref: str,
    body: AuthCredentialUpdateRequest,
    session: Session = Depends(get_session),
) -> WriteResponse[AuthCredentialSetOut]:
    """Update safe fields and explicitly supplied secrets without exposing either."""
    payload = await _dispatch_auth_operation(
        "account.update",
        {
            "credential_ref": credential_ref,
            "fields": body.fields,
            "display_name": body.display_name,
        },
        session=session,
    )
    return WriteResponse[AuthCredentialSetOut].model_validate(payload)


@router.post(
    "/auth/accounts/{credential_ref}/test",
    response_model=WriteResponse[AuthTestOut],
)
async def auth_test(
    credential_ref: str,
    session: Session = Depends(get_session),
) -> WriteResponse[AuthTestOut]:
    """Run a sanitized provider credential test without returning secrets."""
    payload = await _dispatch_auth_operation(
        "account.test",
        {"credential_ref": credential_ref},
        session=session,
    )
    return WriteResponse[AuthTestOut].model_validate(payload)


@router.post(
    "/auth/accounts/{credential_ref}/revoke",
    response_model=WriteResponse[AuthRevokeOut],
)
async def auth_revoke(
    credential_ref: str,
    session: Session = Depends(get_session),
) -> WriteResponse[AuthRevokeOut]:
    """Revoke a provider credential through the local-admin REST surface."""
    payload = await _dispatch_auth_operation(
        "account.revoke",
        {"credential_ref": credential_ref},
        session=session,
    )
    return WriteResponse[AuthRevokeOut].model_validate(payload)


@router.post(
    "/projects/{project_id}/connections/accounts/{credential_ref}",
    response_model=WriteResponse[AccountOut],
)
async def attach_account(
    project_id: int,
    credential_ref: str,
    session: Session = Depends(get_session),
) -> WriteResponse[AccountOut]:
    """Attach one global Account to a project Connection."""
    payload = await _dispatch_auth_operation(
        "connection.attach",
        {"project_id": project_id, "credential_ref": credential_ref},
        session=session,
    )
    return WriteResponse[AccountOut].model_validate(payload)


@router.delete(
    "/projects/{project_id}/connections/accounts/{credential_ref}",
    response_model=WriteResponse[AccountOut],
)
async def detach_account(
    project_id: int,
    credential_ref: str,
    session: Session = Depends(get_session),
) -> WriteResponse[AccountOut]:
    """Detach an Account without revoking or deleting it."""
    payload = await _dispatch_auth_operation(
        "connection.detach",
        {"project_id": project_id, "credential_ref": credential_ref},
        session=session,
    )
    return WriteResponse[AccountOut].model_validate(payload)


__all__ = ["router"]
