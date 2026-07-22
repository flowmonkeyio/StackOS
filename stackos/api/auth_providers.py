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
from stackos.api.envelopes import WriteResponse, write_response
from stackos.auth_providers import (
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
from stackos.repositories.base import RepositoryError

router = APIRouter(prefix="/api/v1", tags=["auth-providers"])


class AuthStartRequest(BaseModel):
    """Local-admin setup request. It never carries a secret."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"auth_method_key": "oauth2", "credential_ref": "cred_..."}},
    )

    auth_method_key: str | None = None
    credential_ref: str | None = None


class AuthTestRequest(BaseModel):
    """Sanitized auth test request using an opaque credential ref."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"credential_ref": "cred_..."}},
    )

    credential_ref: str


class AuthRevokeRequest(BaseModel):
    """Local-admin revoke request using an opaque credential ref."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"credential_ref": "cred_..."}},
    )

    credential_ref: str


class AuthCredentialSetRequest(BaseModel):
    """Local-admin credential profile write. The response never includes secrets."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "auth_method_key": "api_key",
                "profile_key": "primary",
                "label": "Primary",
                "fields": {"api_key": "provider-secret"},
            }
        },
    )

    auth_method_key: str | None = None
    profile_key: str = Field(default="default", min_length=1, max_length=120)
    label: str | None = Field(default=None, max_length=200)
    fields: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None


class AuthCredentialUpdateRequest(BaseModel):
    """Credential fields for the existing provider auth method."""

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, Any] = Field(default_factory=dict)
    label: str | None = Field(max_length=200)


@router.get("/auth/providers", response_model=list[AuthProviderOut])
async def list_auth_providers(
    provider_key: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[AuthProviderOut]:
    """List provider auth metadata synced from StackOS plugin manifests."""
    return AuthRepository(session).list_providers(provider_key=provider_key)


@router.get("/projects/{project_id}/auth/status", response_model=AuthStatusOut)
async def auth_status(
    project_id: int,
    provider_key: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> AuthStatusOut:
    """Return sanitized auth status and opaque credential references."""
    return AuthRepository(session).status(project_id=project_id, provider_key=provider_key)


@router.post(
    "/projects/{project_id}/auth/{provider_key}/start",
    response_model=WriteResponse[AuthStartOut],
    status_code=status.HTTP_200_OK,
)
async def auth_start(
    project_id: int,
    provider_key: str,
    body: AuthStartRequest | None = Body(default=None),
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> WriteResponse[AuthStartOut]:
    """Start a local-human setup flow without accepting or returning secrets."""
    return write_response(
        AuthRepository(session).start(
            project_id=project_id,
            provider_key=provider_key,
            settings=settings,
            auth_method_key=body.auth_method_key if body is not None else None,
            credential_ref=body.credential_ref if body is not None else None,
        )
    )


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
    if result is not None and result.project_id is not None:
        destination = (
            f"http://{settings.host}:{settings.port}/projects/"
            f"{result.project_id}/connections?{urlencode(query)}"
        )
    else:
        destination = f"http://{settings.host}:{settings.port}/?{urlencode(query)}"
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
    "/projects/{project_id}/auth/{provider_key}/credentials",
    response_model=WriteResponse[AuthCredentialSetOut],
    status_code=status.HTTP_201_CREATED,
)
async def auth_store_credential(
    project_id: int,
    provider_key: str,
    body: AuthCredentialSetRequest,
    session: Session = Depends(get_session),
) -> WriteResponse[AuthCredentialSetOut]:
    """Store a provider credential profile through the local-admin auth surface."""
    return write_response(
        AuthRepository(session).store_credential(
            project_id=project_id,
            provider_key=provider_key,
            auth_method_key=body.auth_method_key,
            profile_key=body.profile_key,
            label=body.label,
            fields=body.fields,
            expires_at=body.expires_at,
        )
    )


@router.get(
    "/projects/{project_id}/auth/credentials/{credential_ref}",
    response_model=AuthCredentialEditOut,
)
async def auth_get_credential(
    project_id: int,
    credential_ref: str,
    session: Session = Depends(get_session),
) -> AuthCredentialEditOut:
    """Return editable non-secret values and secret-presence flags."""
    return AuthRepository(session).get_credential_edit_state(
        project_id=project_id,
        credential_ref=credential_ref,
    )


@router.patch(
    "/projects/{project_id}/auth/credentials/{credential_ref}",
    response_model=WriteResponse[AuthCredentialSetOut],
)
async def auth_update_credential(
    project_id: int,
    credential_ref: str,
    body: AuthCredentialUpdateRequest,
    session: Session = Depends(get_session),
) -> WriteResponse[AuthCredentialSetOut]:
    """Update safe fields and explicitly supplied secrets without exposing either."""
    return write_response(
        AuthRepository(session).update_credential(
            project_id=project_id,
            credential_ref=credential_ref,
            fields=body.fields,
            label=body.label,
        )
    )


@router.post(
    "/projects/{project_id}/auth/test",
    response_model=WriteResponse[AuthTestOut],
)
async def auth_test(
    project_id: int,
    body: AuthTestRequest,
    session: Session = Depends(get_session),
) -> WriteResponse[AuthTestOut]:
    """Run a sanitized provider credential test without returning secrets."""
    return write_response(
        await AuthRepository(session).test(
            project_id=project_id,
            credential_ref=body.credential_ref,
        )
    )


@router.post(
    "/projects/{project_id}/auth/revoke",
    response_model=WriteResponse[AuthRevokeOut],
)
async def auth_revoke(
    project_id: int,
    body: AuthRevokeRequest,
    session: Session = Depends(get_session),
) -> WriteResponse[AuthRevokeOut]:
    """Revoke a provider credential through the local-admin REST surface."""
    return write_response(
        AuthRepository(session).revoke(
            project_id=project_id,
            credential_ref=body.credential_ref,
        )
    )


__all__ = ["router"]
