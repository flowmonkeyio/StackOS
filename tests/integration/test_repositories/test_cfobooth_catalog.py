"""P07 atomic catalog projection and shared inventory lifecycle."""

from copy import deepcopy

import pytest
from sqlmodel import select

from stackos.actions import ActionRepository
from stackos.db.models import Action, ActionVersion
from stackos.repositories.base import ConflictError
from tests.integration.test_repositories.cfobooth_test_support import (
    catalog,
    descriptor,
    native_cfo,
    sync,
    verified_account,
)

pytestmark = [native_cfo, pytest.mark.fresh_catalog, pytest.mark.asyncio]


async def test_complete_partial_retirement_history_and_projector_repair(
    session, project_id, httpx_mock
):
    ref = await verified_account(session, project_id, httpx_mock)
    first, second = descriptor(), descriptor("fixture.second")
    initial = await sync(session, project_id, ref, httpx_mock, catalog(first, second))
    assert initial["created"] == 2
    actions = session.exec(select(Action).where(Action.key.startswith("api."))).all()
    rows = [
        r
        for r in actions
        if (r.config_json or {}).get("inventory_source") == "cfobooth.live-catalog"
    ]
    ids = {r.id for r in rows}
    row = rows[0]
    # A stale local projection is repaired despite an unchanged provider hash.
    row.input_schema_json = {"type": "object"}
    row.config_json = {**row.config_json, "inventory_manifest_hash": "old-projector"}
    session.add(row)
    session.commit()
    repaired = await sync(session, project_id, ref, httpx_mock, catalog(first, second))
    assert repaired["updated"] == 1 and repaired["skipped"] == 1
    partial = await sync(session, project_id, ref, httpx_mock, catalog(first), limit=1)
    assert partial["retired"] == 0
    complete = await sync(session, project_id, ref, httpx_mock, catalog(first))
    assert complete["retired"] == 1
    empty = await sync(session, project_id, ref, httpx_mock, catalog())
    assert empty["retired"] == 1
    restored = await sync(session, project_id, ref, httpx_mock, catalog(first, second))
    assert set(restored["action_refs"]) == set(initial["action_refs"])
    assert {r.id for r in session.exec(select(Action)).all() if r.id in ids} == ids
    assert all(
        session.exec(select(ActionVersion).where(ActionVersion.action_id == r.id)).all()
        for r in rows
    )
    assert len(httpx_mock.get_requests()) == 7  # One probe plus one export per sync.


@pytest.mark.parametrize(
    "bad", ["version", "duplicate-id", "duplicate-route", "schema", "effect", "excluded-policy"]
)
async def test_invalid_catalog_preserves_previous_inventory(session, project_id, httpx_mock, bad):
    ref = await verified_account(session, project_id, httpx_mock)
    valid = descriptor()
    await sync(session, project_id, ref, httpx_mock, catalog(valid))
    before = [
        (r.id, deepcopy(r.config_json), deepcopy(r.input_schema_json))
        for r in session.exec(select(Action)).all()
    ]
    export = catalog(valid, descriptor("fixture.new"))
    extra = export["data"]["operations"][1]
    if bad == "version":
        export["data"]["version"] = 2
    elif bad == "duplicate-id":
        extra["operationId"] = valid["operationId"]
    elif bad == "duplicate-route":
        extra["route"] = valid["route"]
    elif bad == "schema":
        extra["schemas"]["query"] = {"$ref": "https://foreign.example.test/schema"}
    elif bad == "effect":
        extra["effect"] = "mutation"
    else:
        extra["execution"] = {"kind": "excluded", "reason": "Control"}
        extra["effect"] = "mutation"
    with pytest.raises(ConflictError):
        await sync(session, project_id, ref, httpx_mock, export)
    assert [
        (r.id, r.config_json, r.input_schema_json) for r in session.exec(select(Action)).all()
    ] == before
    assert len(httpx_mock.get_requests()) == 3


async def test_readonly_post_and_excluded_metadata(session, project_id, httpx_mock):
    ref = await verified_account(session, project_id, httpx_mock)
    read = descriptor(
        "fixture.query", method="POST", body={"type": "object", "additionalProperties": False}
    )
    excluded = descriptor("fixture.excluded")
    excluded["execution"] = {"kind": "excluded", "reason": "Not key callable"}
    result = await sync(session, project_id, ref, httpx_mock, catalog(read, excluded))
    assert result["synced"] == 1
    manifest = (
        ActionRepository(session)
        .describe(project_id=project_id, action_ref=result["action_refs"][0])
        .manifest
    )
    assert manifest.risk_level == "read"
    assert manifest.config_json["native_descriptor"] == read


async def test_late_catalog_cannot_reactivate_replaced_account(session, project_id, httpx_mock):
    import asyncio

    import httpx

    from stackos.auth_providers import AuthRepository
    from tests.integration.test_repositories.cfobooth_test_support import BASE, ORG

    ref = await verified_account(session, project_id, httpx_mock)
    await sync(session, project_id, ref, httpx_mock, catalog(descriptor()))
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(_request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json=catalog(descriptor("fixture.late")))

    httpx_mock.add_callback(
        held, method="GET", url=BASE + f"/api/organizations/{ORG}/operation-catalog"
    )
    pending = asyncio.create_task(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="cfobooth.catalog.sync",
            input_json={},
            credential_ref=ref,
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    AuthRepository(session).update_credential(
        credential_ref=ref, fields={"api_key": "replacement-only"}, display_name=None
    )
    release.set()
    with pytest.raises(ConflictError):
        await pending
    rows = [
        r
        for r in session.exec(select(Action)).all()
        if (r.config_json or {}).get("inventory_source") == "cfobooth.live-catalog"
    ]
    assert len(rows) == 1 and rows[0].config_json["inventory_state"] == "retired"
    assert len(httpx_mock.get_requests()) == 3
