"""StackOS auth provider boundary."""

from __future__ import annotations

from stackos.auth_providers.repository import (
    AccountOut,
    AuthCredentialEditOut,
    AuthCredentialSetOut,
    AuthFieldOut,
    AuthMethodOut,
    AuthProviderOut,
    AuthRepository,
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
]
