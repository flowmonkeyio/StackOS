"""StackOS generic auth provider REST routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from content_stack.api.deps import get_session, get_settings
from content_stack.api.envelopes import WriteResponse, write_response
from content_stack.auth_providers import (
    AuthCredentialSetOut,
    AuthProviderOut,
    AuthRepository,
    AuthRevokeOut,
    AuthStartOut,
    AuthStatusOut,
    AuthTestOut,
)
from content_stack.config import Settings

router = APIRouter(prefix="/api/v1", tags=["auth-providers"])


class AuthStartRequest(BaseModel):
    """Local-admin setup request. It never carries a secret."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"auth_method_key": "oauth2", "redirect_uri": None}},
    )

    auth_method_key: str | None = None
    redirect_uri: str | None = None


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
            redirect_uri=body.redirect_uri if body is not None else None,
        )
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
