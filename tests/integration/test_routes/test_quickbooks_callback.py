"""The public local callback retains exact, bounded QuickBooks realm values."""

from urllib.parse import parse_qs, urlparse

import pytest

from .test_auth_provider_routes import _create_account


@pytest.mark.parametrize("realms", [[], ["1234", "1234"], ["abc"], ["9999"], ["1234"]])
def test_callback_realm_validation_precedes_token_exchange(api, httpx_mock, realms):
    account = _create_account(
        api,
        provider_key="quickbooks-online",
        display_name="Synthetic QBO",
        auth_method_key="oauth2_authorization_code",
        fields={
            "client_id": "synthetic-client",
            "client_secret": "synthetic-secret",
            "environment": "sandbox",
            "realm_id": "1234",
        },
    )
    started = api.post(
        "/api/v1/auth/accounts/quickbooks-online/start",
        json={
            "credential_ref": account["credential_ref"],
            "auth_method_key": "oauth2_authorization_code",
        },
    )
    assert started.status_code == 200, started.text
    state = parse_qs(urlparse(started.json()["data"]["authorization_url"]).query)["state"][0]
    valid = realms == ["1234"]
    if valid:
        httpx_mock.add_response(
            method="POST",
            url="https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
            json={
                "access_token": "synthetic-access",
                "refresh_token": "synthetic-refresh",
                "expires_in": 3600,
            },
        )
    authorization = api.headers.pop("authorization")
    try:
        result = api.get(
            "/api/v1/auth/oauth/callback",
            follow_redirects=False,
            params=[
                ("state", state),
                ("code", "synthetic-code"),
                *(("realmId", value) for value in realms),
            ],
        )
    finally:
        api.headers["authorization"] = authorization
    assert result.status_code == 303, result.text
    assert (
        "oauth_status=connected" if valid else "oauth_status=repair-required"
    ) in result.headers["location"]
    assert len(httpx_mock.get_requests()) == int(valid)
    assert "realmId" not in result.headers["location"]
    assert state not in result.headers["location"]
    assert "synthetic-code" not in result.headers["location"]
