"""Trackbooth generated inventory lifecycle and scoping tests."""

from __future__ import annotations

import asyncio

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.actions import (
    ActionRepository,
)
from stackos.db.models import (
    Action,
    ActionCall,
)
from stackos.repositories.base import (
    ConflictError,
    NotFoundError,
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
        profile_key="local",
    )
    prod_ref = _trackbooth_credential_ref(
        session,
        project_id,
        api_base_url="https://apis.trackbooth.com",
        profile_key="prod",
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
        profile_key="local",
    )
    prod_ref = _trackbooth_credential_ref(
        session,
        project_id,
        api_base_url="https://apis.trackbooth.com",
        profile_key="prod",
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
