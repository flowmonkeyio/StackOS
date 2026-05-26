"""Daemon-internal credential resolution for connector execution."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

from sqlmodel import col, select

from stackos.db.models import Credential, IntegrationCredential
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository

from .schema import ResolvedCredential
from .utils import _PROJECT_SCOPED_PROVIDER_KEYS


class CredentialResolutionMixin:
    """Resolve opaque credential refs without exposing secrets to agents."""

    def resolve_for_execution(
        self,
        *,
        project_id: int,
        provider_key: str | None,
        credential_ref: str | None,
        operation: str,
    ) -> ResolvedCredential:
        """Resolve a credential ref for an in-process connector call.

        This is not an agent-facing read. It decrypts the backing payload only
        inside the daemon, records a redacted usage event, and returns an
        internal dataclass whose secret fields must not be serialized.
        """
        credential, row = self._resolve_credential(
            project_id=project_id,
            credential_ref=credential_ref,
        )
        if credential.status != "connected":
            raise ConflictError(
                "credential is not connected",
                data={
                    "credential_ref": credential.credential_ref,
                    "status": credential.status,
                },
            )
        if provider_key is not None and credential.provider_key != provider_key:
            raise ValidationError(
                "credential provider does not match action provider",
                data={
                    "credential_ref": credential.credential_ref,
                    "credential_provider": credential.provider_key,
                    "action_provider": provider_key,
                },
            )
        assert row.id is not None
        secret_payload = IntegrationCredentialRepository(self._s).get_decrypted(row.id)
        self.record_usage_event(
            credential=credential,
            provider_key=credential.provider_key,
            operation=operation,
            status="used",
            metadata_json={"credential_ref": credential.credential_ref},
        )
        return ResolvedCredential(
            credential=credential,
            integration=row,
            secret_payload=secret_payload,
            config_json=row.config_json,
        )

    def _resolve_credential(
        self,
        *,
        project_id: int,
        credential_ref: str | None,
    ) -> tuple[Credential, IntegrationCredential]:
        if credential_ref is None:
            raise ValidationError("credential_ref is required")
        credential = self._s.exec(
            select(Credential).where(col(Credential.credential_ref) == credential_ref)
        ).first()
        if credential is None:
            raise NotFoundError(f"credential ref {credential_ref!r} not found")

        if credential.revoked_at is not None or credential.integration_credential_id is None:
            raise ConflictError(
                "credential is revoked",
                data={"credential_ref": credential.credential_ref},
            )
        row = self._s.get(IntegrationCredential, credential.integration_credential_id)
        if row is None:
            raise NotFoundError(
                "backing credential not found",
                data={"credential_ref": credential.credential_ref},
            )
        if row.kind in _PROJECT_SCOPED_PROVIDER_KEYS and row.project_id is None:
            raise NotFoundError(
                f"credential {credential.credential_ref!r} not in project {project_id}",
                data={"project_id": project_id, "credential_ref": credential.credential_ref},
            )
        if row.project_id is not None and row.project_id != project_id:
            raise NotFoundError(
                f"credential {credential.credential_ref!r} not in project {project_id}",
                data={"project_id": project_id, "credential_ref": credential.credential_ref},
            )
        return credential, row
