"""Compatibility surface for the auth-provider repository package."""

from __future__ import annotations

from stackos.integrations import integration_class_for

from .repository import AuthRepository
from .schema import (
    AuthCredentialSetOut,
    AuthFieldOut,
    AuthMethodOut,
    AuthProviderOut,
    AuthRevokeOut,
    AuthStartOut,
    AuthStatusOut,
    AuthTestOut,
    CredentialConnectionOut,
    ResolvedCredential,
)

__all__ = [
    "AuthCredentialSetOut",
    "AuthFieldOut",
    "AuthMethodOut",
    "AuthProviderOut",
    "AuthRepository",
    "AuthRevokeOut",
    "AuthStartOut",
    "AuthStatusOut",
    "AuthTestOut",
    "CredentialConnectionOut",
    "ResolvedCredential",
    "integration_class_for",
]
