"""Canonical auth-provider repository facade."""

from __future__ import annotations

from sqlmodel import Session

from .credentials import CredentialStorageMixin
from .events import CredentialEventMixin
from .providers import ProviderMetadataMixin
from .resolution import CredentialResolutionMixin
from .status import CredentialStatusMixin
from .testing import CredentialTestingMixin


class AuthRepository(
    ProviderMetadataMixin,
    CredentialStatusMixin,
    CredentialStorageMixin,
    CredentialTestingMixin,
    CredentialResolutionMixin,
    CredentialEventMixin,
):
    """Auth-provider facade that never returns encrypted payloads or plaintext secrets."""

    def __init__(self, session: Session) -> None:
        self._s = session
