"""Public auth-provider repository package surface."""

from __future__ import annotations

from stackos.integrations import integration_class_for

from .repository import AuthRepository
from .schema import (
    AccountOut,
    AuthCredentialEditOut,
    AuthCredentialSetOut,
    AuthFieldOut,
    AuthMethodOut,
    AuthProviderOut,
    AuthRevokeOut,
    AuthStartOut,
    AuthStatusOut,
    AuthTestOut,
    OAuthCallbackOut,
    ResolvedCredential,
)

__all__ = [
    "AccountOut",
    "AuthCredentialEditOut",
    "AuthCredentialSetOut",
    "AuthFieldOut",
    "AuthMethodOut",
    "AuthProviderOut",
    "AuthRepository",
    "AuthRevokeOut",
    "AuthStartOut",
    "AuthStatusOut",
    "AuthTestOut",
    "OAuthCallbackOut",
    "ResolvedCredential",
    "integration_class_for",
]
