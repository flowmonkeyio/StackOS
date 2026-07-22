"""Core daemon-owned OAuth authorization transaction lifecycle."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import unquote_plus, urlencode, urlparse

import httpx
from sqlalchemy import delete, or_, update
from sqlmodel import col, select

from stackos.auth_providers.oauth_contracts import OAuthProviderContract, oauth_contract_for
from stackos.config import Settings
from stackos.crypto.aes_gcm import encrypt
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

_MAX_TOKEN_RESPONSE_BYTES = 1_000_000
_RETRYABLE_OAUTH_ERRORS = {"server_error", "slow_down", "temporarily_unavailable"}


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

    def start(
        self,
        *,
        project_id: int,
        provider_key: str,
        settings: Settings,
        auth_method_key: str | None = None,
        credential_ref: str | None = None,
    ) -> Envelope[AuthStartOut]:
        self._require_project(project_id)
        provider = self._get_provider(provider_key)
        assert provider is not None
        self._require_provider_enabled_for_project(project_id=project_id, provider=provider)
        method = self._get_auth_method(provider, auth_method_key)
        assert method is not None
        setup_required = provider.auth_type not in {"none", "local"}
        if not method.interactive:
            return Envelope(
                data=AuthStartOut(
                    project_id=project_id,
                    provider_key=provider.key,
                    auth_type=method.auth_type,
                    auth_method_key=method.key,
                    status=("requires-local-credential" if setup_required else "not-required"),
                    setup_url=(
                        self._local_setup_url(
                            settings=settings,
                            project_id=project_id,
                            provider_key=provider_key,
                        )
                        if setup_required
                        else None
                    ),
                ),
                project_id=project_id,
            )
        if credential_ref is None:
            raise ValidationError(
                "credential_ref is required for an interactive auth method",
                data={"provider_key": provider_key, "auth_method_key": method.key},
            )
        credential, row = self._resolve_credential(
            project_id=project_id,
            credential_ref=credential_ref,
        )
        if row.kind != provider_key:
            raise ValidationError(
                "credential provider does not match auth provider",
                data={
                    "credential_ref": credential_ref,
                    "credential_provider": row.kind,
                    "provider_key": provider_key,
                },
            )
        configured_method = (row.config_json or {}).get("auth_method_key")
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
            raise ConflictError(
                "interactive provider application configuration is missing",
                data={"credential_ref": credential_ref, "provider_key": provider_key},
            )
        client_id = application.get("client_id") or (row.config_json or {}).get("client_id")
        if not isinstance(client_id, str) or not client_id.strip():
            raise ConflictError(
                "interactive provider application id is missing",
                data={"credential_ref": credential_ref, "provider_key": provider_key},
            )
        contract = oauth_contract_for(provider_key, safe_config=row.config_json)
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
        query: dict[str, str] = {
            "client_id": client_id.strip(),
            "redirect_uri": redirect_uri,
            "state": raw_state,
        }
        if contract.include_response_type:
            query["response_type"] = "code"
        scopes = tuple(provider.scopes_json or ()) or contract.scopes
        if scopes:
            query["scope"] = contract.scope_separator.join(scopes)
        query.update(dict(contract.authorization_params))
        if contract.pkce_mode != "unavailable":
            verifier = secrets.token_urlsafe(64)
            query["code_challenge"] = self._pkce_challenge(verifier)
            query["code_challenge_method"] = "S256"

        payload["_oauth_pending"] = {
            "code_verifier": verifier,
        }
        safe_config = dict(row.config_json or {})
        safe_config["oauth_pending"] = True
        if safe_config.get("oauth_connection_status") != "connected":
            safe_config["oauth_connection_status"] = "pending"
        assert row.id is not None
        IntegrationCredentialRepository(self._s).set(
            project_id=row.project_id,
            kind=row.kind,
            profile_key=row.profile_key,
            secret_payload=self._encode_payload(payload),
            config_json=safe_config,
            expires_at=row.expires_at,
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
            project_id=project_id,
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
        credential.updated_at = now
        self._s.add(credential)
        self.record_usage_event(
            credential=credential,
            provider_key=provider_key,
            operation="auth.start",
            status="authorization-pending",
            metadata_json={"auth_method_key": method.key, "profile_key": row.profile_key},
        )
        self._s.commit()
        authorization_url = f"{contract.authorization_endpoint}?{urlencode(query)}"
        return Envelope(
            data=AuthStartOut(
                project_id=project_id,
                provider_key=provider_key,
                auth_type=method.auth_type,
                auth_method_key=method.key,
                status="authorization-pending",
                authorization_url=authorization_url,
                redirect_uri=redirect_uri,
                credential_ref=credential.credential_ref,
                expires_at=expires_at,
            ),
            project_id=project_id,
        )

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
            raise NotFoundError("OAuth credential profile no longer exists")
        credential = self._credential_for_oauth_state(state_row=state_row, row=row)
        payload = self._oauth_payload(row)
        pending = payload.get("_oauth_pending")
        application = payload.get("_oauth_application_pending")
        if not isinstance(pending, dict) or not isinstance(application, dict):
            raise ConflictError("OAuth transaction is stale")
        expected_updated_at = row.updated_at
        contract = oauth_contract_for(row.kind, safe_config=row.config_json)
        if provider_error is not None:
            status = self._finish_failed_attempt(
                row=row,
                credential=credential,
                payload=payload,
                expected_updated_at=expected_updated_at,
                outcome="authorization-denied",
            )
            return OAuthCallbackOut(
                project_id=state_row.project_id,
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
                project_id=state_row.project_id,
                provider_key=state_row.provider_key,
                credential_ref=credential.credential_ref,
                status=status,
            )
        try:
            response_body = await self._exchange_authorization_code(
                contract=contract,
                application=application,
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
                project_id=state_row.project_id,
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
            response_body=response_body,
            expected_updated_at=expected_updated_at,
        )

    async def _exchange_authorization_code(
        self,
        *,
        contract: OAuthProviderContract,
        application: dict[str, Any],
        code: str,
        redirect_uri: str,
        code_verifier: object,
    ) -> dict[str, Any]:
        data: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }
        data.update(dict(contract.token_params))
        if isinstance(code_verifier, str) and code_verifier:
            data["code_verifier"] = code_verifier
        response_body = await self._post_oauth_token_request(
            contract=contract,
            application=application,
            data=data,
        )
        if contract.hook == "meta-long-lived":
            return await self._exchange_meta_long_lived_token(
                contract=contract,
                application=application,
                short_lived_token=str(response_body["access_token"]),
            )
        return response_body

    async def _exchange_meta_long_lived_token(
        self,
        *,
        contract: OAuthProviderContract,
        application: dict[str, Any],
        short_lived_token: str,
    ) -> dict[str, Any]:
        """Apply Meta's documented server-side short-to-long token exchange."""

        client_id = application.get("client_id")
        client_secret = application.get("client_secret")
        if not isinstance(client_id, str) or not client_id:
            raise ValidationError("OAuth application id is missing")
        if not isinstance(client_secret, str) or not client_secret:
            raise ValidationError("OAuth application private value is missing")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                contract.token_endpoint,
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "fb_exchange_token": short_lived_token,
                },
            )
        return self._oauth_token_response(response=response, contract=contract)

    async def _post_oauth_token_request(
        self,
        *,
        contract: OAuthProviderContract,
        application: dict[str, Any],
        data: dict[str, str],
    ) -> dict[str, Any]:
        """Post one trusted token request for any core-owned OAuth grant."""

        client_id = application.get("client_id")
        client_secret = application.get("client_secret")
        if not isinstance(client_id, str) or not client_id:
            raise ValidationError("OAuth application id is missing")
        if not isinstance(client_secret, str) or not client_secret:
            raise ValidationError("OAuth application private value is missing")
        request_data = dict(data)
        if contract.client_auth_style != "basic":
            request_data.update({"client_id": client_id, "client_secret": client_secret})
        headers = None
        user_agent = application.get("user_agent")
        if isinstance(user_agent, str) and user_agent.strip():
            headers = {"User-Agent": user_agent.strip()}
        async with httpx.AsyncClient(timeout=30.0) as client:
            if contract.client_auth_style == "basic":
                response = await client.post(
                    contract.token_endpoint,
                    data=request_data,
                    auth=httpx.BasicAuth(client_id, client_secret),
                    headers=headers,
                )
            else:
                response = await client.post(
                    contract.token_endpoint,
                    data=request_data,
                    headers=headers,
                )
        return self._oauth_token_response(response=response, contract=contract)

    def _oauth_token_response(
        self,
        *,
        response: httpx.Response,
        contract: OAuthProviderContract,
    ) -> dict[str, Any]:
        if len(response.content) > _MAX_TOKEN_RESPONSE_BYTES:
            raise OAuthTokenRequestError(
                "provider authorization response is too large",
                provider_key=contract.provider_key,
                retryable=True,
                repair_required=False,
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            error_code = ""
            try:
                error_body = response.json()
            except ValueError:
                error_body = None
            if isinstance(error_body, dict) and isinstance(error_body.get("error"), str):
                error_code = str(error_body["error"]).strip().lower()
            retryable = (
                response.status_code in {408, 425, 429}
                or response.status_code >= 500
                or error_code in _RETRYABLE_OAUTH_ERRORS
            )
            raise OAuthTokenRequestError(
                "provider authorization exchange failed",
                provider_key=contract.provider_key,
                retryable=retryable,
                repair_required=not retryable,
                status_code=response.status_code,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise OAuthTokenRequestError(
                "provider authorization response is not JSON",
                provider_key=contract.provider_key,
                retryable=True,
                repair_required=False,
                status_code=response.status_code,
            ) from exc
        if not isinstance(body, dict):
            raise OAuthTokenRequestError(
                "provider authorization response must be an object",
                provider_key=contract.provider_key,
                retryable=True,
                repair_required=False,
                status_code=response.status_code,
            )
        access_value = body.get("access_token")
        if not isinstance(access_value, str) or not access_value.strip():
            raise OAuthTokenRequestError(
                "provider authorization response is missing access material",
                provider_key=contract.provider_key,
                retryable=True,
                repair_required=False,
                status_code=response.status_code,
            )
        try:
            self._provider_response_config(contract=contract, response_body=body)
        except ValidationError as exc:
            raise OAuthTokenRequestError(
                "provider authorization response metadata is invalid",
                provider_key=contract.provider_key,
                retryable=True,
                repair_required=False,
                status_code=response.status_code,
            ) from exc
        return body

    def _finish_successful_exchange(
        self,
        *,
        state_row: OAuthState,
        row: IntegrationCredential,
        credential: Credential,
        payload: dict[str, Any],
        application: dict[str, Any],
        contract: OAuthProviderContract,
        response_body: dict[str, Any],
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
                "token_type",
            }
        }
        new_payload.update(application)
        new_payload["access_token"] = str(response_body["access_token"]).strip()
        refresh_value = response_body.get("refresh_token")
        if isinstance(refresh_value, str) and refresh_value.strip():
            new_payload["refresh_token"] = refresh_value.strip()
        safe_config = dict(row.config_json or {})
        safe_config.pop("oauth_pending", None)
        safe_config["oauth_connection_status"] = "connected"
        safe_config["scope_status"] = "known"
        safe_config.update(
            self._provider_response_config(contract=contract, response_body=response_body)
        )
        expires_at = None
        raw_expires_in = response_body.get("expires_in")
        if isinstance(raw_expires_in, (int, float)) and raw_expires_in > 0:
            expires_at = utcnow() + timedelta(seconds=float(raw_expires_in))
        if not self._cas_profile_update(
            row=row,
            expected_updated_at=expected_updated_at,
            payload=new_payload,
            safe_config=safe_config,
            expires_at=expires_at,
        ):
            return OAuthCallbackOut(
                project_id=state_row.project_id,
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
            response_body=response_body,
            fallback_scopes=contract.scopes,
        )
        self._replace_account_metadata(
            credential=credential,
            response_body=response_body,
            contract=contract,
        )
        self.record_usage_event(
            credential=credential,
            provider_key=row.kind,
            operation="auth.callback",
            status="connected",
            metadata_json={"profile_key": row.profile_key},
        )
        self._s.commit()
        return OAuthCallbackOut(
            project_id=state_row.project_id,
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
        safe_config = dict(row.config_json or {})
        safe_config.pop("oauth_pending", None)
        safe_config["oauth_connection_status"] = connection_status
        if not self._cas_profile_update(
            row=row,
            expected_updated_at=expected_updated_at,
            payload=payload,
            safe_config=safe_config,
            expires_at=row.expires_at,
        ):
            return "stale-attempt"
        credential.status = connection_status
        credential.config_json = self._safe_config(safe_config)
        credential.updated_at = utcnow()
        self._s.add(credential)
        self.record_usage_event(
            credential=credential,
            provider_key=row.kind,
            operation="auth.callback",
            status=outcome,
            metadata_json={"profile_key": row.profile_key},
        )
        self._s.commit()
        return outcome

    def _cas_profile_update(
        self,
        *,
        row: IntegrationCredential,
        expected_updated_at: Any,
        payload: dict[str, Any],
        safe_config: dict[str, Any],
        expires_at: Any,
    ) -> bool:
        assert row.id is not None
        ciphertext, nonce = encrypt(
            self._encode_payload(payload),
            project_id=row.project_id,
            kind=row.kind,
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
                config_json=safe_config,
                expires_at=expires_at,
                last_refreshed_at=now,
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
        if state_row.provider_key != row.kind or state_row.project_id != row.project_id:
            raise ConflictError("OAuth transaction binding does not match credential")
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
        return credential

    def _replace_scopes(
        self,
        *,
        credential: Credential,
        response_body: dict[str, Any],
        fallback_scopes: tuple[str, ...] | None,
    ) -> None:
        assert credential.id is not None
        raw_scopes = response_body.get("scope")
        if isinstance(raw_scopes, str):
            decoded_scopes = unquote_plus(raw_scopes.replace(",", " "))
            scopes = [scope for scope in decoded_scopes.split() if scope]
        elif isinstance(raw_scopes, list):
            scopes = [str(scope).strip() for scope in raw_scopes if str(scope).strip()]
        else:
            if fallback_scopes is None:
                return
            scopes = list(fallback_scopes)
        self._s.exec(
            delete(CredentialScope).where(col(CredentialScope.credential_id) == credential.id)
        )
        for scope in sorted(set(scopes)):
            self._s.add(CredentialScope(credential_id=credential.id, scope=scope))

    def _provider_response_config(
        self,
        *,
        contract: OAuthProviderContract,
        response_body: dict[str, Any],
    ) -> dict[str, str]:
        """Normalize only provider-returned execution bases used by connectors."""

        if contract.provider_key == "salesforce" and response_body.get("instance_url"):
            return {
                "instance_url": self._trusted_provider_base_url(
                    response_body["instance_url"],
                    provider_key="salesforce",
                    hostname_suffix=".salesforce.com",
                )
            }
        if contract.provider_key == "pipedrive" and response_body.get("api_domain"):
            return {
                "base_url": self._trusted_provider_base_url(
                    response_body["api_domain"],
                    provider_key="pipedrive",
                    hostname_suffix=".pipedrive.com",
                )
            }
        return {}

    @staticmethod
    def _trusted_provider_base_url(
        value: object,
        *,
        provider_key: str,
        hostname_suffix: str,
    ) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(
                "provider authorization response has an invalid execution base",
                data={"provider_key": provider_key},
            )
        parsed = urlparse(value.strip())
        hostname = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or not hostname.endswith(hostname_suffix)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValidationError(
                "provider authorization response has an untrusted execution base",
                data={"provider_key": provider_key},
            )
        return f"https://{parsed.netloc}"

    def _replace_account_metadata(
        self,
        *,
        credential: Credential,
        response_body: dict[str, Any],
        contract: OAuthProviderContract,
    ) -> None:
        assert credential.id is not None
        metadata = {
            field: response_body[field]
            for field in contract.response_metadata_fields
            if field in response_body and isinstance(response_body[field], (str, int, float, bool))
        }
        if not metadata:
            return
        self._s.exec(
            delete(CredentialAccount).where(col(CredentialAccount.credential_id) == credential.id)
        )
        provider_account_id = metadata.get("id")
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
