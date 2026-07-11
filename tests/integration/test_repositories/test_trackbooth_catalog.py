"""Trackbooth catalog discovery and manifest projection tests."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.actions import (
    ActionRepository,
)
from stackos.db.models import (
    Action,
)
from stackos.mcp.context import MCPContext
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.actions import ActionListInput, action_list
from stackos.repositories.base import (
    NotFoundError,
)
from tests.integration.test_repositories.trackbooth_test_support import (
    _add_trackbooth_sync_responses,
    _sync_trackbooth_catalog,
    _trackbooth_credential_ref,
    _trackbooth_dashboard_detail,
    _trackbooth_generated_action_ref,
    _trackbooth_links_create_detail,
    _trackbooth_offers_findbyid_detail,
    _trackbooth_offers_update_detail,
    _trackbooth_reporting_aggregate_detail,
    _trackbooth_reporting_list_metrics_detail,
)


def test_trackbooth_catalog_search_filters_live_catalog_and_uses_api_key_header(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    httpx_mock.add_response(
        method="GET",
        url="https://trackbooth.local.test/api/agent-api/catalog",
        json={
            "data": [
                {
                    "operation_id": "LinksController.findAll",
                    "method": "GET",
                    "path": "/api/links",
                    "context": {
                        "title": "List links",
                        "category": "links",
                        "tags": ["links"],
                    },
                },
                {
                    "operation_id": "AdvertiserController.create",
                    "method": "POST",
                    "path": "/api/advertisers",
                    "context": {
                        "title": "Create advertiser",
                        "category": "advertisers",
                        "tags": ["advertisers"],
                    },
                },
            ]
        },
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="trackbooth.catalog.search",
            input_json={"query": "links", "limit": 10},
            credential_ref=credential_ref,
        )
    ).data

    assert out.output_json["tool_count"] == 2
    assert out.output_json["count"] == 1
    assert out.output_json["data"][0]["operation_id"] == "LinksController.findAll"
    request = httpx_mock.get_requests()[0]
    assert request.headers["X-API-Key"] == "tb-test-key"
    assert "X-Acting-As-Account" not in request.headers
    assert "tb-test-key" not in json.dumps(out.model_dump(mode="json"))


def test_trackbooth_operation_describe_expands_request_and_response_schema(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    httpx_mock.add_response(
        method="GET",
        url="https://trackbooth.local.test/api/agent-api/catalog/LinksController.create",
        json={"data": _trackbooth_links_create_detail()},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="trackbooth.operation.describe",
            input_json={"operation_id": "LinksController.create"},
            credential_ref=credential_ref,
        )
    ).data

    detail = out.output_json["data"]
    assert detail["method"] == "POST"
    assert detail["request_body"]["properties"]["routing_mode"]["enum"] == ["direct", "rules"]
    assert detail["request_body"]["properties"]["redirect_type"]["enum"] == [
        "301",
        "302",
        "meta",
        "javascript",
    ]
    assert detail["response"]["properties"]["data"]["x_trackbooth_type"] == "LinkDetailItem"


def test_trackbooth_read_operation_substitutes_path_and_serializes_query(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    _add_trackbooth_sync_responses(httpx_mock, _trackbooth_offers_findbyid_detail())
    sync_output = _sync_trackbooth_catalog(session, project_id, credential_ref)
    action_ref = _trackbooth_generated_action_ref(sync_output, "OffersController.findById")
    httpx_mock.add_response(method="GET", json={"data": {"id": "offer-123"}})

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref=action_ref,
            input_json={
                "path_params": {"id": "offer-123"},
                "query": {"include": ["payouts", "localizations"], "active": True},
            },
            credential_ref=credential_ref,
        )
    ).data

    assert out.output_json["status_code"] == 200
    actual = httpx_mock.get_requests()[1]
    assert actual.method == "GET"
    assert actual.url.path == "/api/offers/offer-123"
    assert parse_qs(actual.url.query.decode()) == {
        "include": ["payouts", "localizations"],
        "active": ["true"],
    }


def test_trackbooth_write_operation_sends_body_and_explicit_acting_as_header(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    _add_trackbooth_sync_responses(httpx_mock, _trackbooth_links_create_detail())
    sync_output = _sync_trackbooth_catalog(
        session,
        project_id,
        credential_ref,
        acting_as_account="acct-managed",
    )
    action_ref = _trackbooth_generated_action_ref(sync_output, "LinksController.create")
    httpx_mock.add_response(method="POST", json={"data": {"id": "link-1"}})

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref=action_ref,
            input_json={
                "body": {
                    "campaign_id": "campaign-1",
                    "name": "Rules link",
                    "routing_mode": "direct",
                    "offer_id": "offer-1",
                },
            },
            provider_context_json={"acting_as_account": "acct-managed"},
            credential_ref=credential_ref,
        )
    ).data

    assert out.output_json["data"] == {"data": {"id": "link-1"}}
    actual = httpx_mock.get_requests()[1]
    assert actual.method == "POST"
    assert actual.url.path == "/api/links"
    assert actual.headers["X-API-Key"] == "tb-test-key"
    assert actual.headers["X-Acting-As-Account"] == "acct-managed"
    assert json.loads(actual.content) == {
        "campaign_id": "campaign-1",
        "name": "Rules link",
        "routing_mode": "direct",
        "offer_id": "offer-1",
    }


def test_trackbooth_catalog_sync_creates_runtime_generated_actions(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    from stackos.repositories.plugins import PluginRepository

    credential_ref = _trackbooth_credential_ref(session, project_id)
    _add_trackbooth_sync_responses(
        httpx_mock,
        _trackbooth_links_create_detail(),
        _trackbooth_offers_findbyid_detail(),
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="trackbooth.catalog.sync",
            input_json={},
            credential_ref=credential_ref,
        )
    ).data

    assert out.output_json["synced"] == 2
    assert out.output_json["created"] == 2
    assert out.output_json["updated"] == 0
    assert out.output_json["skipped"] == 0
    assert out.output_json["retired"] == 0
    assert out.output_json["detail_fetch_count"] == 0
    assert isinstance(out.output_json["write_ms"], int)
    assert isinstance(out.output_json["total_ms"], int)
    assert out.output_json["blocked_operation_ids"] == []
    assert out.output_json["inventory_scope_key"].startswith("inv_")
    links_action_ref = _trackbooth_generated_action_ref(out.output_json, "LinksController.create")
    offers_action_ref = _trackbooth_generated_action_ref(
        out.output_json,
        "OffersController.findById",
    )
    assert {ref.removeprefix("trackbooth.") for ref in out.output_json["action_refs"]} == {
        "api.links_create",
        "api.offers_findbyid",
    }

    repo = ActionRepository(session)
    described = repo.describe(
        project_id=project_id,
        action_ref=links_action_ref,
    )
    listed_actions = PluginRepository(session).list_actions(
        plugin_slug="trackbooth",
        project_id=project_id,
    )
    listed_refs = {action.action_ref for action in listed_actions}
    listed_keys = {action.key for action in listed_actions}

    assert described.manifest.operation == "operation.execute"
    assert links_action_ref in listed_refs
    assert offers_action_ref in listed_refs
    assert "api.links_create" in listed_keys
    assert "api.offers_findbyid" in listed_keys
    assert not any(key.startswith("api.inv_") for key in listed_keys)
    stored_links_row = next(
        row
        for row in session.exec(select(Action)).all()
        if (row.config_json or {}).get("public_action_key") == "api.links_create"
    )
    assert stored_links_row.key.startswith("api.inv_")
    with pytest.raises(NotFoundError):
        repo.describe(project_id=project_id, action_ref=f"trackbooth.{stored_links_row.key}")
    with pytest.raises(NotFoundError):
        repo.validate(project_id=project_id, action_ref=f"trackbooth.{stored_links_row.key}")
    with pytest.raises(NotFoundError):
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref=f"trackbooth.{stored_links_row.key}",
                input_json={"body": {"url": "https://example.com", "routing_mode": "direct"}},
                credential_ref=credential_ref,
            )
        )
    assert described.manifest.config_json["inventory_source"] == "trackbooth.catalog.sync"
    assert described.manifest.config_json["inventory_state"] == "active"
    assert described.manifest.config_json["inventory_project_id"] == project_id
    assert described.manifest.config_json["inventory_credential_ref"] == credential_ref
    assert described.manifest.config_json["inventory_api_base_url"] == (
        "https://trackbooth.local.test"
    )
    assert "acting_as_account" not in described.manifest.input_schema_json["properties"]
    assert (
        described.manifest.provider_context_schema_json["properties"]["acting_as_account"]["type"]
        == "string"
    )
    repo.describe(project_id=project_id, action_ref=offers_action_ref)
    assert described.manifest.input_schema_json["properties"]["body"]["properties"]["routing_mode"][
        "enum"
    ] == ["direct", "rules"]
    assert (
        described.manifest.output_schema_json["properties"]["data"]["properties"]["data"][
            "x_trackbooth_type"
        ]
        == "LinkDetailItem"
    )


def test_trackbooth_catalog_sync_skips_unchanged_catalog_hash(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    detail = _trackbooth_links_create_detail()
    detail["checksum"] = "links-create-v1"
    _add_trackbooth_sync_responses(httpx_mock, detail, catalog_hash="catalog-v1")
    first = _sync_trackbooth_catalog(session, project_id, credential_ref)
    links_ref = _trackbooth_generated_action_ref(first, "LinksController.create")
    row = next(
        row
        for row in session.exec(select(Action)).all()
        if (row.config_json or {}).get("public_action_key") == links_ref.removeprefix("trackbooth.")
    )
    initial_updated_at = row.updated_at
    assert row.config_json["inventory_catalog_hash"] == "catalog-v1"
    assert row.config_json["inventory_endpoint_checksum"] == "links-create-v1"

    _add_trackbooth_sync_responses(httpx_mock, detail, catalog_hash="catalog-v1")
    second = _sync_trackbooth_catalog(session, project_id, credential_ref)

    assert second["synced"] == 1
    assert second["created"] == 0
    assert second["updated"] == 0
    assert second["skipped"] == 1
    assert second["pruned"] == 0
    assert second["retired"] == 0
    assert second["detail_fetch_count"] == 0
    assert isinstance(second["write_ms"], int)
    assert isinstance(second["total_ms"], int)
    session.refresh(row)
    assert row.updated_at == initial_updated_at


def test_trackbooth_catalog_sync_preserves_array_schemas_and_read_reporting_risk(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    _add_trackbooth_sync_responses(httpx_mock, _trackbooth_reporting_aggregate_detail())
    sync_output = _sync_trackbooth_catalog(session, project_id, credential_ref)
    action_ref = _trackbooth_generated_action_ref(sync_output, "ReportingController.getAggregate")

    repo = ActionRepository(session)
    described = repo.describe(project_id=project_id, action_ref=action_ref)
    body = described.manifest.input_schema_json["properties"]["body"]["properties"]

    assert described.manifest.risk_level == "read"
    assert body["group_by"]["type"] == "array"
    assert body["group_by"]["items"]["enum"] == ["offer", "campaign", "country", "device", "link"]
    assert body["metrics"]["type"] == "array"
    assert body["metrics"]["items"]["enum"] == [
        "clicks",
        "unique_clicks",
        "leads",
        "sales",
        "revenue",
        "payout",
        "margin",
        "margin_rate",
        "epc",
        "epl",
        "cpl",
        "cpa",
        "lead_cr",
        "sale_cr",
        "lead_to_sale_cr",
    ]
    assert body["drilldown_path"]["type"] == "array"
    assert (
        described.manifest.output_schema_json["properties"]["data"]["properties"]["group_by"][
            "type"
        ]
        == "array"
    )

    validation = repo.validate(
        project_id=project_id,
        action_ref=action_ref,
        credential_ref=credential_ref,
        input_json={
            "body": {
                "report_type": "offer",
                "filters": {},
                "group_by": ["offer"],
                "metrics": ["bogus"],
            }
        },
    )

    assert any(
        issue.path == "$.body.metrics[0]" and issue.code == "enum_mismatch"
        for issue in validation.issues
    )


def test_trackbooth_catalog_sync_uses_explicit_catalog_risk_before_semantics(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    detail = _trackbooth_reporting_aggregate_detail()
    detail["read_only"] = False
    _add_trackbooth_sync_responses(httpx_mock, detail)
    sync_output = _sync_trackbooth_catalog(session, project_id, credential_ref)
    action_ref = _trackbooth_generated_action_ref(sync_output, "ReportingController.getAggregate")

    described = ActionRepository(session).describe(project_id=project_id, action_ref=action_ref)

    assert described.manifest.risk_level == "write"


def test_trackbooth_catalog_sync_keeps_idempotent_post_as_write_risk(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    detail = _trackbooth_offers_update_detail()
    detail["idempotent"] = True
    _add_trackbooth_sync_responses(httpx_mock, detail)
    sync_output = _sync_trackbooth_catalog(session, project_id, credential_ref)
    action_ref = _trackbooth_generated_action_ref(sync_output, "OffersController.update")

    described = ActionRepository(session).describe(project_id=project_id, action_ref=action_ref)

    assert described.manifest.risk_level == "write"


def test_action_list_search_matches_generated_schema_fields_and_unordered_terms(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    _add_trackbooth_sync_responses(
        httpx_mock,
        _trackbooth_dashboard_detail(),
        _trackbooth_reporting_list_metrics_detail("campaigns"),
        _trackbooth_reporting_list_metrics_detail("offers"),
        _trackbooth_reporting_list_metrics_detail("links"),
        _trackbooth_offers_update_detail(),
    )
    _sync_trackbooth_catalog(session, project_id, credential_ref)
    ctx = MCPContext(
        session=session,
        request_id="test-action-list-search",
        project_id=project_id,
    )
    emitter = ProgressEmitter(None, None)

    top_offers = asyncio.run(
        action_list(
            ActionListInput(project_id=project_id, plugin_slug="trackbooth", query="top offers"),
            ctx,
            emitter,
        )
    )
    list_metrics = asyncio.run(
        action_list(
            ActionListInput(
                project_id=project_id,
                plugin_slug="trackbooth",
                query="list metrics campaign offer link reporting",
            ),
            ctx,
            emitter,
        )
    )
    offer_update = asyncio.run(
        action_list(
            ActionListInput(project_id=project_id, plugin_slug="trackbooth", query="offer update"),
            ctx,
            emitter,
        )
    )

    assert "trackbooth.api.dashboard_getdashboard" in {item.action_ref for item in top_offers.items}
    list_metric_refs = {item.action_ref for item in list_metrics.items}
    assert {
        "trackbooth.api.reporting_getcampaignlistmetrics",
        "trackbooth.api.reporting_getofferlistmetrics",
        "trackbooth.api.reporting_getlinklistmetrics",
    } <= list_metric_refs
    assert "trackbooth.api.offers_update" in {item.action_ref for item in offer_update.items}
