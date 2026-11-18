"""Project Account probes retain the Connection that authorized their result."""

import asyncio
import sqlite3
import subprocess
import sys
from contextlib import closing

import httpx
import pytest
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.db.models import Action, CredentialAccount, CredentialUsageEvent
from stackos.mcp.context import build_context
from stackos.mcp.permissions import check_call_grant
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.auth_handlers import AccountTestInput, account_test
from stackos.repositories.base import ConflictError, NotFoundError
from stackos.repositories.projects import ProjectRepository
from tests.integration.test_repositories import cfobooth_test_support as native

pytestmark = [pytest.mark.fresh_catalog, native.native_cfo, pytest.mark.asyncio]


async def test_connected_account_from_cold_repository(session, project_id, httpx_mock, tmp_path):
    ref = await native.verified_account(session, project_id, httpx_mock)
    database = tmp_path / "account.db"
    with closing(sqlite3.connect(database)) as target:
        session.connection().connection.driver_connection.backup(target)
    program = """
import sys
from sqlmodel import Session, create_engine
from stackos.auth_providers import AuthRepository

assert "stackos.actions" not in sys.modules
with Session(create_engine("sqlite:///" + sys.argv[1])) as session:
    account = AuthRepository(session).require_attached_account(
        project_id=int(sys.argv[2]), credential_ref=sys.argv[3], provider_key="cfobooth"
    )
    assert account.status == "connected"
assert "stackos.actions" not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", program, str(database), str(project_id), ref],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


async def _test_account(session, project_id, ref):
    inputs = AccountTestInput(project_id=project_id, credential_ref=ref, response_mode="raw")
    ctx = build_context(inputs.model_dump(), session)
    if project_id is None:
        ctx.extras.update(surface="rest", trusted_local_admin=True)
    else:
        check_call_grant("account.test", ctx, inputs)
    return await account_test(inputs, ctx, ProgressEmitter(None, None))


@pytest.mark.parametrize("reattach", [False, True])
async def test_project_probe_rejects_ended_connection(session, project_id, httpx_mock, reattach):
    repo = AuthRepository(session)
    saved = repo.store_credential(
        provider_key="cfobooth",
        display_name="Pending probe",
        fields={
            "api_key": native.CANARY,
            "api_base_url": native.BASE,
        },
        attach_project_id=project_id,
    ).data
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(_request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json=native.identity())

    httpx_mock.add_callback(held, method="GET", url=native.BASE + "/api/api-keys/current")
    old = asyncio.create_task(_test_account(session, project_id, saved.credential_ref))
    await asyncio.wait_for(entered.wait(), 5)
    with Session(session.get_bind()) as changes:
        AuthRepository(changes).detach_account(
            project_id=project_id, credential_ref=saved.credential_ref
        )
        if reattach:
            AuthRepository(changes).attach_account(
                project_id=project_id, credential_ref=saved.credential_ref
            )
    usage_count = len(session.exec(select(CredentialUsageEvent)).all())
    release.set()
    with pytest.raises(ConflictError, match="changed during"):
        await old
    assert repo.get_account(credential_ref=saved.credential_ref).status == "pending"
    assert session.exec(select(CredentialAccount)).all() == []
    assert len(session.exec(select(CredentialUsageEvent)).all()) == usage_count


async def test_detached_probe_failure_preserves_shared_account(session, project_id, httpx_mock):
    repo = AuthRepository(session)
    ref = await native.verified_account(session, project_id, httpx_mock)
    other = (
        ProjectRepository(session)
        .create(slug="shared-probe", name="Shared probe", domain="example.test")
        .data.id
    )
    repo.attach_account(project_id=other, credential_ref=ref)
    await native.sync(session, other, ref, httpx_mock, native.catalog(native.descriptor()))
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(_request):
        entered.set()
        await release.wait()
        return httpx.Response(401, json={"error": "denied"})

    httpx_mock.add_callback(held, method="GET", url=native.BASE + "/api/api-keys/current")
    old = asyncio.create_task(_test_account(session, project_id, ref))
    await asyncio.wait_for(entered.wait(), 5)
    with Session(session.get_bind()) as changes:
        AuthRepository(changes).detach_account(project_id=project_id, credential_ref=ref)
    usage_count = len(session.exec(select(CredentialUsageEvent)).all())
    release.set()
    with pytest.raises(ConflictError, match="changed during"):
        await old
    session.expire_all()
    current = repo.get_account(credential_ref=ref)
    assert current.status == "connected" and current.account is not None
    assert other in current.project_ids
    inventory = [
        action.config_json["inventory_state"]
        for action in session.exec(select(Action)).all()
        if (action.config_json or {}).get("inventory_project_id") == other
        and (action.config_json or {}).get("inventory_source") == "cfobooth.live-catalog"
    ]
    assert inventory == ["active"]
    assert len(session.exec(select(CredentialUsageEvent)).all()) == usage_count


@pytest.mark.parametrize("global_admin", [False, True])
async def test_probe_keeps_unaffected_authority(session, project_id, httpx_mock, global_admin):
    repo = AuthRepository(session)
    ref = await native.verified_account(session, project_id, httpx_mock)
    other = (
        ProjectRepository(session)
        .create(slug="unaffected-probe", name="Other", domain="example.test")
        .data.id
    )
    repo.attach_account(project_id=other, credential_ref=ref)
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(_request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json=native.identity())

    httpx_mock.add_callback(held, method="GET", url=native.BASE + "/api/api-keys/current")
    old = asyncio.create_task(_test_account(session, None if global_admin else other, ref))
    await asyncio.wait_for(entered.wait(), 5)
    with Session(session.get_bind()) as changes:
        AuthRepository(changes).detach_account(project_id=project_id, credential_ref=ref)
    release.set()
    assert (await old).data.ok
    assert repo.get_account(credential_ref=ref).status == "connected"


async def test_global_probe_cannot_restore_revoked_key(session, project_id, httpx_mock):
    repo = AuthRepository(session)
    ref = await native.verified_account(session, project_id, httpx_mock)
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(_request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json=native.identity())

    httpx_mock.add_callback(held, method="GET", url=native.BASE + "/api/api-keys/current")
    old = asyncio.create_task(_test_account(session, None, ref))
    await asyncio.wait_for(entered.wait(), 5)
    with Session(session.get_bind()) as changes:
        changes_repo = AuthRepository(changes)
        changes_repo.detach_account(project_id=project_id, credential_ref=ref)
        changes_repo.revoke(credential_ref=ref)
    release.set()
    with pytest.raises(ConflictError, match="changed during"):
        await old
    assert repo.get_account(credential_ref=ref).status == "revoked"


async def test_foreign_project_probe_denied_before_provider_access(session, project_id, httpx_mock):
    ref = await native.verified_account(session, project_id, httpx_mock)
    other = (
        ProjectRepository(session)
        .create(slug="foreign-probe", name="Other", domain="example.test")
        .data.id
    )
    usage_count = len(session.exec(select(CredentialUsageEvent)).all())
    with pytest.raises(NotFoundError, match="not attached"):
        await _test_account(session, other, ref)
    assert len(httpx_mock.get_requests()) == 1
    assert len(session.exec(select(CredentialUsageEvent)).all()) == usage_count
