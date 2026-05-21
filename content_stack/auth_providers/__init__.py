"""StackOS auth provider boundary."""

from __future__ import annotations

from content_stack.auth_providers.repository import (
    AuthProviderOut,
    AuthRepository,
    AuthRevokeOut,
    AuthSecretSetOut,
    AuthStartOut,
    AuthStatusOut,
    AuthTestOut,
    CredentialConnectionOut,
    ResolvedCredential,
)

__all__ = [
    "AuthProviderOut",
    "AuthRepository",
    "AuthRevokeOut",
    "AuthSecretSetOut",
    "AuthStartOut",
    "AuthStatusOut",
    "AuthTestOut",
    "CredentialConnectionOut",
    "ResolvedCredential",
]
