from __future__ import annotations

import pytest

from stackos.auth_providers.oauth_contracts import (
    oauth_contract_for,
    oauth_provider_keys,
)
from stackos.repositories.base import ValidationError

EXPECTED_CONTRACTS = {
    "google-ads": ("authorization_code", "supported", "body"),
    "google-workspace": ("authorization_code", "supported", "body"),
    "google-search-console": ("authorization_code", "supported", "body"),
    "google-analytics": ("authorization_code", "supported", "body"),
    "google-tag-manager": ("authorization_code", "supported", "body"),
    "meta-ads": ("authorization_code", "unavailable", "body"),
    "salesforce": ("authorization_code", "required", "body"),
    "pipedrive": ("authorization_code", "unavailable", "basic"),
    "outreach": ("authorization_code", "unavailable", "body"),
    "salesloft": ("authorization_code", "unavailable", "body"),
    "microsoft-365": ("authorization_code", "required", "body"),
    "taboola": ("client_credentials", "unavailable", "body"),
    "reddit": ("client_credentials", "unavailable", "basic"),
}


@pytest.mark.parametrize(
    ("provider_key", "expected"),
    sorted(EXPECTED_CONTRACTS.items()),
)
def test_oauth_provider_contracts_declare_one_trusted_flow(
    provider_key: str,
    expected: tuple[str, str, str],
) -> None:
    contract = oauth_contract_for(provider_key)

    assert (contract.flow, contract.pkce_mode, contract.client_auth_style) == expected
    assert contract.token_endpoint.startswith("https://")
    if contract.flow == "authorization_code":
        assert contract.authorization_endpoint is not None
        assert contract.authorization_endpoint.startswith("https://")
    else:
        assert contract.authorization_endpoint is None


def test_oauth_provider_inventory_and_real_variants_are_explicit() -> None:
    assert oauth_provider_keys() == frozenset(EXPECTED_CONTRACTS)

    assert oauth_contract_for("meta-ads").hook == "meta-long-lived"
    assert oauth_contract_for("pipedrive").include_response_type is False
    assert oauth_contract_for("pipedrive").response_metadata_fields == ("api_domain",)
    assert oauth_contract_for("salesforce").response_metadata_fields == (
        "instance_url",
        "id",
    )
    assert oauth_contract_for("outreach").scopes == ("sequenceStates.write",)


def test_google_family_shares_endpoints_but_keeps_provider_scopes() -> None:
    provider_keys = (
        "google-ads",
        "google-workspace",
        "google-search-console",
        "google-analytics",
        "google-tag-manager",
    )
    contracts = [oauth_contract_for(key) for key in provider_keys]

    assert {contract.authorization_endpoint for contract in contracts} == {
        "https://accounts.google.com/o/oauth2/v2/auth"
    }
    assert {contract.token_endpoint for contract in contracts} == {
        "https://oauth2.googleapis.com/token"
    }
    assert all(
        ("access_type", "offline") in contract.authorization_params for contract in contracts
    )
    assert {contract.scopes for contract in contracts} == {
        ("https://www.googleapis.com/auth/adwords",),
        (
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/calendar.events",
        ),
        ("https://www.googleapis.com/auth/webmasters.readonly",),
        ("https://www.googleapis.com/auth/analytics.readonly",),
        ("https://www.googleapis.com/auth/tagmanager.readonly",),
    }


def test_salesforce_contract_validates_environment_owned_hosts() -> None:
    assert (
        oauth_contract_for("salesforce", safe_config={"environment": "sandbox"}).token_endpoint
        == "https://test.salesforce.com/services/oauth2/token"
    )
    assert (
        oauth_contract_for(
            "salesforce",
            safe_config={
                "environment": "my-domain",
                "login_domain": "example.my.salesforce.com",
            },
        ).authorization_endpoint
        == "https://example.my.salesforce.com/services/oauth2/authorize"
    )

    with pytest.raises(ValidationError):
        oauth_contract_for(
            "salesforce",
            safe_config={"environment": "my-domain", "login_domain": "example.com"},
        )
    with pytest.raises(ValidationError):
        oauth_contract_for("salesforce", safe_config={"environment": "staging"})


def test_microsoft_contract_validates_tenant_path_segment() -> None:
    contract = oauth_contract_for(
        "microsoft-365",
        safe_config={"tenant": "contoso.onmicrosoft.com"},
    )

    assert contract.authorization_endpoint == (
        "https://login.microsoftonline.com/contoso.onmicrosoft.com/oauth2/v2.0/authorize"
    )
    assert contract.token_endpoint == (
        "https://login.microsoftonline.com/contoso.onmicrosoft.com/oauth2/v2.0/token"
    )

    with pytest.raises(ValidationError):
        oauth_contract_for("microsoft-365", safe_config={"tenant": "../common"})


def test_provider_without_core_oauth_contract_is_rejected() -> None:
    with pytest.raises(ValidationError, match="no daemon OAuth contract"):
        oauth_contract_for("hubspot")
