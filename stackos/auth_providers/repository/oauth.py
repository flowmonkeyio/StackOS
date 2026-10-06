"""Core daemon-owned OAuth authorization transaction lifecycle."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import delete, or_, update
from sqlmodel import col, select
from stackos_connectors import ConnectorAuth
from stackos_connectors import auth as connector_auth
from stackos_connectors.auth import OAuthProviderContract, OAuthTokenError, TokenResult

from stackos.config import Settings
from stackos.crypto.aes_gcm import encrypt_account
from stackos.db.models import (
    Credential,
    CredentialAccount,
    CredentialScope,
    IntegrationCredential,
    OAuthState,
)
from stackos.repositories.base import ConflictError, Envelope, NotFoundError, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository

from .schema import AuthStartOut, OAuthCallbackOut
from .utils import utcnow


class OAuthTokenRequestError(ValidationError):
    """Classified token-endpoint failure for renewal state decisions."""

    def __init__(
        self,
        detail: str,
        *,
        provider_key: str,
        retryable: bool,
        repair_required: bool,
        status_code: int | None = None,
    ) -> None:
        data: dict[str, Any] = {"provider_key": provider_key}
        if status_code is not None:
            data["status"] = status_code
        super().__init__(detail, data=data, retryable=retryable)
        self.repair_required = repair_required


class OAuthLifecycleMixin:
    """Complete OAuth flows while keeping protocol state inside the daemon."""

    def _get_oauth_contract(self, credential: Credential) -> OAuthProviderContract:
        """Adapt package validation into the host's safe repository error contract."""
        try:
            return connector_auth.get_auth_contract(
                credential.provider_key,
                method=credential.auth_method_key,
                config=credential.config_json,
            )
        except OAuthTokenError as exc:
            raise ValidationError(
                str(exc),
                data={"provider_key": credential.provider_key, "fields": list(exc.invalid_fields)},
            ) from None

    def start(
        self,
        *,
        provider_key: str,
        settings: Settings,
        auth_method_key: str | None = None,
        credential_ref: str | None = None,
        attach_project_id: int | None = None,
        return_surface: str = "accounts",
    ) -> Envelope[AuthStartOut]:
        if return_surface not in {"accounts", "project-connections"}:
            raise ValidationError("return_surface must be accounts or project-connections")
        if return_surface == "project-connections" and attach_project_id is None:
            raise ValidationError(
                "attach_project_id is required for the project-connections return surface"
            )
        provider = self._get_provider(provider_key)
        assert provider is not None
        if attach_project_id is not None:
            self._require_project(attach_project_id)
            self._require_provider_enabled_for_project(
                project_id=attach_project_id,
                provider=provider,
            )
        method = self._get_auth_method(provider, auth_method_key)
        assert method is not None
        setup_required = provider.auth_type not in {"none", "local"}
        if not method.interactive:
            return Envelope(
                data=AuthStartOut(
                    attach_project_id=attach_project_id,
                    return_surface=return_surface,
                    provider_key=provider.key,
                    auth_type=method.auth_type,
                    auth_method_key=method.key,
                    status=("requires-local-credential" if setup_required else "not-required"),
                    setup_url=(
                        self._local_setup_url(
                            settings=settings,
                            project_id=attach_project_id,
                            provider_key=provider_key,
                        )
                        if setup_required
                        else None
                    ),
                ),
                project_id=attach_project_id,
            )
        if credential_ref is None:
            raise ValidationError(
                "credential_ref is required for an interactive auth method",
                data={"provider_key": provider_key, "auth_method_key": method.key},
            )
        credential, row = self._global_credential(credential_ref)
        if credential.provider_key != provider_key:
            raise ValidationError(
                "credential provider does not match auth provider",
                data={
                    "credential_ref": credential_ref,
                    "credential_provider": credential.provider_key,
                    "provider_key": provider_key,
                },
            )
        configured_method = credential.auth_method_key
        if configured_method != method.key:
            raise ValidationError(
                "credential auth method does not match requested auth method",
                data={
                    "credential_ref": credential_ref,
                    "credential_auth_method_key": configured_method,
                    "auth_method_key": method.key,
                },
            )
        payload = self._oauth_payload(row)
        application = payload.get("_oauth_application_pending")
        if not isinstance(application, dict):
            # Successful exchanges promote application fields out of the
            # pending envelope. Rebuild it from the Account's durable secret
            # payload and safe configuration when reconnecting.
            safe_config = credential.config_json or {}
            application = {
                field.key: (payload.get(field.key) if field.secret else safe_config.get(field.key))
                for field in method.fields
                if (field.key in payload if field.secret else field.key in safe_config)
            }
            if not application:
                raise ConflictError(
                    "interactive provider application configuration is missing",
                    data={"credential_ref": credential_ref, "provider_key": provider_key},
                )
            payload["_oauth_application_pending"] = application
        client_id = application.get("client_id") or (credential.config_json or {}).get("client_id")
        if not isinstance(client_id, str) or not client_id.strip():
            raise ConflictError(
                "interactive provider application id is missing",
                data={"credential_ref": credential_ref, "provider_key": provider_key},
            )
        contract = self._get_oauth_contract(credential)
        if contract.flow != "authorization_code" or contract.authorization_endpoint is None:
            raise ValidationError(
                "auth method is not an authorization-code provider flow",
                data={"provider_key": provider_key, "auth_method_key": method.key},
            )

        raw_state = secrets.token_urlsafe(32)
        state_digest = self._state_digest(raw_state)
        redirect_uri = settings.oauth_callback_uri
        now = utcnow()
        expires_at = now + timedelta(seconds=settings.oauth_state_ttl_seconds)
        verifier: str | None = None
        optional_scopes = self._selected_optional_scopes(
            provider_key=provider_key,
            provider_config=provider.config_json,
            credential_config=credential.config_json,
        )
        if contract.pkce_mode != "unavailable":
            verifier = secrets.token_urlsafe(64)
        try:
            authorization = connector_auth.build_authorization_request(
                provider_key,
                auth=ConnectorAuth(method.key, application, credential.config_json or {}),
                redirect_uri=redirect_uri,
                state=raw_state,
                code_challenge=self._pkce_challenge(verifier) if verifier else None,
                optional_scopes=optional_scopes,
            )
        except OAuthTokenError as exc:
            raise ValidationError(str(exc), data={"provider_key": provider_key}) from None
        scopes = authorization.required_scopes

        payload["_oauth_pending"] = {
            "code_verifier": verifier,
            "required_scopes": list(scopes),
        }
        safe_config = dict(credential.config_json or {})
        safe_config["oauth_pending"] = True
        if safe_config.get("oauth_connection_status") != "connected":
            safe_config["oauth_connection_status"] = "pending"
        assert row.id is not None
        IntegrationCredentialRepository(self._s).set(
            credential_ref=credential.credential_ref,
            provider_key=credential.provider_key,
            secret_payload=self._encode_payload(payload),
            integration_credential_id=row.id,
            commit=False,
        )
        self._s.exec(
            update(OAuthState)
            .where(
                col(OAuthState.integration_credential_id) == row.id,
                col(OAuthState.consumed_at).is_(None),
            )
            .values(consumed_at=now)
        )
        state_row = OAuthState(
            attach_project_id=attach_project_id,
            return_surface=return_surface,
            provider_key=provider_key,
            credential_id=credential.id,
            integration_credential_id=row.id,
            state=state_digest,
            redirect_uri=redirect_uri,
            expires_at=expires_at,
        )
        self._s.add(state_row)
        if credential.status != "connected":
            credential.status = "pending"
        credential.config_json = self._safe_config(safe_config)
        credential.updated_at = now
        self._s.add(credential)
        self.record_usage_event(
            credential=credential,
            provider_key=provider_key,
            operation="account.start",
            status="authorization-pending",
            metadata_json={"auth_method_key": method.key},
            project_id=attach_project_id,
        )
        self._s.commit()
        authorization_url = authorization.url
        return Envelope(
            data=AuthStartOut(
                attach_project_id=attach_project_id,
                return_surface=return_surface,
                provider_key=provider_key,
                auth_type=method.auth_type,
                auth_method_key=method.key,
                status="authorization-pending",
                authorization_url=authorization_url,
                redirect_uri=redirect_uri,
                credential_ref=credential.credential_ref,
                expires_at=expires_at,
            ),
            project_id=attach_project_id,
        )

    @staticmethod
    def _selected_optional_scopes(
        *,
        provider_key: str,
        provider_config: dict[str, Any] | None,
        credential_config: dict[str, Any] | None,
    ) -> tuple[str, ...]:
        """Resolve operator-selected provider bundles from safe manifest facts."""

        raw_selected = (credential_config or {}).get("scope_bundles")
        if raw_selected is None or raw_selected == "":
            return ()
        if isinstance(raw_selected, str):
            selected = [item.strip() for item in raw_selected.split(",") if item.strip()]
        elif isinstance(raw_selected, list):
            selected = [str(item).strip() for item in raw_selected if str(item).strip()]
        else:
            raise ValidationError(
                "OAuth scope bundle selection must be a list or comma-separated string",
                data={"provider_key": provider_key, "field": "scope_bundles"},
            )
        raw_bundles = (provider_config or {}).get("scope_bundles")
        if not isinstance(raw_bundles, dict):
            raise ValidationError(
                "provider does not declare OAuth scope bundles",
                data={"provider_key": provider_key},
            )
        unknown = sorted(set(selected) - set(raw_bundles))
        if unknown:
            raise ValidationError(
                "OAuth scope bundle selection includes unknown bundles",
                data={"provider_key": provider_key, "unknown_scope_bundles": unknown},
            )
        scopes: list[str] = []
        for bundle_key in dict.fromkeys(selected):
            bundle = raw_bundles[bundle_key]
            raw_scopes = bundle.get("optional_scopes") if isinstance(bundle, dict) else None
            if not isinstance(raw_scopes, list) or not all(
                isinstance(scope, str) and scope.strip() for scope in raw_scopes
            ):
                raise ValidationError(
                    "provider OAuth scope bundle is invalid",
                    data={"provider_key": provider_key, "scope_bundle": bundle_key},
                )
            scopes.extend(scope.strip() for scope in raw_scopes)
        return tuple(dict.fromkeys(scopes))

    async def complete_oauth_callback(
        self,
        *,
        state: str,
        settings: Settings,
        code: str | None = None,
        provider_error: str | None = None,
    ) -> OAuthCallbackOut:
        del settings  # The redirect was frozen into the transaction at start.
        state_row = self.consume_oauth_state(state=state)
        if state_row is None:
            raise ConflictError("invalid or expired OAuth transaction")
        if state_row.integration_credential_id is None:
            raise ConflictError("OAuth transaction is no longer linked to a credential")
        row = self._s.get(IntegrationCredential, state_row.integration_credential_id)
        if row is None or row.id is None:
            raise NotFoundError("OAuth Account backing no longer exists")
        credential = self._credential_for_oauth_state(state_row=state_row, row=row)
        payload = self._oauth_payload(row)
        pending = payload.get("_oauth_pending")
        application = payload.get("_oauth_application_pending")
        if not isinstance(pending, dict) or not isinstance(application, dict):
            raise ConflictError("OAuth transaction is stale")
        expected_updated_at = row.updated_at
        contract = self._get_oauth_contract(credential)
        if provider_error is not None:
            status = self._finish_failed_attempt(
                row=row,
                credential=credential,
                payload=payload,
                expected_updated_at=expected_updated_at,
                outcome="authorization-denied",
            )
            return OAuthCallbackOut(
                attach_project_id=state_row.attach_project_id,
                return_surface=state_row.return_surface,
                provider_key=state_row.provider_key,
                credential_ref=credential.credential_ref,
                status=status,
            )
        if code is None or not code.strip():
            status = self._finish_failed_attempt(
                row=row,
                credential=credential,
                payload=payload,
                expected_updated_at=expected_updated_at,
                outcome="repair-required",
            )
            return OAuthCallbackOut(
                attach_project_id=state_row.attach_project_id,
                return_surface=state_row.return_surface,
                provider_key=state_row.provider_key,
                credential_ref=credential.credential_ref,
                status=status,
            )
        try:
            token_result = await connector_auth.request_token(
                credential.provider_key,
                auth=ConnectorAuth(
                    credential.auth_method_key,
                    application,
                    credential.config_json or {},
                ),
                grant_type="authorization_code",
                code=code.strip(),
                redirect_uri=state_row.redirect_uri or "",
                code_verifier=pending.get("code_verifier"),
            )
        except (httpx.HTTPError, ValueError, ValidationError):
            status = self._finish_failed_attempt(
                row=row,
                credential=credential,
                payload=payload,
                expected_updated_at=expected_updated_at,
                outcome="repair-required",
            )
            return OAuthCallbackOut(
                attach_project_id=state_row.attach_project_id,
                return_surface=state_row.return_surface,
                provider_key=state_row.provider_key,
                credential_ref=credential.credential_ref,
                status=status,
            )
        return self._finish_successful_exchange(
            state_row=state_row,
            row=row,
            credential=credential,
            payload=payload,
            application=application,
            contract=contract,
            token_result=token_result,
            expected_updated_at=expected_updated_at,
        )

    def _finish_successful_exchange(
        self,
        *,
        state_row: OAuthState,
        row: IntegrationCredential,
        credential: Credential,
        payload: dict[str, Any],
        application: dict[str, Any],
        contract: OAuthProviderContract,
        token_result: TokenResult,
        expected_updated_at: Any,
    ) -> OAuthCallbackOut:
        new_payload = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "_oauth_application_pending",
                "_oauth_pending",
                "access_token",
                "refresh_token",
                "expires_in",
                "scope",
                "scopes",
                "token_type",
            }
        }
        new_payload.update(application)
        new_payload["access_token"] = token_result.access_token
        refresh_value = token_result.refresh_token
        if isinstance(refresh_value, str) and refresh_value.strip():
            new_payload["refresh_token"] = refresh_value.strip()
        safe_config = dict(credential.config_json or {})
        safe_config.pop("oauth_pending", None)
        safe_config["oauth_connection_status"] = "connected"
        safe_config["scope_status"] = "known"
        safe_config.update(token_result.config_updates)
        expires_at = None
        raw_expires_in = token_result.expires_in
        if isinstance(raw_expires_in, (int, float)) and raw_expires_in > 0:
            expires_at = utcnow() + timedelta(seconds=float(raw_expires_in))
        if not self._cas_profile_update(
            row=row,
            credential=credential,
            expected_updated_at=expected_updated_at,
            payload=new_payload,
            safe_config=safe_config,
            expires_at=expires_at,
        ):
            return OAuthCallbackOut(
                attach_project_id=state_row.attach_project_id,
                return_surface=state_row.return_surface,
                provider_key=state_row.provider_key,
                credential_ref=credential.credential_ref,
                status="stale-attempt",
            )
        credential.status = "connected"
        credential.auth_method_key = str(safe_config.get("auth_method_key") or "oauth")
        credential.expires_at = expires_at
        credential.config_json = self._safe_config(safe_config)
        credential.updated_at = utcnow()
        self._s.add(credential)
        self._replace_scopes(
            credential=credential,
            token_result=token_result,
            fallback_scopes=self._required_scopes_from_pending(
                payload=payload,
                default=contract.scopes,
            ),
            contract=contract,
        )
        self._replace_account_metadata(
            credential=credential,
            token_result=token_result,
            contract=contract,
        )
        self.record_usage_event(
            credential=credential,
            provider_key=credential.provider_key,
            operation="auth.callback",
            status="connected",
            metadata_json={},
            project_id=state_row.attach_project_id,
        )
        if state_row.attach_project_id is not None and credential.id is not None:
            from stackos.db.models import ProjectCredential

            attached = self._s.exec(
                select(ProjectCredential).where(
                    col(ProjectCredential.project_id) == state_row.attach_project_id,
                    col(ProjectCredential.credential_id) == credential.id,
                )
            ).first()
            if attached is None:
                self._s.add(
                    ProjectCredential(
                        project_id=state_row.attach_project_id,
                        credential_id=credential.id,
                        attached_by="oauth-callback",
                    )
                )
        self._s.commit()
        return OAuthCallbackOut(
            attach_project_id=state_row.attach_project_id,
            return_surface=state_row.return_surface,
            provider_key=state_row.provider_key,
            credential_ref=credential.credential_ref,
            status="connected",
        )

    def _finish_failed_attempt(
        self,
        *,
        row: IntegrationCredential,
        credential: Credential,
        payload: dict[str, Any],
        expected_updated_at: Any,
        outcome: str,
    ) -> str:
        payload = dict(payload)
        payload.pop("_oauth_pending", None)
        active = any(payload.get(key) for key in ("access_token", "refresh_token", "value"))
        connection_status = "connected" if active else "repair-required"
        safe_config = dict(credential.config_json or {})
        safe_config.pop("oauth_pending", None)
        safe_config["oauth_connection_status"] = connection_status
        if not self._cas_profile_update(
            row=row,
            credential=credential,
            expected_updated_at=expected_updated_at,
            payload=payload,
            safe_config=safe_config,
            expires_at=credential.expires_at,
        ):
            return "stale-attempt"
        credential.status = connection_status
        credential.config_json = self._safe_config(safe_config)
        credential.updated_at = utcnow()
        self._s.add(credential)
        self.record_usage_event(
            credential=credential,
            provider_key=credential.provider_key,
            operation="auth.callback",
            status=outcome,
            metadata_json={},
        )
        self._s.commit()
        return outcome

    def _cas_profile_update(
        self,
        *,
        row: IntegrationCredential,
        credential: Credential,
        expected_updated_at: Any,
        payload: dict[str, Any],
        safe_config: dict[str, Any],
        expires_at: Any,
    ) -> bool:
        assert row.id is not None
        ciphertext, nonce = encrypt_account(
            self._encode_payload(payload),
            credential_ref=credential.credential_ref,
            provider_key=credential.provider_key,
        )
        now = utcnow()
        result = self._s.exec(
            update(IntegrationCredential)
            .where(
                col(IntegrationCredential.id) == row.id,
                col(IntegrationCredential.updated_at) == expected_updated_at,
            )
            .values(
                encrypted_payload=ciphertext,
                nonce=nonce,
                updated_at=now,
            )
        )
        if result.rowcount != 1:  # type: ignore[union-attr]
            self._s.rollback()
            return False
        return True

    def consume_oauth_state(self, *, state: str) -> OAuthState | None:
        digest = self._state_digest(state)
        now = utcnow()
        result = self._s.exec(
            update(OAuthState)
            .where(
                col(OAuthState.state) == digest,
                col(OAuthState.consumed_at).is_(None),
                or_(col(OAuthState.expires_at).is_(None), col(OAuthState.expires_at) > now),
            )
            .values(consumed_at=now)
        )
        if result.rowcount != 1:  # type: ignore[union-attr]
            self._s.rollback()
            return None
        self._s.commit()
        return self._s.exec(select(OAuthState).where(col(OAuthState.state) == digest)).first()

    def _credential_for_oauth_state(
        self,
        *,
        state_row: OAuthState,
        row: IntegrationCredential,
    ) -> Credential:
        credential = None
        if state_row.credential_id is not None:
            credential = self._s.get(Credential, state_row.credential_id)
        if credential is None:
            credential = self._s.exec(
                select(Credential).where(
                    col(Credential.integration_credential_id) == state_row.integration_credential_id
                )
            ).first()
        if credential is None or credential.integration_credential_id != row.id:
            raise ConflictError("OAuth transaction credential binding is invalid")
        if state_row.provider_key != credential.provider_key:
            raise ConflictError("OAuth transaction binding does not match credential")
        return credential

    def _replace_scopes(
        self,
        *,
        credential: Credential,
        token_result: TokenResult,
        fallback_scopes: tuple[str, ...] | None,
        contract: OAuthProviderContract,
    ) -> None:
        assert credential.id is not None
        scopes = token_result.scopes
        if scopes is None:
            if fallback_scopes is None:
                return
            scopes = fallback_scopes
        self._s.exec(
            delete(CredentialScope).where(col(CredentialScope.credential_id) == credential.id)
        )
        for scope in sorted(set(scopes)):
            self._s.add(CredentialScope(credential_id=credential.id, scope=scope))

    @staticmethod
    def _required_scopes_from_pending(
        *,
        payload: dict[str, Any],
        default: tuple[str, ...],
    ) -> tuple[str, ...]:
        pending = payload.get("_oauth_pending")
        raw = pending.get("required_scopes") if isinstance(pending, dict) else None
        if isinstance(raw, list) and all(isinstance(scope, str) for scope in raw):
            return tuple(scope for scope in raw if scope)
        return default

    def _replace_account_metadata(
        self,
        *,
        credential: Credential,
        token_result: TokenResult,
        contract: OAuthProviderContract,
    ) -> None:
        assert credential.id is not None
        metadata = dict(token_result.metadata)
        if not metadata:
            return
        self._s.exec(
            delete(CredentialAccount).where(col(CredentialAccount.credential_id) == credential.id)
        )
        provider_account_id = token_result.account_id
        self._s.add(
            CredentialAccount(
                credential_id=credential.id,
                provider_account_id=(
                    str(provider_account_id) if provider_account_id is not None else None
                ),
                metadata_json=metadata,
            )
        )

    def _oauth_payload(self, row: IntegrationCredential) -> dict[str, Any]:
        assert row.id is not None
        try:
            decoded = json.loads(
                IntegrationCredentialRepository(self._s).get_decrypted(row.id).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConflictError("OAuth credential payload is not valid JSON") from exc
        if not isinstance(decoded, dict):
            raise ConflictError("OAuth credential payload must be an object")
        return decoded

    @staticmethod
    def _encode_payload(payload: dict[str, Any]) -> bytes:
        return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    @staticmethod
    def _state_digest(state: str) -> str:
        return hashlib.sha256(state.encode("utf-8")).hexdigest()

    @staticmethod
    def _pkce_challenge(verifier: str) -> str:
        return (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
