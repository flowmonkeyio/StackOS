"""QuickBooks company binding and fresh OAuth grant evidence, using synthetic accounts."""

import asyncio
import json
from urllib.parse import parse_qs, urlparse

import pytest
from sqlmodel import select

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.db.models import Credential, CredentialScope, OAuthState
from stackos.plugins.manifest import BUILTIN_PLUGIN_MANIFESTS
from stackos.repositories.base import ConflictError, NotFoundError
from stackos.repositories.projects import IntegrationCredentialRepository

PROVIDER = "quickbooks-online"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"


def test_quickbooks_manifest_uses_canonical_setup_and_exact_read_surface():
    finance = next(item for item in BUILTIN_PLUGIN_MANIFESTS if item.slug == "finance")
    provider = next(item for item in finance.providers if item.key == PROVIDER)
    methods = {method.key: method for method in provider.auth_methods}
    assert set(methods) == {"oauth2_authorization_code", "oauth2_token"}
    assert methods["oauth2_authorization_code"].label == "Connect with QuickBooks"
    assert [field.key for field in methods["oauth2_authorization_code"].fields] == [
        "client_id",
        "client_secret",
        "environment",
        "realm_id",
    ]
    assert [field.key for field in methods["oauth2_token"].fields] == [
        "access_token",
        "environment",
        "realm_id",
    ]
    assert {action.key for action in finance.actions if action.provider == PROVIDER} == {
        "quickbooks-online.company-info.get",
        "quickbooks-online.invoices.list",
    }


def create_account(repo, project_id=None, method="oauth2_authorization_code"):
    return repo.store_credential(
        provider_key=PROVIDER,
        auth_method_key=method,
        display_name="Synthetic company",
        attach_project_id=project_id,
        fields={
            "environment": "sandbox",
            "realm_id": "001234",
            **(
                {"client_id": "synthetic-client", "client_secret": "synthetic-secret"}
                if method == "oauth2_authorization_code"
                else {"access_token": "synthetic-access"}
            ),
        },
    ).data.credential_ref


def start(repo, ref, settings):
    result = repo.start(
        provider_key=PROVIDER,
        auth_method_key="oauth2_authorization_code",
        credential_ref=ref,
        settings=settings,
    ).data
    return parse_qs(urlparse(result.authorization_url).query)["state"][0]


def token_response(httpx_mock, scope=None):
    data = {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
    if scope is not None:
        data["scope"] = scope
    httpx_mock.add_response(method="POST", url=TOKEN_URL, json=data)


def account_row(session, ref):
    return session.exec(select(Credential).where(Credential.credential_ref == ref)).one()


def payload(session, row):
    return json.loads(
        IntegrationCredentialRepository(session).get_decrypted(row.integration_credential_id)
    )


@pytest.mark.parametrize("scope", [None, "", "com.intuit.quickbooks.accounting"])
def test_fresh_grant_records_only_returned_scopes_and_clears_previous_grants(
    session, project_id, settings, httpx_mock, scope
):
    repo = AuthRepository(session)
    ref = create_account(repo, project_id)
    row = account_row(session, ref)
    session.add(CredentialScope(credential_id=row.id, scope="old.permission"))
    session.commit()
    state = start(repo, ref, settings)
    assert payload(session, row)["_oauth_pending"]["realm_id"] == "001234"
    assert payload(session, row)["_oauth_pending"]["environment"] == "sandbox"
    token_response(httpx_mock, scope)
    result = asyncio.run(
        repo.complete_oauth_callback(
            state=state, code="one-code", realm_ids=["001234"], settings=settings
        )
    )
    assert result.status == "connected"
    session.refresh(row)
    assert row.config_json["scope_status"] == ("unknown" if scope is None else "known")
    assert [r.scope for r in session.exec(select(CredentialScope)).all()] == (
        [scope] if scope else []
    )
    assert payload(session, row)["access_token"] == "new-access"
    assert "_oauth_pending" not in payload(session, row)
    with pytest.raises(ConflictError):
        asyncio.run(
            repo.complete_oauth_callback(
                state=state, code="replay", realm_ids=["001234"], settings=settings
            )
        )
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize(
    "realms",
    [None, [], [""], ["abc"], ["\uff11\uff12\uff13"], ["1" * 33], ["1234"], ["001234", "001234"]],
)
@pytest.mark.parametrize("reconnect", [False, True])
def test_realm_rejection_precedes_exchange_and_preserves_usable_reconnect(
    session, project_id, settings, httpx_mock, realms, reconnect
):
    repo = AuthRepository(session)
    ref = create_account(repo, project_id)
    row = account_row(session, ref)
    if reconnect:
        state = start(repo, ref, settings)
        token_response(httpx_mock, "com.intuit.quickbooks.accounting")
        assert (
            asyncio.run(
                repo.complete_oauth_callback(
                    state=state, code="first", realm_ids=["001234"], settings=settings
                )
            ).status
            == "connected"
        )
    state = start(repo, ref, settings)
    before = payload(session, row)
    result = asyncio.run(
        repo.complete_oauth_callback(
            state=state, code="wrong-company", realm_ids=realms, settings=settings
        )
    )
    assert result.status == "repair-required"
    session.refresh(row)
    assert row.status == ("connected" if reconnect else "repair-required")
    after = payload(session, row)
    assert "_oauth_pending" not in after
    if reconnect:
        assert after["access_token"] == before["access_token"]
        assert after["refresh_token"] == before["refresh_token"]
        assert [r.scope for r in session.exec(select(CredentialScope)).all()] == [
            "com.intuit.quickbooks.accounting"
        ]
    assert len(httpx_mock.get_requests()) == int(reconnect)


@pytest.mark.parametrize("change", [{"realm_id": "999"}, {"environment": "production"}])
def test_changed_company_config_cannot_complete_old_consent(session, settings, httpx_mock, change):
    repo = AuthRepository(session)
    ref = create_account(repo)
    state = start(repo, ref, settings)
    row = account_row(session, ref)
    row.config_json = {**row.config_json, **change}
    session.add(row)
    session.commit()
    result = asyncio.run(
        repo.complete_oauth_callback(
            state=state, code="old-consent", realm_ids=["001234"], settings=settings
        )
    )
    assert result.status == "repair-required"
    assert not httpx_mock.get_requests()
    assert session.exec(select(OAuthState)).one().consumed_at is not None


@pytest.mark.parametrize("method", ["oauth2_authorization_code", "oauth2_token"])
def test_catalog_and_project_attachment_enforced(session, project_id, settings, httpx_mock, method):
    repo = AuthRepository(session)
    ref = create_account(repo, method=method)
    if method == "oauth2_authorization_code":
        state = start(repo, ref, settings)
        token_response(httpx_mock)
        assert (
            asyncio.run(
                repo.complete_oauth_callback(
                    state=state, code="code", realm_ids=["001234"], settings=settings
                )
            ).status
            == "connected"
        )
    actions = ActionRepository(session)
    for key in ("company-info.get", "invoices.list"):
        described = actions.describe(action_ref=f"finance.quickbooks-online.{key}")
        assert described.manifest.risk_level == "read"
        assert not described.manifest.required_scopes
    with pytest.raises(NotFoundError, match="not attached"):
        asyncio.run(
            repo.resolve_for_execution(
                project_id=project_id,
                provider_key=PROVIDER,
                credential_ref=ref,
                operation="company-info.get",
                required_scopes=[],
            )
        )
