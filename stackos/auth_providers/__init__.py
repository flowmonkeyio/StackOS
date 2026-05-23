"""StackOS auth provider boundary."""

from __future__ import annotations

from stackos.auth_providers.repository import (
    AuthCredentialSetOut,
    AuthFieldOut,
    AuthMethodOut,
    AuthProviderOut,
    AuthRepository,
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
]
