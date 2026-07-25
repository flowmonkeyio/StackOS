"""Daemon-internal credential resolution, renewal, and scope enforcement."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any, ClassVar

import httpx
from sqlmodel import col, select

from stackos.auth_providers.oauth_contracts import OAuthProviderContract, oauth_contract_for
from stackos.db.models import (
    Credential,
    CredentialScope,
    IntegrationCredential,
    ProjectCredential,
)
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository

from .oauth import OAuthTokenRequestError
from .schema import ResolvedCredential
from .utils import utcnow

_RENEWAL_WINDOW = timedelta(seconds=60)


class CredentialResolutionMixin:
    """Resolve opaque references and keep renewable credentials usable."""

    _oauth_refresh_locks: ClassVar[dict[tuple[int, int], asyncio.Lock]] = {}

    def require_attached_account(
        self,
        *,
        project_id: int,
        credential_ref: str,
        provider_key: str | None = None,
        require_connected: bool = True,
    ) -> Credential:
        """Validate one Account through the sole project-attachment boundary."""
        credential = self._s.exec(
            select(Credential).where(col(Credential.credential_ref) == credential_ref)
        ).first()
        if credential is None:
            raise NotFoundError(
                f"Account {credential_ref!r} not found",
                data={
                    "project_id": project_id,
                    "credential_ref": credential_ref,
                    "provider_key": provider_key,
                    "next_action": (
                        "Select an existing Account or create one in the local Accounts setup."
                    ),
                },
            )
        assert credential.id is not None
        attachment = self._s.exec(
            select(ProjectCredential).where(
                col(ProjectCredential.project_id) == project_id,
                col(ProjectCredential.credential_id) == credential.id,
            )
        ).first()
        if attachment is None:
            raise NotFoundError(
                f"Account {credential_ref!r} is not attached to project {project_id}",
                data={
                    "project_id": project_id,
                    "credential_ref": credential_ref,
                },
            )
        if credential.revoked_at is not None or credential.integration_credential_id is None:
            raise ConflictError(
                "Account is revoked",
                data={"credential_ref": credential_ref},
            )
        if provider_key is not None and credential.provider_key != provider_key:
            raise ValidationError(
                "Account provider does not match the requested provider",
                data={
                    "credential_ref": credential_ref,
                    "credential_provider": credential.provider_key,
                    "provider_key": provider_key,
                },
            )
        if require_connected and credential.status != "connected":
            raise ConflictError(
                "Account is not connected",
                data={
                    "credential_ref": credential_ref,
                    "status": credential.status,
                },
            )
        return credential

    async def resolve_for_execution(
        self,
        *,
        project_id: int,
        provider_key: str | None,
        credential_ref: str | None,
        operation: str,
        required_scopes: list[str] | tuple[str, ...] | None = None,
    ) -> ResolvedCredential:
        """Resolve, renew if needed, then enforce declared action scopes."""

        credential, row = self._resolve_credential(
            project_id=project_id,
            credential_ref=credential_ref,
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
        if credential.status not in {"connected", "expired"}:
            raise ConflictError(
                "credential is not connected",
                data={
                    "credential_ref": credential.credential_ref,
                    "status": credential.status,
                },
            )
        contract = self._optional_oauth_contract(credential)
        if contract is not None and self._needs_renewal(
            credential=credential,
            row=row,
            contract=contract,
        ):
            credential, row = await self._renew_under_lock(
                project_id=project_id,
                credential_ref=credential.credential_ref,
                contract=contract,
            )
        elif credential.expires_at is not None and credential.expires_at <= utcnow():
            credential.status = "repair-required"
            credential.updated_at = utcnow()
            self._s.add(credential)
            self._s.commit()
            raise ConflictError(
                "credential has expired and cannot be renewed",
                data={
                    "credential_ref": credential.credential_ref,
                    "status": "repair-required",
                    "next_action": "Reconnect this provider credential.",
                },
            )
        if self._uses_scoped_auth(credential):
            self._require_scopes(
                credential=credential,
                required_scopes=tuple(required_scopes or ()),
            )
        assert row.id is not None
        secret_payload = IntegrationCredentialRepository(self._s).get_decrypted(row.id)
        self.record_usage_event(
            credential=credential,
            provider_key=credential.provider_key,
            operation=operation,
            status="used",
            metadata_json={
                "credential_ref": credential.credential_ref,
                "required_scope_count": len(required_scopes or ()),
            },
            project_id=project_id,
        )
        self._s.commit()
        return ResolvedCredential(
            credential=credential,
            integration=row,
            secret_payload=secret_payload,
            config_json=credential.config_json,
        )

    async def _renew_under_lock(
        self,
        *,
        project_id: int,
        credential_ref: str,
        contract: OAuthProviderContract,
    ) -> tuple[Credential, IntegrationCredential]:
        credential, row = self._resolve_credential(
            project_id=project_id,
            credential_ref=credential_ref,
        )
        assert row.id is not None
        loop = asyncio.get_running_loop()
        lock_key = (id(loop), row.id)
        lock = self._oauth_refresh_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            self._s.expire_all()
            credential, row = self._resolve_credential(
                project_id=project_id,
                credential_ref=credential_ref,
            )
            if not self._needs_renewal(
                credential=credential,
                row=row,
                contract=contract,
            ):
                return credential, row
            payload = self._json_payload(row)
            expected_updated_at = row.updated_at
            try:
                response_body = await self._request_renewal(
                    contract=contract,
                    payload=payload,
                )
                self._validate_token_response(
                    contract=contract,
                    response_body=response_body,
                    grant_type=(
                        "client_credentials"
                        if contract.flow == "client_credentials"
                        else "refresh_token"
                    ),
                )
            except OAuthTokenRequestError as exc:
                if not exc.repair_required:
                    self._record_retryable_renewal_failure(
                        credential=credential,
                        provider_key=credential.provider_key,
                        reason="token-endpoint-unavailable",
                    )
                    raise ConflictError(
                        "credential renewal is temporarily unavailable",
                        data={
                            "credential_ref": credential_ref,
                            "status": "temporarily-unavailable",
                            "next_action": "Retry credential resolution later.",
                        },
                        retryable=exc.retryable,
                    ) from None
                self._mark_renewal_failed(
                    row=row,
                    credential=credential,
                    payload=payload,
                    expected_updated_at=expected_updated_at,
                )
                raise ConflictError(
                    "credential renewal failed",
                    data={
                        "credential_ref": credential_ref,
                        "status": "repair-required",
                        "next_action": "Reconnect this provider credential.",
                    },
                ) from None
            except httpx.HTTPError:
                self._record_retryable_renewal_failure(
                    credential=credential,
                    provider_key=credential.provider_key,
                    reason="token-endpoint-network-failure",
                )
                raise ConflictError(
                    "credential renewal is temporarily unavailable",
                    data={
                        "credential_ref": credential_ref,
                        "status": "temporarily-unavailable",
                        "next_action": "Retry credential resolution later.",
                    },
                    retryable=True,
                ) from None
            except (ValueError, ValidationError):
                self._mark_renewal_failed(
                    row=row,
                    credential=credential,
                    payload=payload,
                    expected_updated_at=expected_updated_at,
                )
                raise ConflictError(
                    "credential renewal failed",
                    data={
                        "credential_ref": credential_ref,
                        "status": "repair-required",
                        "next_action": "Reconnect this provider credential.",
                    },
                ) from None
            updated_payload = dict(payload)
            updated_payload["access_token"] = str(response_body["access_token"]).strip()
            refresh_value = response_body.get("refresh_token")
            if isinstance(refresh_value, str) and refresh_value.strip():
                updated_payload["refresh_token"] = refresh_value.strip()
            expires_at = None
            raw_expires_in = response_body.get("expires_in")
            if isinstance(raw_expires_in, (int, float)) and raw_expires_in > 0:
                expires_at = utcnow() + timedelta(seconds=float(raw_expires_in))
            safe_config = dict(credential.config_json or {})
            safe_config["oauth_connection_status"] = "connected"
            response_declares_scopes = any(
                isinstance(response_body.get(field), str | list)
                for field in contract.response_scope_fields
            )
            if response_declares_scopes or contract.flow == "client_credentials":
                safe_config["scope_status"] = "known"
            safe_config.update(
                self._provider_response_config(
                    contract=contract,
                    response_body=response_body,
                )
            )
            if not self._cas_profile_update(
                row=row,
                credential=credential,
                expected_updated_at=expected_updated_at,
                payload=updated_payload,
                safe_config=safe_config,
                expires_at=expires_at,
            ):
                self._s.expire_all()
                current_credential, current_row = self._resolve_credential(
                    project_id=project_id,
                    credential_ref=credential_ref,
                )
                if not self._needs_renewal(
                    credential=current_credential,
                    row=current_row,
                    contract=contract,
                ):
                    return current_credential, current_row
                raise ConflictError(
                    "credential changed during renewal",
                    data={
                        "credential_ref": credential_ref,
                        "status": "retryable-conflict",
                        "next_action": "Retry credential resolution.",
                    },
                )
            credential.status = "connected"
            credential.expires_at = expires_at
            credential.config_json = self._safe_config(safe_config)
            credential.updated_at = utcnow()
            self._s.add(credential)
            self._replace_scopes(
                credential=credential,
                response_body=response_body,
                fallback_scopes=(
                    contract.scopes if contract.flow == "client_credentials" else None
                ),
                contract=contract,
            )
            self.record_refresh_event(
                credential=credential,
                provider_key=credential.provider_key,
                status="refreshed",
                metadata_json={"flow": contract.flow},
                project_id=project_id,
            )
            self._s.commit()
            self._s.expire_all()
            return self._resolve_credential(
                project_id=project_id,
                credential_ref=credential_ref,
            )

    async def _request_renewal(
        self,
        *,
        contract: OAuthProviderContract,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if contract.flow == "client_credentials":
            request_data = {
                "grant_type": "client_credentials",
                **dict(contract.token_params),
            }
            if contract.scopes:
                request_data["scope"] = contract.scope_separator.join(contract.scopes)
            return await self._post_oauth_token_request(
                contract=contract,
                application=payload,
                data=request_data,
            )
        refresh_value = payload.get("refresh_token")
        if not isinstance(refresh_value, str) or not refresh_value.strip():
            raise ValidationError("renewable credential is missing renewal material")
        return await self._post_oauth_token_request(
            contract=contract,
            application=payload,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_value.strip(),
                **dict(contract.token_params),
            },
        )

    def _mark_renewal_failed(
        self,
        *,
        row: IntegrationCredential,
        credential: Credential,
        payload: dict[str, Any],
        expected_updated_at: Any,
    ) -> None:
        safe_config = dict(credential.config_json or {})
        safe_config["oauth_connection_status"] = "repair-required"
        if not self._cas_profile_update(
            row=row,
            credential=credential,
            expected_updated_at=expected_updated_at,
            payload=payload,
            safe_config=safe_config,
            expires_at=credential.expires_at,
        ):
            return
        credential.status = "repair-required"
        credential.config_json = self._safe_config(safe_config)
        credential.updated_at = utcnow()
        self._s.add(credential)
        self.record_refresh_event(
            credential=credential,
            provider_key=credential.provider_key,
            status="failed",
            metadata_json={"reason": "provider-renewal-failed"},
        )
        self._s.commit()

    def _record_retryable_renewal_failure(
        self,
        *,
        credential: Credential,
        provider_key: str,
        reason: str,
    ) -> None:
        self.record_refresh_event(
            credential=credential,
            provider_key=provider_key,
            status="retryable-failure",
            metadata_json={"reason": reason},
        )
        self._s.commit()

    def _require_scopes(
        self,
        *,
        credential: Credential,
        required_scopes: tuple[str, ...],
    ) -> None:
        normalized_required = tuple(sorted({scope.strip() for scope in required_scopes if scope}))
        if not normalized_required:
            return
        scope_status = (credential.config_json or {}).get("scope_status")
        if scope_status != "known":
            raise ConflictError(
                "credential scopes are unknown",
                data={
                    "credential_ref": credential.credential_ref,
                    "required_scopes": list(normalized_required),
                    "next_action": "Reconnect this credential to record provider grants.",
                },
            )
        assert credential.id is not None
        granted = {
            row.scope
            for row in self._s.exec(
                select(CredentialScope).where(col(CredentialScope.credential_id) == credential.id)
            ).all()
        }
        missing = sorted(set(normalized_required) - granted)
        if missing:
            raise ConflictError(
                "credential is missing required scopes",
                data={
                    "credential_ref": credential.credential_ref,
                    "missing_scopes": missing,
                    "next_action": "Reconnect this credential with the required grants.",
                },
            )

    def _optional_oauth_contract(
        self,
        credential: Credential,
    ) -> OAuthProviderContract | None:
        method = self._configured_auth_method(credential)
        has_refresh_material = bool(
            method is not None and any(field.key == "refresh_token" for field in method.fields)
        )
        if method is not None and not (
            method.interactive
            or method.auth_type == "oauth-client-credentials"
            or has_refresh_material
        ):
            return None
        try:
            return oauth_contract_for(
                credential.provider_key,
                safe_config=credential.config_json,
            )
        except ValidationError:
            return None

    def _uses_scoped_auth(self, credential: Credential) -> bool:
        method = self._configured_auth_method(credential)
        if method is None:
            return True
        return self._method_requires_local_scope_gate(method)

    def _configured_auth_method(self, credential: Credential) -> Any | None:
        method_key = credential.auth_method_key
        if not method_key:
            return None
        provider = self._get_provider(
            credential.provider_key,
            required=False,
            sync=False,
        )
        if provider is None:
            return None
        return self._get_auth_method(provider, method_key, required=False)

    def _needs_renewal(
        self,
        *,
        credential: Credential,
        row: IntegrationCredential,
        contract: OAuthProviderContract,
    ) -> bool:
        payload = self._json_payload(row)
        if contract.flow == "client_credentials" and not payload.get("access_token"):
            return True
        if (
            contract.flow == "authorization_code"
            and not payload.get("access_token")
            and payload.get("refresh_token")
        ):
            return True
        if credential.expires_at is None:
            return False
        return credential.expires_at <= utcnow() + _RENEWAL_WINDOW

    def _json_payload(self, row: IntegrationCredential) -> dict[str, Any]:
        assert row.id is not None
        try:
            payload = json.loads(
                IntegrationCredentialRepository(self._s).get_decrypted(row.id).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConflictError("OAuth credential payload is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ConflictError("OAuth credential payload must be an object")
        return payload

    def _resolve_credential(
        self,
        *,
        project_id: int,
        credential_ref: str | None,
    ) -> tuple[Credential, IntegrationCredential]:
        if credential_ref is None:
            raise ValidationError("credential_ref is required")
        credential = self.require_attached_account(
            project_id=project_id,
            credential_ref=credential_ref,
            require_connected=False,
        )
        row = self._s.get(IntegrationCredential, credential.integration_credential_id)
        if row is None:
            raise NotFoundError(
                "backing credential not found",
                data={"credential_ref": credential.credential_ref},
            )
        return credential, row
