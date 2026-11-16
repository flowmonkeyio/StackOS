"""Trackbooth generated inventory lifecycle and scoping tests."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select
from stackos_connectors.connectors.trackbooth.schema import operation_input_schema

from stackos.actions import (
    ActionRepository,
)
from stackos.actions.generated_inventory import GeneratedInventoryIdentity, upsert_generated_actions
from stackos.actions.trackbooth_inventory import _runtime_inventory_scope_key
from stackos.db.models import (
    Action,
    ActionCall,
    ActionVersion,
    Plugin,
    PluginSource,
    Provider,
)
from stackos.generated_inventory import generated_inventory_scope_key
from stackos.repositories.base import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from stackos.repositories.execution_contexts import ExecutionContextRepository
from stackos.repositories.projects import (
    ProjectRepository,
)
from tests.integration.test_repositories.trackbooth_test_support import (
    _add_trackbooth_sync_responses,
    _sync_trackbooth_catalog,
    _trackbooth_credential_ref,
    _trackbooth_generated_action_ref,
    _trackbooth_links_create_detail,
    _trackbooth_offers_findbyid_detail,
)

pytestmark = pytest.mark.fresh_catalog


@pytest.fixture
def second_provider_inventory(session: Session, project_id: int):
    """A normalized protocol control; no CFO plugin, auth or remote service."""
    plugin = Plugin(slug="inventory-fixture", name="Inventory fixture", source=PluginSource.PROJECT)
    session.add(plugin)
    session.flush()
    provider = Provider(plugin_id=plugin.id, key="fixture-api", name="Fixture API")
    session.add(provider)
    session.commit()
    identity = GeneratedInventoryIdentity(
        plugin_slug=plugin.slug,
        provider_key=provider.key,
        inventory_source="fixture.catalog.export",
        scope_fields=(
            "project_id",
            "credential_ref",
            "api_base_url",
            "subject",
            "organization",
            "key_id",
        ),
    )
    scope = {
        "project_id": project_id,
        "credential_ref": "cred_fixture",
        "api_base_url": "http://127.0.0.1:8123",
        "subject": "user-fixture",
        "organization": "org-fixture",
        "key_id": "key-fixture",
    }
    return identity, scope


def _second_provider_action(scope: dict, operation_id: str = "records_read"):
    # Existing normalized operation shape, consumed through the preserved public
    # projector import (the accepted c08 delegates it to the shared schema owner).
    detail = {
        "operation_id": operation_id,
        "method": "POST",
        "path": "/api/records/query",
        "request_body": {
            "type": "object",
            "additionalProperties": False,
            "required": ["count"],
            "properties": {"count": {"type": "integer", "minimum": 1, "maximum": 5}},
        },
    }
    scope_key = generated_inventory_scope_key(scope)
    return operation_id, {
        "key": f"api.{scope_key}.{operation_id}",
        "name": "Fixture records",
        "description": "Local normalized catalog fixture",
        "provider": "fixture-api",
        "capability": "agent-api",
        "risk_level": "read",
        "input_schema": operation_input_schema(detail),
        "output_schema": {"type": "object"},
        "config": {
            "public_action_key": f"api.{operation_id}",
            "connector": "fixture-api",
            "operation": "operation.execute",
            "method": "POST",
            "path": detail["path"],
        },
    }


def _sync_second_provider(session: Session, identity, scope, actions, *, complete=True, day=1):
    return upsert_generated_actions(
        session=session,
        identity=identity,
        scope=scope,
        actions=actions,
        catalog_hash="unchanged-provider-hash",
        prune_missing=complete,
        now=datetime(2026, 11, day),
    )


def test_shared_inventory_updates_projection_and_preserves_versions_and_history(
    session: Session, project_id: int, second_provider_inventory
) -> None:
    identity, scope = second_provider_inventory
    action = _second_provider_action(scope)
    first = _sync_second_provider(session, identity, scope, [action])
    session.commit()
    row = session.exec(select(Action).where(Action.key == action[1]["key"])).one()
    row_id = row.id
    live = session.exec(select(ActionVersion).where(ActionVersion.action_id == row_id)).one()
    live_id = live.id
    captured = ActionVersion(
        action_id=row_id, version="captured-before-sync", manifest_json=deepcopy(live.manifest_json)
    )
    session.add(captured)
    # Real audit history is also covered below through Trackbooth's caller.
    session.commit()
    historical = deepcopy(captured.manifest_json)
    initial_hash = row.config_json["inventory_manifest_hash"]
    initial_updated = row.updated_at

    second = _sync_second_provider(session, identity, scope, [action], day=2)
    assert (second["created"], second["updated"], second["skipped"]) == (0, 0, 1)
    assert row.updated_at == initial_updated
    assert live.manifest_json["config"]["inventory_synced_at"] == "2026-11-01T00:00:00"
    changed = deepcopy(action)
    changed[1]["input_schema"]["properties"]["body"]["properties"]["count"]["maximum"] = 3
    third = _sync_second_provider(session, identity, scope, [changed], day=3)
    session.commit()
    session.refresh(row)
    session.refresh(live)
    session.refresh(captured)
    assert first["action_refs"] == third["action_refs"] == ["inventory-fixture.api.records_read"]
    assert (third["created"], third["updated"], third["skipped"]) == (0, 1, 0)
    assert row.id == row_id and live.id == live_id
    assert row.config_json["inventory_catalog_hash"] == "unchanged-provider-hash"
    assert row.config_json["inventory_manifest_hash"] != initial_hash
    assert (
        live.manifest_json["input_schema"]["properties"]["body"]["properties"]["count"]["maximum"]
        == 3
    )
    assert captured.manifest_json == historical
    assert (
        len(session.exec(select(ActionVersion).where(ActionVersion.action_id == row_id)).all()) == 2
    )


def test_shared_inventory_partial_complete_and_reactivation_keep_history(
    session: Session, second_provider_inventory
) -> None:
    identity, scope = second_provider_inventory
    first = _second_provider_action(scope)
    unseen = _second_provider_action(scope, "records_other")
    _sync_second_provider(session, identity, scope, [first, unseen])
    session.commit()
    other = session.exec(select(Action).where(Action.key == unseen[1]["key"])).one()
    other_id = other.id
    version = session.exec(select(ActionVersion).where(ActionVersion.action_id == other_id)).one()
    historical_manifest = deepcopy(version.manifest_json)
    partial = _sync_second_provider(session, identity, scope, [first], complete=False)
    assert partial["pruned"] == 0 and other.config_json["inventory_state"] == "active"
    complete = _sync_second_provider(session, identity, scope, [first])
    assert complete["retired"] == 1
    assert other.config_json["inventory_state"] == "retired"
    assert "connector" not in other.config_json
    assert version.manifest_json == historical_manifest
    repeated = _sync_second_provider(session, identity, scope, [first])
    assert repeated["retired"] == 0
    recovered = _sync_second_provider(session, identity, scope, [first, unseen])
    assert recovered["updated"] == 1 and recovered["created"] == 0
    assert other.id == other_id and other.config_json["inventory_state"] == "active"
    assert "inventory_retired_at" not in other.config_json
    # The helper has not committed: caller rollback restores the prior committed
    # rows, including the original live-catalog snapshot.
    session.rollback()
    session.refresh(other)
    assert other.config_json["inventory_state"] == "active"
    assert version.manifest_json == historical_manifest


@pytest.mark.parametrize(
    "dimension",
    ["project_id", "credential_ref", "api_base_url", "subject", "organization", "key_id"],
)
def test_shared_inventory_binds_every_declared_scope_dimension(
    session: Session, second_provider_inventory, dimension: str
) -> None:
    identity, scope = second_provider_inventory
    original = _second_provider_action(scope)
    initial = _sync_second_provider(session, identity, scope, [original])
    session.commit()
    changed_scope = {
        **scope,
        dimension: scope[dimension] + 1
        if dimension == "project_id"
        else scope[dimension] + "-other",
    }
    changed = _second_provider_action(changed_scope)
    out = _sync_second_provider(session, identity, changed_scope, [changed])
    assert out["inventory_scope_key"] != initial["inventory_scope_key"]
    assert out["created"] == 1 and out["retired"] == 0
    rows = session.exec(
        select(Action).where(Action.key.in_([original[1]["key"], changed[1]["key"]]))
    ).all()
    assert len(rows) == 2
    assert all(row.config_json["inventory_state"] == "active" for row in rows)


@pytest.mark.parametrize("bad", ["duplicate", "provider", "scope", "missing_scope", "owner"])
def test_shared_inventory_rejects_invalid_batch_before_publishing(
    session: Session, second_provider_inventory, bad: str
) -> None:
    identity, scope = second_provider_inventory
    first = _second_provider_action(scope)
    second = _second_provider_action(scope, "records_other")
    actions = [first, second]
    if bad == "duplicate":
        second[1]["config"]["public_action_key"] = first[1]["config"]["public_action_key"]
    elif bad == "provider":
        second[1]["provider"] = "foreign-provider"
    elif bad == "scope":
        second[1]["config"]["inventory_organization"] = "foreign-org"
    elif bad == "missing_scope":
        scope = {key: value for key, value in scope.items() if key != "key_id"}
    else:
        plugin = session.exec(select(Plugin).where(Plugin.slug == identity.plugin_slug)).one()
        session.add(
            Action(plugin_id=plugin.id, key=second[1]["key"], name="Unrelated static action")
        )
        session.commit()
    before = len(session.exec(select(Action)).all())
    with pytest.raises(ValidationError):
        _sync_second_provider(session, identity, scope, actions)
    assert len(session.exec(select(Action)).all()) == before
    assert session.exec(select(ActionVersion)).all() == []


def test_shared_inventory_rolls_back_new_rows_with_caller_transaction(
    session: Session, second_provider_inventory
) -> None:
    identity, scope = second_provider_inventory
    out = _sync_second_provider(session, identity, scope, [_second_provider_action(scope)])
    assert out["created"] == 1
    session.rollback()
    assert session.exec(select(Action)).all() == []
    assert session.exec(select(ActionVersion)).all() == []


def test_trackbooth_scope_identity_preserves_legacy_hash() -> None:
    scope = {
        "project_id": 7,
        "credential_ref": "cred_legacy",
        "api_base_url": "https://trackbooth.local.test",
    }
    # Original three-field serialized preimage, pinned before extraction.
    assert generated_inventory_scope_key(scope) == "inv_e2c834db749a"
    assert (
        _runtime_inventory_scope_key({**scope, "acting_as_account": "ignored"})
        == "inv_e2c834db749a"
    )


def test_trackbooth_resync_and_retirement_preserve_action_and_audit_history(
    session: Session, project_id: int, httpx_mock: HTTPXMock
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    detail = _trackbooth_links_create_detail()
    _add_trackbooth_sync_responses(httpx_mock, detail)
    initial = _sync_trackbooth_catalog(session, project_id, credential_ref)
    action_ref = _trackbooth_generated_action_ref(initial, "LinksController.create")
    row = session.exec(
        select(Action).where(
            Action.config_json["public_action_key"].as_string() == "api.links_create"
        )
    ).one()
    original_id = row.id
    live = session.exec(select(ActionVersion).where(ActionVersion.action_id == row.id)).one()
    original_live_id = live.id
    captured = ActionVersion(
        action_id=row.id, version="captured-run", manifest_json=deepcopy(live.manifest_json)
    )
    session.add(captured)
    session.commit()
    historical_manifest = deepcopy(captured.manifest_json)
    httpx_mock.add_response(
        method="POST",
        url="https://trackbooth.local.test/api/links",
        json={"data": {"id": "link-history"}},
    )
    repo = ActionRepository(session)
    asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref=action_ref,
            credential_ref=credential_ref,
            input_json={
                "body": {
                    "campaign_id": "campaign-1",
                    "name": "History probe",
                    "routing_mode": "direct",
                }
            },
        )
    )
    call = session.exec(select(ActionCall).where(ActionCall.action_id == original_id)).one()
    historical_call = deepcopy(call.model_dump())

    detail["body_schema"]["json_schema"]["properties"]["name"]["maxLength"] = 80
    _add_trackbooth_sync_responses(httpx_mock, detail)
    refreshed = _sync_trackbooth_catalog(session, project_id, credential_ref)
    assert refreshed["updated"] == 1
    assert _trackbooth_generated_action_ref(refreshed, "LinksController.create") == action_ref
    assert (
        repo.describe(project_id=project_id, action_ref=action_ref).manifest.input_schema_json[
            "properties"
        ]["body"]["properties"]["name"]["maxLength"]
        == 80
    )
    _add_trackbooth_sync_responses(httpx_mock)
    retired = _sync_trackbooth_catalog(session, project_id, credential_ref)
    assert retired["retired"] == 1
    with pytest.raises(NotFoundError):
        repo.describe(project_id=project_id, action_ref=action_ref)
    session.refresh(row)
    session.refresh(live)
    session.refresh(captured)
    session.refresh(call)
    assert row.id == original_id and live.id == original_live_id
    assert row.config_json["inventory_state"] == "retired"
    assert captured.manifest_json == historical_manifest
    assert call.model_dump() == historical_call


def test_shared_inventory_complete_sync_preserves_other_provider_rows(
    session: Session, second_provider_inventory
) -> None:
    identity, scope = second_provider_inventory
    action = _second_provider_action(scope)
    _sync_second_provider(session, identity, scope, [action])
    row = session.exec(select(Action).where(Action.key == action[1]["key"])).one()
    provider = Provider(plugin_id=row.plugin_id, key="foreign-provider", name="Foreign provider")
    session.add(provider)
    session.flush()
    foreign = Action(
        plugin_id=row.plugin_id,
        provider_id=provider.id,
        key=f"api.{generated_inventory_scope_key(scope)}.foreign",
        name="Foreign inventory",
        config_json=deepcopy(row.config_json),
    )
    session.add(foreign)
    session.commit()
    session.refresh(foreign)
    snapshot = deepcopy(foreign.model_dump())
    out = _sync_second_provider(session, identity, scope, [])
    assert out["retired"] == 1
    session.flush()
    session.refresh(foreign)
    assert foreign.model_dump() == snapshot


def test_trackbooth_full_catalog_sync_prunes_missing_runtime_actions(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    _add_trackbooth_sync_responses(
        httpx_mock,
        _trackbooth_links_create_detail(),
        _trackbooth_offers_findbyid_detail(),
    )
    initial = _sync_trackbooth_catalog(session, project_id, credential_ref)
    links_ref = _trackbooth_generated_action_ref(initial, "LinksController.create")
    offers_ref = _trackbooth_generated_action_ref(initial, "OffersController.findById")
    offers_public_key = offers_ref.removeprefix("trackbooth.")

    _add_trackbooth_sync_responses(httpx_mock, _trackbooth_links_create_detail())
    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="trackbooth.catalog.sync",
            input_json={},
            credential_ref=credential_ref,
        )
    ).data

    assert out.output_json["synced"] == 1
    assert out.output_json["updated"] == 0
    assert out.output_json["skipped"] == 1
    assert out.output_json["pruned"] == 1
    assert out.output_json["retired"] == 1

    repo = ActionRepository(session)
    repo.describe(project_id=project_id, action_ref=links_ref)
    with pytest.raises(NotFoundError):
        repo.describe(project_id=project_id, action_ref=offers_ref)
    from stackos.repositories.plugins import PluginRepository

    listed_refs = {
        action.action_ref
        for action in PluginRepository(session).list_actions(
            plugin_slug="trackbooth",
            project_id=project_id,
        )
    }
    assert links_ref in listed_refs
    assert offers_ref not in listed_refs
    assert not any(action_ref.startswith("trackbooth.api.inv_") for action_ref in listed_refs)
    retired = next(
        (
            row
            for row in session.exec(select(Action)).all()
            if (row.config_json or {}).get("public_action_key") == offers_public_key
        ),
        None,
    )
    assert retired is not None
    assert retired.config_json["inventory_state"] == "retired"
    assert retired.config_json["execution_mode"] == "deferred.retired"


def test_trackbooth_catalog_sync_retires_superseded_runtime_inventory(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    _add_trackbooth_sync_responses(httpx_mock, _trackbooth_links_create_detail())
    initial = _sync_trackbooth_catalog(session, project_id, credential_ref)
    links_ref = _trackbooth_generated_action_ref(initial, "LinksController.create")
    current_scope = initial["inventory_scope_key"]
    superseded_scope = "inv_superseded_inventory"
    superseded_key = "api.inv_superseded_inventory.links_create"

    current = next(
        row
        for row in session.exec(select(Action)).all()
        if (row.config_json or {}).get("public_action_key") == links_ref.removeprefix("trackbooth.")
    )
    superseded_config = dict(current.config_json)
    superseded_config["inventory_scope_key"] = superseded_scope
    superseded_config["inventory_acting_as_account"] = None
    superseded_config["inventory_synced_at"] = "2026-01-01T00:00:00"
    session.add(
        Action(
            plugin_id=current.plugin_id,
            provider_id=current.provider_id,
            key=superseded_key,
            name=current.name,
            description=current.description,
            capability_key=current.capability_key,
            risk_level=current.risk_level,
            input_schema_json=current.input_schema_json,
            output_schema_json=current.output_schema_json,
            config_json=superseded_config,
        )
    )
    session.commit()

    _add_trackbooth_sync_responses(httpx_mock, _trackbooth_links_create_detail())
    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="trackbooth.catalog.sync",
            input_json={},
            credential_ref=credential_ref,
        )
    ).data

    assert out.output_json["inventory_scope_key"] == current_scope
    assert out.output_json["updated"] == 0
    assert out.output_json["skipped"] == 1
    assert out.output_json["pruned"] == 1
    superseded = session.exec(select(Action).where(Action.key == superseded_key)).one()
    assert superseded.config_json["inventory_state"] == "retired"
    assert superseded.config_json["execution_mode"] == "deferred.retired"

    repo = ActionRepository(session)
    repo.describe(project_id=project_id, action_ref=links_ref)
    with pytest.raises(NotFoundError):
        repo.describe(project_id=project_id, action_ref=f"trackbooth.{superseded_key}")
    from stackos.repositories.plugins import PluginRepository

    listed_refs = {
        action.action_ref
        for action in PluginRepository(session).list_actions(
            plugin_slug="trackbooth",
            project_id=project_id,
        )
    }
    assert links_ref in listed_refs
    assert f"trackbooth.{superseded_key}" not in listed_refs
    assert not any(action_ref.startswith("trackbooth.api.inv_") for action_ref in listed_refs)


def test_trackbooth_filtered_catalog_sync_does_not_prune_unrelated_runtime_actions(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    _add_trackbooth_sync_responses(
        httpx_mock,
        _trackbooth_links_create_detail(),
        _trackbooth_offers_findbyid_detail(),
    )
    initial = _sync_trackbooth_catalog(session, project_id, credential_ref)
    links_ref = _trackbooth_generated_action_ref(initial, "LinksController.create")
    offers_ref = _trackbooth_generated_action_ref(initial, "OffersController.findById")

    _add_trackbooth_sync_responses(httpx_mock, _trackbooth_links_create_detail())
    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="trackbooth.catalog.sync",
            input_json={"operation_ids": ["LinksController.create"]},
            credential_ref=credential_ref,
        )
    ).data

    assert out.output_json["synced"] == 1
    assert out.output_json["updated"] == 0
    assert out.output_json["skipped"] == 1
    assert out.output_json["pruned"] == 0

    repo = ActionRepository(session)
    repo.describe(project_id=project_id, action_ref=links_ref)
    repo.describe(project_id=project_id, action_ref=offers_ref)


def test_trackbooth_generated_inventory_is_project_scoped(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    from stackos.repositories.plugins import PluginRepository

    other_project_id = (
        ProjectRepository(session)
        .create(
            slug="trackbooth-other",
            name="Trackbooth Other",
            domain="other.example",
            locale="en-US",
        )
        .data.id
    )
    credential_ref = _trackbooth_credential_ref(session, project_id)
    _trackbooth_credential_ref(session, other_project_id)
    _add_trackbooth_sync_responses(httpx_mock, _trackbooth_links_create_detail())
    sync_output = _sync_trackbooth_catalog(session, project_id, credential_ref)
    action_ref = _trackbooth_generated_action_ref(sync_output, "LinksController.create")

    repo = ActionRepository(session)
    repo.describe(project_id=project_id, action_ref=action_ref)
    with pytest.raises(NotFoundError):
        repo.describe(project_id=other_project_id, action_ref=action_ref)

    other_actions = PluginRepository(session).list_actions(
        plugin_slug="trackbooth",
        project_id=other_project_id,
    )
    assert action_ref not in {action.action_ref for action in other_actions}


def test_trackbooth_generated_inventory_is_bound_to_credential_and_api_url(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    local_ref = _trackbooth_credential_ref(
        session,
        project_id,
        api_base_url="https://trackbooth.local.test",
        account_name="Trackbooth - Local",
    )
    prod_ref = _trackbooth_credential_ref(
        session,
        project_id,
        api_base_url="https://apis.trackbooth.com",
        account_name="Trackbooth - Production",
    )
    _add_trackbooth_sync_responses(httpx_mock, _trackbooth_links_create_detail())
    sync_output = _sync_trackbooth_catalog(session, project_id, local_ref)
    action_ref = _trackbooth_generated_action_ref(sync_output, "LinksController.create")

    httpx_mock.add_response(method="POST", json={"data": {"id": "link-local"}})
    local_out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref=action_ref,
            input_json={
                "body": {
                    "campaign_id": "campaign-1",
                    "name": "Local scoped link",
                    "routing_mode": "direct",
                },
            },
            credential_ref=local_ref,
        )
    ).data
    assert local_out.output_json["data"] == {"data": {"id": "link-local"}}

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref=action_ref,
                input_json={
                    "body": {
                        "campaign_id": "campaign-1",
                        "name": "Wrong credential",
                        "routing_mode": "direct",
                    },
                },
                credential_ref=prod_ref,
            )
        )
    assert "credential used for catalog sync" in excinfo.value.data["error"]


def test_trackbooth_generated_action_runs_with_execution_context_ref(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    _add_trackbooth_sync_responses(httpx_mock, _trackbooth_links_create_detail())
    sync_output = _sync_trackbooth_catalog(session, project_id, credential_ref)
    action_ref = _trackbooth_generated_action_ref(sync_output, "LinksController.create")
    ExecutionContextRepository(session).create(
        project_id=project_id,
        context_ref="ctx_generated_action",
        name="Generated action context",
        plugin_slug="trackbooth",
        provider_key="trackbooth",
        action_ref=action_ref,
        credential_ref=credential_ref,
        credential_locked=True,
        provider_context_json={"acting_as_account": "acct-managed"},
        provider_context_locked_fields_json=["acting_as_account"],
    )
    httpx_mock.add_response(
        method="POST",
        url="https://trackbooth.local.test/api/links",
        json={"data": {"id": "link-context"}},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref=action_ref,
            context_ref="ctx_generated_action",
            input_json={
                "body": {
                    "campaign_id": "campaign-1",
                    "name": "Context scoped link",
                    "routing_mode": "direct",
                },
            },
        )
    ).data

    assert out.credential_ref == credential_ref
    assert out.output_json["operation_id"] == "LinksController.create"
    actual = httpx_mock.get_requests()[-1]
    assert actual.url.host == "trackbooth.local.test"
    assert actual.headers["X-Acting-As-Account"] == "acct-managed"
    call = session.exec(
        select(ActionCall).where(ActionCall.action_key == action_ref.split(".", 1)[1])
    ).first()
    assert call is not None
    assert call.credential_ref == credential_ref
    assert call.metadata_json["execution_context"]["context_ref"] == "ctx_generated_action"


def test_trackbooth_stable_ref_uses_execution_context_to_select_inventory_scope(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    local_ref = _trackbooth_credential_ref(
        session,
        project_id,
        api_base_url="https://trackbooth.local.test",
        account_name="Trackbooth - Local",
    )
    prod_ref = _trackbooth_credential_ref(
        session,
        project_id,
        api_base_url="https://apis.trackbooth.com",
        account_name="Trackbooth - Production",
    )
    _add_trackbooth_sync_responses(
        httpx_mock,
        _trackbooth_links_create_detail(),
        base_url="https://trackbooth.local.test",
    )
    local_sync = _sync_trackbooth_catalog(session, project_id, local_ref)
    action_ref = _trackbooth_generated_action_ref(local_sync, "LinksController.create")
    _add_trackbooth_sync_responses(
        httpx_mock,
        _trackbooth_links_create_detail(),
        base_url="https://apis.trackbooth.com",
    )
    prod_sync = _sync_trackbooth_catalog(session, project_id, prod_ref)
    assert _trackbooth_generated_action_ref(prod_sync, "LinksController.create") == action_ref
    from stackos.repositories.plugins import PluginRepository

    listed_refs = [
        action.action_ref
        for action in PluginRepository(session).list_actions(
            plugin_slug="trackbooth",
            project_id=project_id,
        )
    ]
    assert listed_refs.count(action_ref) == 1

    ExecutionContextRepository(session).create(
        project_id=project_id,
        context_ref="ctx_generated_local",
        name="Generated local context",
        plugin_slug="trackbooth",
        provider_key="trackbooth",
        action_ref=action_ref,
        credential_ref=local_ref,
        credential_locked=True,
    )
    ExecutionContextRepository(session).create(
        project_id=project_id,
        context_ref="ctx_generated_prod",
        name="Generated production context",
        plugin_slug="trackbooth",
        provider_key="trackbooth",
        action_ref=action_ref,
        credential_ref=prod_ref,
        credential_locked=True,
    )
    httpx_mock.add_response(
        method="POST",
        url="https://trackbooth.local.test/api/links",
        json={"data": {"id": "link-local-context"}},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://apis.trackbooth.com/api/links",
        json={"data": {"id": "link-prod-context"}},
    )

    local_out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref=action_ref,
            context_ref="ctx_generated_local",
            input_json={
                "body": {
                    "campaign_id": "campaign-1",
                    "name": "Local context link",
                    "routing_mode": "direct",
                },
            },
        )
    ).data
    prod_out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref=action_ref,
            context_ref="ctx_generated_prod",
            input_json={
                "body": {
                    "campaign_id": "campaign-1",
                    "name": "Production context link",
                    "routing_mode": "direct",
                },
            },
        )
    ).data

    assert local_out.output_json["data"] == {"data": {"id": "link-local-context"}}
    assert prod_out.output_json["data"] == {"data": {"id": "link-prod-context"}}
    requests = [
        request for request in httpx_mock.get_requests() if request.url.path == "/api/links"
    ]
    assert [str(request.url) for request in requests] == [
        "https://trackbooth.local.test/api/links",
        "https://apis.trackbooth.com/api/links",
    ]
