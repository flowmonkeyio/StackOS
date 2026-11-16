"""P06 real host Accounts; only the reviewed native declaration/probe is injected."""

from __future__ import annotations

import asyncio
import copy
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.db.models import (
    Action,
    Credential,
    CredentialAccount,
    CredentialScope,
    CredentialUsageEvent,
    IntegrationCredential,
    Plugin,
    Provider,
)
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository
from tests.integration.account_test_support import install_cfobooth_contract
from tests.integration.test_repositories import cfobooth_test_support as native

ORG = "73837d14-fbd6-40ca-81f4-19e2ce031fc9"
USER = "576bb550-1dcc-430b-bbf2-c3327f603027"
KEY_ID = "53761da2-37bc-40ab-afbb-489fb72a67a1"
CANARY = "s11-private-opaque-canary"
BASE = "http://localhost:3100"


def fields(**overrides):
    return {"api_base_url": BASE, "organization_id": ORG, "api_key": CANARY, **overrides}


def probe_result(**overrides):
    return copy.deepcopy(
        {
            "ok": True,
            "vendor": "cfobooth",
            "status": "ok",
            "api_base_url": BASE,
            "identity": {
                "keyId": KEY_ID,
                "organizationId": ORG,
                "user": {"id": USER, "email": "contract@example.test"},
                "expiresAt": None,
            },
            "metadata": {
                "evidence": {
                    "account": {
                        "provider_account_id": USER,
                        "display_name": "contract@example.test",
                        "metadata": {"organization_id": ORG, "key_id": KEY_ID, "expires_at": None},
                    }
                }
            },
            **overrides,
        }
    )


@pytest.fixture
def contract(session, monkeypatch):
    return install_cfobooth_contract(session, monkeypatch)


@pytest.mark.asyncio
async def test_unverified_account_persists_identity_without_grant_overlay(
    session: Session,
    project_id: int,
    monkeypatch,
    contract,
):
    repo = AuthRepository(session)
    method = repo.list_providers(provider_key="cfobooth")[0].auth_methods[0]
    assert method.permission_verification is None
    assert method.interactive is False
    assert {f.key for f in method.fields} == {"api_base_url", "organization_id", "api_key"}
    saved = repo.store_credential(
        provider_key="cfobooth",
        display_name="Local CFO",
        fields=fields(),
        attach_project_id=project_id,
    ).data
    assert saved.account is None
    assert saved.status == "pending"
    with pytest.raises(ConflictError, match="not connected"):
        await repo.resolve_for_execution(
            project_id=project_id,
            provider_key="cfobooth",
            credential_ref=saved.credential_ref,
            operation="action.run",
        )

    async def probe(connector, *, auth, **kwargs):
        assert connector == "cfobooth"
        assert dict(auth.fields) == {"api_key": CANARY}
        assert dict(auth.config) == {"api_base_url": BASE, "organization_id": ORG}
        return probe_result()

    monkeypatch.setattr("stackos_connectors.probe.probe_credentials", probe)
    result = (await repo.test(project_id=project_id, credential_ref=saved.credential_ref)).data
    assert result.ok is True
    account = repo.get_account(credential_ref=saved.credential_ref)
    assert account.account["provider_account_id"] == USER
    assert account.account["metadata_json"]["key_id"] == KEY_ID
    assert account.scopes == []


@pytest.mark.parametrize("missing", ["api_key", "api_base_url", "organization_id"])
def test_missing_inputs_never_create_custody(session, contract, missing):
    values = fields(**{missing: ""})
    with pytest.raises(ValidationError, match="missing"):
        AuthRepository(session).store_credential(
            provider_key="cfobooth", display_name="Incomplete", fields=values
        )
    assert session.exec(select(IntegrationCredential)).all() == []


@pytest.mark.parametrize(
    "outcome", ["failed", "malformed", "wrong-org", "missing-account", "blank-id", "string-ok"]
)
@pytest.mark.asyncio
async def test_failed_or_malformed_probe_cannot_establish_identity(
    session,
    project_id,
    monkeypatch,
    contract,
    outcome,
):
    repo = AuthRepository(session)
    saved = repo.store_credential(
        provider_key="cfobooth",
        display_name="Rejected",
        fields=fields(),
        attach_project_id=project_id,
    ).data
    raw = probe_result()
    if outcome == "failed":
        raw.update(ok=False, status="failed")
    elif outcome == "malformed":
        raw["metadata"]["evidence"]["account"]["provider_account_id"] = {"unsafe": "shape"}
    elif outcome == "wrong-org":
        raw["metadata"]["evidence"]["account"]["metadata"]["organization_id"] = "other-org"
    elif outcome == "missing-account":
        raw["metadata"]["evidence"] = {}
    elif outcome == "blank-id":
        raw["metadata"]["evidence"]["account"]["provider_account_id"] = " "
    else:
        raw["ok"] = "true"

    async def probe(*args, **kwargs):
        return raw

    monkeypatch.setattr("stackos_connectors.probe.probe_credentials", probe)
    tested = (await repo.test(project_id=project_id, credential_ref=saved.credential_ref)).data
    assert tested.ok is False
    assert repo.get_account(credential_ref=saved.credential_ref).status == "repair-required"
    assert session.exec(select(CredentialAccount)).all() == []
    assert session.exec(select(CredentialScope)).all() == []
    with pytest.raises(ConflictError, match="not connected"):
        await repo.resolve_for_execution(
            project_id=project_id,
            provider_key="cfobooth",
            credential_ref=saved.credential_ref,
            operation="action.run",
        )


def inventory(session, ref, project_id, *, suffix="own"):
    plugin = session.exec(select(Plugin).where(Plugin.slug == "utils")).one()
    # Real generic Action rows, isolated from a not-yet-delivered CFO plugin.
    provider = session.exec(select(Provider).where(Provider.key == "cfobooth")).first()
    if provider is None:
        provider = Provider(plugin_id=plugin.id, key="cfobooth", name="Inventory fixture")
        session.add(provider)
        session.flush()
    row = Action(
        plugin_id=plugin.id,
        provider_id=provider.id,
        key=f"api.s11-{suffix}",
        name="Scoped fixture",
        description="",
        config_json={
            "inventory_source": "cfobooth.test",
            "inventory_state": "active",
            "inventory_credential_ref": ref,
            "inventory_project_id": project_id,
            "connector": "fixture",
            "execution_mode": "connector",
        },
    )
    session.add(row)
    session.commit()
    return row


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_field", ["api_key", "api_base_url", "organization_id"])
async def test_replacement_invalidates_scope_and_late_probe_cannot_overwrite(
    session,
    project_id,
    monkeypatch,
    contract,
    changed_field,
):
    repo = AuthRepository(session)
    saved = repo.store_credential(
        provider_key="cfobooth",
        display_name="Rotate",
        fields=fields(),
        attach_project_id=project_id,
    ).data

    async def success(*args, **kwargs):
        return probe_result()

    monkeypatch.setattr("stackos_connectors.probe.probe_credentials", success)
    await repo.test(project_id=project_id, credential_ref=saved.credential_ref)
    old_inventory = inventory(session, saved.credential_ref, project_id)
    foreign_inventory = inventory(session, "cred_other", project_id, suffix="foreign")
    values = fields(
        **{
            changed_field: {
                "api_key": "replacement-canary",
                "api_base_url": "http://127.0.0.1:3200",
                "organization_id": "b6ed0649-65a3-4ec1-bcf5-233bd5049817",
            }[changed_field]
        }
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(*args, **kwargs):
        entered.set()
        await release.wait()
        return probe_result()

    monkeypatch.setattr("stackos_connectors.probe.probe_credentials", held)
    old_test = asyncio.create_task(
        repo.test(project_id=project_id, credential_ref=saved.credential_ref)
    )
    await entered.wait()
    changed = repo.update_credential(
        credential_ref=saved.credential_ref,
        fields=values,
        display_name=None,
    ).data
    assert changed.status == "pending"
    assert changed.account is None
    assert changed.last_test is None
    session.refresh(old_inventory)
    assert old_inventory.config_json["inventory_state"] == "retired"
    assert "connector" not in old_inventory.config_json
    session.refresh(foreign_inventory)
    assert foreign_inventory.config_json["inventory_state"] == "active"

    async def replacement(*args, **kwargs):
        result = probe_result(api_base_url=values["api_base_url"])
        result["metadata"]["evidence"]["account"]["metadata"]["key_id"] = "replacement-key-id"
        result["metadata"]["evidence"]["account"]["metadata"]["organization_id"] = values[
            "organization_id"
        ]
        return result

    monkeypatch.setattr("stackos_connectors.probe.probe_credentials", replacement)
    await repo.test(project_id=project_id, credential_ref=saved.credential_ref)
    release.set()
    with pytest.raises(ConflictError, match="changed during"):
        await old_test
    current = repo.get_account(credential_ref=saved.credential_ref)
    assert current.status == "connected"
    assert current.account["metadata_json"]["key_id"] == "replacement-key-id"
    edit = repo.get_credential_edit_state(credential_ref=saved.credential_ref)
    assert edit.values["api_base_url"] == values["api_base_url"]


@pytest.fixture
def account_api(app, auth_token, monkeypatch):
    with (
        TestClient(
            app, base_url="http://127.0.0.1:5180", headers={"Authorization": f"Bearer {auth_token}"}
        ) as client,
        Session(app.state.engine) as database,
    ):
        install_cfobooth_contract(database, monkeypatch)
        yield client, database


def test_real_local_admin_api_custody_failure_recovery_and_disconnect(
    monkeypatch,
    account_api,
    caplog,
):
    from stackos_connectors.errors import IntegrationDownError

    api, session = account_api
    project = api.post(
        "/api/v1/projects",
        json={"slug": "s11", "name": "s11", "domain": "example.test", "locale": "en-US"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["data"]["id"]
    observed = []
    state = {"mode": "success"}

    async def probe(connector, *, auth, **kwargs):
        observed.append((connector, dict(auth.config), dict(auth.fields)))
        if state["mode"] == "success":
            return probe_result()
        raise IntegrationDownError(
            CANARY,
            data={
                "reason_code": "invalid_key_identity",
                "stage": "test",
                **({"status": 401} if state["mode"] == "401" else {}),
            },
        )

    monkeypatch.setattr("stackos_connectors.probe.probe_credentials", probe)
    created = api.post(
        "/api/v1/auth/accounts/cfobooth",
        json={
            "display_name": "API flow",
            "fields": fields(),
            "attach_project_id": project_id,
        },
    )
    assert created.status_code == 201, created.text
    ref = created.json()["data"]["credential_ref"]
    assert created.json()["data"]["status"] == "pending"
    path = f"/api/v1/auth/accounts/{ref}"
    verified = api.post(path + "/test")
    assert verified.status_code == 200, verified.text
    assert verified.json()["data"]["ok"] is True
    assert observed == [
        ("cfobooth", {"api_base_url": BASE, "organization_id": ORG}, {"api_key": CANARY})
    ]
    foreign = api.post(
        "/api/v1/projects",
        json={
            "slug": "foreign",
            "name": "Foreign",
            "domain": "foreign.example.test",
            "locale": "en-US",
        },
    ).json()["data"]["id"]
    denied = api.post(
        "/api/v1/operations/account.test/call",
        json={
            "arguments": {
                "project_id": foreign,
                "credential_ref": ref,
                "response_mode": "raw",
            }
        },
    )
    assert denied.status_code == 404, denied.text
    assert "not attached" in denied.json()["detail"]
    assert len(observed) == 1
    credential = session.exec(select(Credential).where(Credential.credential_ref == ref)).one()
    backing = session.get(IntegrationCredential, credential.integration_credential_id)
    backing_id = backing.id
    assert CANARY.encode() not in backing.encrypted_payload
    from pathlib import Path

    database_path = Path(session.get_bind().url.database)
    for candidate in (database_path, Path(str(database_path) + "-wal")):
        if candidate.exists():
            assert CANARY.encode() not in candidate.read_bytes()
    assert json.loads(IntegrationCredentialRepository(session).get_decrypted(backing.id)) == {
        "api_key": CANARY
    }
    own = inventory(session, ref, project_id)
    for mode, reason in [("revoked", "probe_error"), ("401", "authentication_failed")]:
        state["mode"] = mode
        patched = api.patch(path, json={"fields": {"api_key": "replacement-canary"}})
        assert patched.status_code == 200, patched.text
        failed = api.post(path + "/test")
        assert failed.status_code == 200, failed.text
        assert failed.json()["data"]["ok"] is False
        assert failed.json()["data"]["metadata"]["reason_code"] == reason
        listed = api.get("/api/v1/auth/accounts?provider_key=cfobooth").json()
        assert listed["accounts"][0]["status"] == "repair-required"
        assert listed["accounts"][0]["account"] is None
        assert CANARY not in json.dumps([patched.json(), failed.json(), listed])
    state["mode"] = "success"
    assert api.post(path + "/test").json()["data"]["ok"] is True
    assert (
        api.delete(f"/api/v1/projects/{project_id}/connections/accounts/{ref}").status_code == 200
    )
    removed = api.post(path + "/revoke")
    assert removed.status_code == 200, removed.text
    session.expire_all()
    assert session.get(IntegrationCredential, backing_id) is None
    assert AuthRepository(session).get_account(credential_ref=ref).status == "revoked"
    session.refresh(own)
    assert own.config_json["inventory_state"] == "retired"
    assert len(observed) == 4  # No remote revoke, login, refresh or mutation replay.
    assert CANARY not in json.dumps(
        [
            dict(row)
            for row in session.connection().exec_driver_sql("SELECT * FROM credentials").mappings()
        ],
        default=str,
    )
    assert CANARY not in json.dumps(
        [e.model_dump(mode="json") for e in session.exec(select(CredentialUsageEvent)).all()]
    )
    assert CANARY not in caplog.text


# s12: actual accepted native declaration/probe/HTTP handlers; no r8 handler stub.


@native.native_cfo
@pytest.mark.fresh_catalog
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    ["denied", "revoked", "expired", "wrong-org", "malformed-email", "malformed-identity"],
)
async def test_native_account_failure_never_establishes_identity(
    session, project_id, httpx_mock, failure, caplog
):
    repo = AuthRepository(session)
    saved = repo.store_credential(
        provider_key="cfobooth",
        display_name="Native failure",
        fields={
            "api_key": native.CANARY,
            "api_base_url": native.BASE,
            "organization_id": native.ORG,
        },
        attach_project_id=project_id,
    ).data
    raw = native.identity()
    status = 200
    if failure in {"denied", "revoked"}:
        status = 403 if failure == "denied" else 401
        raw = {"requestId": "denied", "error": {"code": "forbidden", "message": native.CANARY}}
    elif failure == "expired":
        raw["data"]["expiresAt"] = "2020-01-01T00:00:00Z"
    elif failure == "wrong-org":
        raw["data"]["organizationId"] = "b6ed0649-65a3-4ec1-bcf5-233bd5049817"
    elif failure == "malformed-email":
        raw["data"]["user"]["email"] = "not-an-email"
    else:
        raw["data"]["user"]["id"] = {"unsafe": "shape"}
    httpx_mock.add_response(
        method="GET",
        url=native.BASE + f"/api/organizations/{native.ORG}/api-keys/current",
        status_code=status,
        json=raw,
    )
    tested = (await repo.test(project_id=project_id, credential_ref=saved.credential_ref)).data
    assert not tested.ok
    account = repo.get_account(credential_ref=saved.credential_ref)
    assert account.status == "repair-required" and account.account is None
    assert session.exec(select(CredentialScope)).all() == []
    assert native.CANARY not in tested.model_dump_json() + caplog.text
    with pytest.raises(ConflictError):
        await repo.resolve_for_execution(
            project_id=project_id,
            provider_key="cfobooth",
            credential_ref=saved.credential_ref,
            operation="action.run",
        )
    assert len(httpx_mock.get_requests()) == 1


@native.native_cfo
@pytest.mark.fresh_catalog
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://cfobooth.com",
        "https://CFOBOOTH.COM.:443",
        "http://remote.example.test",
        "",
        "http://localhost:31837?override=true",
    ],
)
async def test_native_canonical_host_and_unsafe_url_reject_before_probe(
    session, project_id, httpx_mock, url
):
    repo = AuthRepository(session)
    values = {"api_key": native.CANARY, "api_base_url": url, "organization_id": native.ORG}
    if not url:
        with pytest.raises(ValidationError):
            repo.store_credential(provider_key="cfobooth", display_name="Invalid", fields=values)
    else:
        saved = repo.store_credential(
            provider_key="cfobooth",
            display_name="Invalid",
            fields=values,
            attach_project_id=project_id,
        ).data
        result = (await repo.test(project_id=project_id, credential_ref=saved.credential_ref)).data
        assert not result.ok
    assert httpx_mock.get_requests() == []


@native.native_cfo
@pytest.mark.fresh_catalog
@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["api_key", "api_base_url", "organization_id"])
async def test_native_replacement_and_held_probe_fence(session, project_id, httpx_mock, field):
    repo = AuthRepository(session)
    ref = await native.verified_account(session, project_id, httpx_mock)
    synced = await native.sync(
        session, project_id, ref, httpx_mock, native.catalog(native.descriptor())
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(request):
        import httpx

        entered.set()
        await release.wait()
        return httpx.Response(200, json=native.identity())

    httpx_mock.add_callback(
        held, method="GET", url=native.BASE + f"/api/organizations/{native.ORG}/api-keys/current"
    )
    old = asyncio.create_task(repo.test(project_id=project_id, credential_ref=ref))
    await asyncio.wait_for(entered.wait(), 5)
    values = {
        "api_key": "s12-replacement-key",
        "api_base_url": "http://localhost:31838/new;prefix",
        "organization_id": "b6ed0649-65a3-4ec1-bcf5-233bd5049817",
    }
    changed = repo.update_credential(
        credential_ref=ref, fields={field: values[field]}, display_name=None
    ).data
    assert changed.status == "pending" and changed.account is None
    current_org = values[field] if field == "organization_id" else native.ORG
    current_base = values[field] if field == "api_base_url" else native.BASE
    replacement = native.identity(
        organizationId=current_org, keyId="a80e7020-3854-4f26-bf33-458b27139f20"
    )
    httpx_mock.add_response(
        method="GET",
        url=current_base + f"/api/organizations/{current_org}/api-keys/current",
        json=replacement,
    )
    assert (await repo.test(project_id=project_id, credential_ref=ref)).data.ok
    release.set()
    with pytest.raises(ConflictError, match="changed during"):
        await old
    current = repo.get_account(credential_ref=ref)
    assert current.account["metadata_json"]["key_id"] == replacement["data"]["keyId"]
    from stackos.actions import ActionRepository
    from stackos.repositories.base import RepositoryError

    with pytest.raises(RepositoryError):
        await ActionRepository(session).execute(
            project_id=project_id,
            action_ref=synced["action_refs"][0],
            credential_ref=ref,
            input_json={},
        )
    assert len(httpx_mock.get_requests()) == 4


@native.native_cfo
@pytest.mark.fresh_catalog
def test_native_local_admin_account_custody_api_and_disconnect(app, auth_token, httpx_mock, caplog):
    with TestClient(
        app, base_url="http://127.0.0.1:5180", headers={"Authorization": f"Bearer {auth_token}"}
    ) as api:
        provider = next(
            p for p in api.get("/api/v1/auth/providers").json() if p["key"] == "cfobooth"
        )
        method = provider["auth_methods"][0]
        assert method["key"] == "api_key" and method["permission_verification"] is None
        secret = next(f for f in method["fields"] if f["key"] == "api_key")
        assert secret["secret"] is True
        project_id = api.post(
            "/api/v1/projects",
            json={
                "slug": "native-account",
                "name": "Native",
                "domain": "example.test",
                "locale": "en-US",
            },
        ).json()["data"]["id"]
        created = api.post(
            "/api/v1/auth/accounts/cfobooth",
            json={
                "display_name": "Native API",
                "fields": {
                    "api_key": native.CANARY,
                    "api_base_url": native.BASE + "/",
                    "organization_id": native.ORG,
                },
                "attach_project_id": project_id,
            },
        )
        assert created.status_code == 201, created.text
        ref = created.json()["data"]["credential_ref"]
        path = f"/api/v1/auth/accounts/{ref}"
        httpx_mock.add_response(
            method="GET",
            url=native.BASE + f"/api/organizations/{native.ORG}/api-keys/current",
            json=native.identity(),
        )
        tested = api.post(path + "/test")
        assert tested.json()["data"]["ok"] is True, tested.text
        account = api.get(path).json()
        assert native.CANARY not in json.dumps([created.json(), tested.json(), account])
        with Session(app.state.engine) as database:
            credential = database.exec(
                select(Credential).where(Credential.credential_ref == ref)
            ).one()
            backing = database.get(IntegrationCredential, credential.integration_credential_id)
            assert native.CANARY.encode() not in backing.encrypted_payload
            assert json.loads(
                IntegrationCredentialRepository(database).get_decrypted(backing.id)
            ) == {"api_key": native.CANARY}
            assert credential.config_json["api_base_url"] == native.BASE
            from pathlib import Path

            db_path = Path(database.get_bind().url.database)
            for file in (db_path, Path(str(db_path) + "-wal")):
                if file.exists():
                    assert native.CANARY.encode() not in file.read_bytes()
        assert (
            api.delete(f"/api/v1/projects/{project_id}/connections/accounts/{ref}").status_code
            == 200
        )
        assert api.post(path + "/revoke").status_code == 200
        assert len(httpx_mock.get_requests()) == 1  # No remote revoke/refresh/session calls.
        assert native.CANARY not in caplog.text
