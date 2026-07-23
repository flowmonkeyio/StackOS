"""Trusted, decision-free OAuth protocol contracts for executable providers."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal

from stackos.repositories.base import ValidationError

OAuthFlow = Literal["authorization_code", "client_credentials"]
ClientAuthStyle = Literal["body", "basic"]
PKCEMode = Literal["required", "supported", "unavailable"]
OAuthTokenResponseRequirement = Literal["refresh_token", "expires_in", "scope_evidence"]


@dataclass(frozen=True)
class OAuthProviderContract:
    provider_key: str
    flow: OAuthFlow
    token_endpoint: str
    authorization_endpoint: str | None = None
    include_response_type: bool = True
    scopes: tuple[str, ...] = ()
    scope_separator: str = " "
    optional_scope_parameter: str | None = None
    client_auth_style: ClientAuthStyle = "body"
    pkce_mode: PKCEMode = "unavailable"
    authorization_params: tuple[tuple[str, str], ...] = ()
    token_params: tuple[tuple[str, str], ...] = ()
    response_metadata_fields: tuple[str, ...] = ()
    response_scope_fields: tuple[str, ...] = ("scope",)
    response_account_id_field: str | None = "id"
    authorization_code_response_requirements: tuple[OAuthTokenResponseRequirement, ...] = ()
    refresh_token_response_requirements: tuple[OAuthTokenResponseRequirement, ...] = ()
    hook: str | None = None


_GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"

_CONTRACTS: dict[str, OAuthProviderContract] = {
    "google-ads": OAuthProviderContract(
        provider_key="google-ads",
        flow="authorization_code",
        authorization_endpoint=_GOOGLE_AUTH,
        token_endpoint=_GOOGLE_TOKEN,
        scopes=("https://www.googleapis.com/auth/adwords",),
        pkce_mode="supported",
        authorization_params=(
            ("access_type", "offline"),
            ("include_granted_scopes", "true"),
            ("prompt", "consent"),
        ),
    ),
    "google-workspace": OAuthProviderContract(
        provider_key="google-workspace",
        flow="authorization_code",
        authorization_endpoint=_GOOGLE_AUTH,
        token_endpoint=_GOOGLE_TOKEN,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/calendar.events",
        ),
        pkce_mode="supported",
        authorization_params=(
            ("access_type", "offline"),
            ("include_granted_scopes", "true"),
            ("prompt", "consent"),
        ),
    ),
    "google-search-console": OAuthProviderContract(
        provider_key="google-search-console",
        flow="authorization_code",
        authorization_endpoint=_GOOGLE_AUTH,
        token_endpoint=_GOOGLE_TOKEN,
        scopes=("https://www.googleapis.com/auth/webmasters.readonly",),
        pkce_mode="supported",
        authorization_params=(
            ("access_type", "offline"),
            ("include_granted_scopes", "true"),
            ("prompt", "consent"),
        ),
    ),
    "google-analytics": OAuthProviderContract(
        provider_key="google-analytics",
        flow="authorization_code",
        authorization_endpoint=_GOOGLE_AUTH,
        token_endpoint=_GOOGLE_TOKEN,
        scopes=("https://www.googleapis.com/auth/analytics.readonly",),
        pkce_mode="supported",
        authorization_params=(
            ("access_type", "offline"),
            ("include_granted_scopes", "true"),
            ("prompt", "consent"),
        ),
    ),
    "google-tag-manager": OAuthProviderContract(
        provider_key="google-tag-manager",
        flow="authorization_code",
        authorization_endpoint=_GOOGLE_AUTH,
        token_endpoint=_GOOGLE_TOKEN,
        scopes=("https://www.googleapis.com/auth/tagmanager.readonly",),
        pkce_mode="supported",
        authorization_params=(
            ("access_type", "offline"),
            ("include_granted_scopes", "true"),
            ("prompt", "consent"),
        ),
    ),
    "meta-ads": OAuthProviderContract(
        provider_key="meta-ads",
        flow="authorization_code",
        authorization_endpoint="https://www.facebook.com/v25.0/dialog/oauth",
        token_endpoint="https://graph.facebook.com/v25.0/oauth/access_token",
        scopes=("ads_management", "ads_read", "business_management"),
        pkce_mode="unavailable",
        hook="meta-long-lived",
    ),
    "salesforce": OAuthProviderContract(
        provider_key="salesforce",
        flow="authorization_code",
        authorization_endpoint="https://login.salesforce.com/services/oauth2/authorize",
        token_endpoint="https://login.salesforce.com/services/oauth2/token",
        scopes=("api", "refresh_token"),
        pkce_mode="required",
        response_metadata_fields=("instance_url", "id"),
    ),
    "pipedrive": OAuthProviderContract(
        provider_key="pipedrive",
        flow="authorization_code",
        authorization_endpoint="https://oauth.pipedrive.com/oauth/authorize",
        token_endpoint="https://oauth.pipedrive.com/oauth/token",
        include_response_type=False,
        client_auth_style="basic",
        pkce_mode="unavailable",
        response_metadata_fields=("api_domain",),
    ),
    "outreach": OAuthProviderContract(
        provider_key="outreach",
        flow="authorization_code",
        authorization_endpoint="https://api.outreach.io/oauth/authorize",
        token_endpoint="https://api.outreach.io/oauth/token",
        scopes=("sequenceStates.write",),
        pkce_mode="unavailable",
    ),
    "salesloft": OAuthProviderContract(
        provider_key="salesloft",
        flow="authorization_code",
        authorization_endpoint="https://accounts.salesloft.com/oauth/authorize",
        token_endpoint="https://accounts.salesloft.com/oauth/token",
        pkce_mode="unavailable",
    ),
    "microsoft-365": OAuthProviderContract(
        provider_key="microsoft-365",
        flow="authorization_code",
        authorization_endpoint=("https://login.microsoftonline.com/common/oauth2/v2.0/authorize"),
        token_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        scopes=(
            "offline_access",
            "https://graph.microsoft.com/Mail.Send",
            "https://graph.microsoft.com/Calendars.ReadWrite",
        ),
        pkce_mode="required",
        authorization_params=(("response_mode", "query"),),
    ),
    "hubspot": OAuthProviderContract(
        provider_key="hubspot",
        flow="authorization_code",
        authorization_endpoint="https://app.hubspot.com/oauth/authorize",
        token_endpoint="https://api.hubspot.com/oauth/2026-03/token",
        optional_scope_parameter="optional_scope",
        client_auth_style="body",
        pkce_mode="unavailable",
        response_metadata_fields=(
            "hub_id",
            "user_id",
            "app_id",
            "hub_domain",
            "is_private_distribution",
        ),
        response_scope_fields=("scopes", "scope"),
        response_account_id_field="hub_id",
        authorization_code_response_requirements=(
            "refresh_token",
            "expires_in",
            "scope_evidence",
        ),
        refresh_token_response_requirements=(
            "refresh_token",
            "expires_in",
        ),
    ),
    "taboola": OAuthProviderContract(
        provider_key="taboola",
        flow="client_credentials",
        token_endpoint="https://backstage.taboola.com/backstage/oauth/token",
        client_auth_style="body",
    ),
    "reddit": OAuthProviderContract(
        provider_key="reddit",
        flow="client_credentials",
        token_endpoint="https://www.reddit.com/api/v1/access_token",
        client_auth_style="basic",
    ),
}

_TENANT_RE = re.compile(r"^(?:common|organizations|consumers|[0-9a-fA-F-]{36}|[A-Za-z0-9.-]+)$")
_SALESFORCE_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.my\.salesforce\.com$")


def oauth_contract_for(
    provider_key: str,
    *,
    safe_config: dict[str, object] | None = None,
) -> OAuthProviderContract:
    """Return a trusted provider contract with validated endpoint variants."""

    try:
        contract = _CONTRACTS[provider_key]
    except KeyError as exc:
        raise ValidationError(
            "provider has no daemon OAuth contract",
            data={"provider_key": provider_key},
        ) from exc
    config = safe_config or {}
    if provider_key == "microsoft-365":
        tenant = str(config.get("tenant") or "common").strip()
        if not _TENANT_RE.fullmatch(tenant) or ".." in tenant:
            raise ValidationError(
                "Microsoft tenant must be common, organizations, consumers, a tenant id, "
                "or a verified tenant domain",
                data={"provider_key": provider_key, "field": "tenant"},
            )
        base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
        contract = replace(
            contract,
            authorization_endpoint=f"{base}/authorize",
            token_endpoint=f"{base}/token",
        )
    elif provider_key == "salesforce":
        environment = str(config.get("environment") or "production").strip().lower()
        login_host = "login.salesforce.com"
        if environment == "sandbox":
            login_host = "test.salesforce.com"
        elif environment == "my-domain":
            candidate = str(config.get("login_domain") or "").strip().lower()
            if not _SALESFORCE_DOMAIN_RE.fullmatch(candidate):
                raise ValidationError(
                    "Salesforce My Domain must end in .my.salesforce.com",
                    data={"provider_key": provider_key, "field": "login_domain"},
                )
            login_host = candidate
        elif environment != "production":
            raise ValidationError(
                "Salesforce environment must be production, sandbox, or my-domain",
                data={"provider_key": provider_key, "field": "environment"},
            )
        base = f"https://{login_host}/services/oauth2"
        contract = replace(
            contract,
            authorization_endpoint=f"{base}/authorize",
            token_endpoint=f"{base}/token",
        )
    return contract


def oauth_provider_keys() -> frozenset[str]:
    return frozenset(_CONTRACTS)


__all__ = ["OAuthProviderContract", "oauth_contract_for", "oauth_provider_keys"]
