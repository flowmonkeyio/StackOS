"""Shared fixtures for Trackbooth generated-action integration tests."""

from __future__ import annotations

import asyncio

from pytest_httpx import HTTPXMock
from sqlmodel import Session

from stackos.actions import (
    ActionRepository,
)
from stackos.repositories.projects import (
    IntegrationCredentialRepository,
)


def _trackbooth_credential_ref(
    session: Session,
    project_id: int,
    *,
    api_base_url: str = "https://trackbooth.local.test",
    profile_key: str = "default",
) -> str:
    from stackos.auth_providers import AuthRepository
    from stackos.repositories.plugins import PluginRepository

    PluginRepository(session).get_plugin("trackbooth")
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="trackbooth",
        profile_key=profile_key,
        secret_payload=b'{"api_key":"tb-test-key"}',
        config_json={"label": "Trackbooth test", "api_base_url": api_base_url},
    )
    status = AuthRepository(session).status(project_id=project_id, provider_key="trackbooth")
    for connection in status.connections:
        if connection.profile_key == profile_key:
            return connection.credential_ref
    raise AssertionError(f"missing Trackbooth credential profile {profile_key!r}")


def _trackbooth_links_create_detail() -> dict:
    return {
        "operation_id": "LinksController.create",
        "name": "links_create",
        "method": "POST",
        "path": "/api/links",
        "context": {
            "title": "Create link",
            "category": "links",
            "tags": ["links", "create"],
        },
        "body_schema": {
            "component_name": "CreateLinkBody",
            "details": {
                "type": "object",
                "fields": [
                    {"name": "campaign_id", "type": "string", "required": True},
                    {"name": "name", "type": "string", "required": True},
                    {
                        "name": "routing_mode",
                        "type": "'direct' | 'rules'",
                        "required": True,
                    },
                    {"name": "offer_id", "type": "string", "required": False},
                    {
                        "name": "redirect_type",
                        "type": "'301' | '302' | 'meta' | 'javascript'",
                        "required": False,
                    },
                ],
            },
        },
        "response_schema": {
            "component_name": "ApiOkResponse<LinkDetailItem>",
            "details": {
                "type": "object",
                "fields": [{"name": "data", "type": "LinkDetailItem", "required": True}],
            },
        },
    }


def _trackbooth_offers_findbyid_detail() -> dict:
    return {
        "operation_id": "OffersController.findById",
        "name": "offers_findbyid",
        "method": "GET",
        "path": "/api/offers/:id",
        "context": {
            "title": "Get offer",
            "category": "offers",
            "tags": ["offers"],
        },
        "query_schema": {
            "component_name": "FindOfferQuery",
            "details": {
                "type": "object",
                "fields": [
                    {"name": "include", "type": "string[]", "required": False},
                    {"name": "active", "type": "boolean", "required": False},
                ],
            },
        },
        "response_schema": {
            "component_name": "ApiOkResponse<OfferDetailItem>",
            "details": {
                "type": "object",
                "fields": [{"name": "data", "type": "OfferDetailItem", "required": True}],
            },
        },
    }


def _trackbooth_offers_update_detail() -> dict:
    return {
        "operation_id": "OffersController.update",
        "name": "offers_update",
        "method": "PATCH",
        "path": "/api/offers/:id",
        "context": {
            "title": "Update offer",
            "category": "offers",
            "tags": ["offers", "update"],
        },
        "path_params": [{"name": "id"}],
        "body_schema": {
            "component_name": "UpdateOfferBody",
            "details": {
                "type": "object",
                "fields": [
                    {"name": "name", "type": "string", "required": False},
                    {"name": "active", "type": "boolean", "required": False},
                ],
            },
        },
        "response_schema": {
            "component_name": "ApiOkResponse<OfferDetailItem>",
            "details": {
                "type": "object",
                "fields": [{"name": "data", "type": "OfferDetailItem", "required": True}],
            },
        },
    }


def _trackbooth_reporting_aggregate_detail() -> dict:
    metrics = [
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
    dimensions = ["offer", "campaign", "country", "device", "link"]
    return {
        "operation_id": "ReportingController.getAggregate",
        "name": "reporting_getaggregate",
        "method": "POST",
        "path": "/api/reporting/aggregate",
        "context": {
            "title": "View aggregate report",
            "subtitle": "Shows rolled-up performance totals for the selected report filters.",
            "category": "reporting",
            "tags": ["reporting", "aggregate", "performance"],
        },
        "body_schema": {
            "component_name": "ReportingAggregateBody",
            "details": {
                "type": "object",
                "fields": [
                    {
                        "name": "report_type",
                        "type": "enum",
                        "required": True,
                        "enum_values": ["offer", "campaign", "country"],
                    },
                    {"name": "filters", "type": "object", "required": True},
                    {
                        "name": "drilldown_path",
                        "type": "readonly ReportingInvestigationDimensionKey[]",
                        "required": False,
                        "enum_values": dimensions,
                    },
                    {
                        "name": "group_by",
                        "type": "Array<'offer' | 'campaign' | 'country' | 'device' | 'link'>",
                        "required": True,
                        "enum_values": dimensions,
                    },
                    {
                        "name": "metrics",
                        "type": (
                            "Array<'clicks' | 'unique_clicks' | 'leads' | 'sales' | "
                            "'revenue' | 'payout' | 'margin' | 'margin_rate' | 'epc' | "
                            "'epl' | 'cpl' | 'cpa' | 'lead_cr' | 'sale_cr' | "
                            "'lead_to_sale_cr'>"
                        ),
                        "required": True,
                        "validations": ["min(1)"],
                        "enum_values": metrics,
                    },
                ],
                "type_script": (
                    "type reportingAggregateSchema = { group_by: "
                    "Array<'offer' | 'campaign'>; metrics: Array<'clicks' | "
                    "'unique_clicks'>; };"
                ),
            },
        },
        "response_schema": {
            "component_name": "ApiOkResponse<ReportingAggregateResponse>",
            "details": {
                "type": "object",
                "fields": [
                    {"name": "report_type", "type": "string", "required": True},
                    {"name": "group_by", "type": "readonly string[]", "required": True},
                    {"name": "rows", "type": "readonly ReportingAggregateRow[]", "required": True},
                ],
            },
        },
    }


def _trackbooth_reporting_list_metrics_detail(entity: str) -> dict:
    title_entity = entity.removesuffix("s").capitalize()
    return {
        "operation_id": f"ReportingController.get{title_entity}ListMetrics",
        "name": f"reporting_get{title_entity.lower()}listmetrics",
        "method": "POST",
        "path": f"/api/reporting/list-metrics/{entity}",
        "context": {
            "title": f"Get {title_entity} list metrics",
            "category": "reporting",
            "tags": ["reporting", "list-metrics", entity],
        },
        "body_schema": {
            "component_name": f"Reporting{title_entity}ListMetricsBody",
            "details": {
                "type": "object",
                "fields": [
                    {"name": "row_ids", "type": "string[]", "required": True},
                    {"name": "filters", "type": "object", "required": True},
                ],
            },
        },
    }


def _trackbooth_dashboard_detail() -> dict:
    return {
        "operation_id": "DashboardController.getDashboard",
        "name": "dashboard_getdashboard",
        "method": "GET",
        "path": "/api/dashboard",
        "context": {
            "title": "View dashboard",
            "category": "reporting",
            "tags": ["dashboard", "reporting", "top"],
        },
        "query_schema": {
            "component_name": "DashboardQuery",
            "details": {
                "type": "object",
                "fields": [
                    {
                        "name": "include",
                        "type": "Array<'kpis' | 'top_offers' | 'top_campaigns'>",
                        "required": False,
                        "enum_values": ["kpis", "top_offers", "top_campaigns"],
                    },
                ],
            },
        },
        "response_schema": {
            "component_name": "ApiOkResponse<DashboardResult>",
            "details": {
                "type": "object",
                "fields": [
                    {"name": "top_offers", "type": "DashboardTopOfferItem[]", "required": True},
                    {
                        "name": "top_campaigns",
                        "type": "DashboardTopCampaignItem[]",
                        "required": True,
                    },
                ],
            },
        },
    }


def _trackbooth_api_key_reveal_detail() -> dict:
    return {
        "operation_id": "AccountApiKeyController.revealApiKey",
        "name": "accountapikey_revealapikey",
        "method": "GET",
        "path": "/api/accounts/:id/api-key",
        "context": {
            "title": "Reveal account API key",
            "category": "accounts",
            "tags": ["accounts"],
        },
    }


def _trackbooth_api_key_generate_detail() -> dict:
    return {
        "operation_id": "AccountApiKeyController.generateApiKey",
        "name": "accountapikey_generateapikey",
        "method": "POST",
        "path": "/api/accounts/:id/api-key/generate",
        "context": {
            "title": "Generate account API key",
            "category": "accounts",
            "tags": ["accounts"],
        },
    }


def _trackbooth_account_listaccounts_detail() -> dict:
    return {
        "operation_id": "AccountController.listAccounts",
        "name": "account_listaccounts",
        "method": "GET",
        "path": "/api/accounts",
        "context": {
            "title": "List accounts",
            "category": "accounts",
            "tags": ["accounts"],
        },
        "query_schema": {"type": "object", "additionalProperties": False},
        "response_schema": {"type": "object", "additionalProperties": True},
    }


def _add_trackbooth_sync_responses(
    httpx_mock: HTTPXMock,
    *details: dict,
    base_url: str = "https://trackbooth.local.test",
    catalog_hash: str = "test-catalog-hash",
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{base_url}/api/agent-api/catalog/export",
        json={
            "status": "ok",
            "data": {
                "version": 1,
                "generated_at": "2026-06-04T00:00:00Z",
                "catalog_hash": catalog_hash,
                "endpoint_count": len(details),
                "endpoints": list(details),
            },
        },
    )


def _sync_trackbooth_catalog(
    session: Session,
    project_id: int,
    credential_ref: str,
    *,
    operation_ids: list[str] | None = None,
    acting_as_account: str | None = None,
) -> dict:
    input_json: dict = {}
    if operation_ids:
        input_json["operation_ids"] = operation_ids
    return asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="trackbooth.catalog.sync",
            input_json=input_json,
            provider_context_json={"acting_as_account": acting_as_account}
            if acting_as_account
            else None,
            credential_ref=credential_ref,
        )
    ).data.output_json


def _trackbooth_generated_action_ref(sync_output: dict, operation_id: str) -> str:
    operation_ids = sync_output["operation_ids"]
    index = operation_ids.index(operation_id)
    action_ref = sync_output["action_refs"][index]
    assert action_ref.startswith("trackbooth.api.")
    return action_ref
