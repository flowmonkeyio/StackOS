"""Repository tests for the StackOS internal action executor."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.actions import (
    ActionConnectorRegistry,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionRepository,
    ActionValidationIssue,
)
from stackos.db.models import (
    Action,
    ActionCall,
    Credential,
    CredentialUsageEvent,
    Plugin,
    PluginSource,
    Provider,
)
from stackos.mcp.context import MCPContext
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.actions import ActionListInput, action_list
from stackos.repositories.base import (
    BudgetExceededError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from stackos.repositories.execution_contexts import ExecutionContextRepository
from stackos.repositories.projects import (
    IntegrationBudgetRepository,
    IntegrationCredentialRepository,
    ProjectRepository,
)
from stackos.repositories.resources import ResourceRepository
from stackos.repositories.run_plans import RunPlanRepository


def _png_header(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


def _webp_header() -> bytes:
    payload = b"\x00\x00\x00\x00" + (31).to_bytes(3, "little") + (31).to_bytes(3, "little")
    return b"RIFF" + (22).to_bytes(4, "little") + b"WEBPVP8X" + (10).to_bytes(4, "little") + payload


class _FakeConnector:
    key = "fake.echo"

    def __init__(self) -> None:
        self.calls = 0
        self.saw_secret: bytes | None = None
        self.saw_provider_context: dict | None = None

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        if "name" not in request.input_json:
            return [
                ActionValidationIssue(
                    path="$.name",
                    message="name is required",
                    code="required",
                )
            ]
        return []

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 12

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        self.calls += 1
        assert request.credential is not None
        self.saw_secret = request.credential.secret_payload
        self.saw_provider_context = request.provider_context_json
        return ActionConnectorResult(
            output_json={
                "echo": request.input_json,
                "authorization": "Bearer leaked-token",
                "nested": {"api_key": "sk-leak"},
            },
            metadata_json={"safe": "ok", "refresh_token": "rt-leak"},
            cost_cents=34,
        )


class _NoAuthConnector:
    key = "fake.noauth"

    def __init__(self) -> None:
        self.calls = 0
        self.saw_credential = False

    def validate(self, _request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        return []

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        self.calls += 1
        self.saw_credential = request.credential is not None
        return ActionConnectorResult(output_json={"ok": True})


SITEMAP_URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.com/a</loc>
    <lastmod>2026-05-22</lastmod>
  </url>
  <url>
    <loc>https://example.com/b</loc>
  </url>
</urlset>
"""


def _api_key_auth_methods() -> dict:
    return {
        "auth_methods": [
            {
                "key": "api_key",
                "label": "API key",
                "auth_type": "api-key",
                "payload_format": "raw",
                "payload_field": "api_key",
                "fields": [
                    {
                        "key": "api_key",
                        "label": "API Key",
                        "type": "secret",
                        "secret": True,
                        "required": True,
                    }
                ],
            }
        ]
    }


def _seed_action(session: Session) -> None:
    plugin = Plugin(
        slug="test-actions",
        name="Test Actions",
        version="0.1.0",
        description="Test plugin",
        source=PluginSource.PROJECT,
        manifest_json={},
    )
    session.add(plugin)
    session.flush()
    assert plugin.id is not None
    provider = Provider(
        plugin_id=plugin.id,
        key="fake-provider",
        name="Fake Provider",
        auth_type="api-key",
        config_json=_api_key_auth_methods(),
    )
    session.add(provider)
    session.flush()
    assert provider.id is not None
    session.add(
        Action(
            plugin_id=plugin.id,
            provider_id=provider.id,
            key="echo.run",
            name="Echo Run",
            description="Echo explicit input.",
            capability_key=None,
            risk_level="write",
            input_schema_json={
                "type": "object",
                "additionalProperties": False,
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            },
            output_schema_json={"type": "object", "additionalProperties": True},
            config_json={
                "schema_version": "stackos.action.v1",
                "connector": "fake.echo",
                "operation": "echo.run",
                "requires_credential": True,
            },
        )
    )
    session.commit()


def _seed_noauth_action(session: Session) -> None:
    plugin = session.exec(select(Plugin).where(Plugin.slug == "test-actions")).one()
    assert plugin.id is not None
    session.add(
        Action(
            plugin_id=plugin.id,
            provider_id=None,
            key="noauth.run",
            name="No Auth Run",
            description="No-auth action for scope tests.",
            risk_level="read",
            input_schema_json={"type": "object", "additionalProperties": True},
            output_schema_json={"type": "object", "additionalProperties": True},
            config_json={
                "schema_version": "stackos.action.v1",
                "connector": "fake.noauth",
                "operation": "noauth.run",
                "requires_credential": False,
            },
        )
    )
    session.commit()


def _seed_http_action(session: Session) -> None:
    plugin = session.exec(select(Plugin).where(Plugin.slug == "test-actions")).one()
    assert plugin.id is not None
    provider = Provider(
        plugin_id=plugin.id,
        key="internal-webhook",
        name="Internal Webhook",
        auth_type="api-key",
        config_json=_api_key_auth_methods(),
    )
    session.add(provider)
    session.flush()
    assert provider.id is not None
    session.add(
        Action(
            plugin_id=plugin.id,
            provider_id=provider.id,
            key="webhook.trigger",
            name="Trigger Webhook",
            description="Trigger a static internal webhook.",
            risk_level="write",
            input_schema_json={
                "type": "object",
                "additionalProperties": False,
                "required": ["task"],
                "properties": {"task": {"type": "string"}},
            },
            output_schema_json={"type": "object", "additionalProperties": True},
            config_json={
                "schema_version": "stackos.action.v1",
                "connector": "http",
                "operation": "request",
                "requires_credential": True,
                "http": {
                    "method": "POST",
                    "url": "https://hooks.example/internal/run",
                    "auth": {"type": "bearer"},
                    "request_mode": "json",
                    "response_mode": "json",
                    "headers": {"Content-Type": "application/json"},
                },
            },
        )
    )
    session.commit()


def _credential_ref(session: Session, project_id: int) -> str:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="fake-provider",
        secret_payload=b"daemon-only-secret",
        config_json={"label": "Fake"},
    )
    from stackos.auth_providers import AuthRepository

    status = AuthRepository(session).status(project_id=project_id, provider_key="fake-provider")
    return status.connections[0].credential_ref


def _enable_workspace_provider_context_schema(session: Session) -> None:
    action = session.exec(select(Action).where(Action.key == "echo.run")).one()
    action.config_json = {
        **action.config_json,
        "provider_context_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["workspace_id"],
            "properties": {
                "workspace_id": {"type": "string"},
                "surface": {"type": "string", "enum": ["default", "analysis"]},
            },
        },
    }
    session.add(action)
    session.commit()


def _provider_credential_ref(session: Session, project_id: int, provider_key: str) -> str:
    from stackos.auth_providers import AuthRepository

    status = AuthRepository(session).status(project_id=project_id, provider_key=provider_key)
    return status.connections[0].credential_ref


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


def test_action_execute_resolves_secret_internally_and_redacts_audit(
    session: Session,
    project_id: int,
) -> None:
    _seed_action(session)
    credential_ref = _credential_ref(session, project_id)
    fake = _FakeConnector()
    registry = ActionConnectorRegistry()
    registry.register(fake)

    out = asyncio.run(
        ActionRepository(session, connectors=registry).execute(
            project_id=project_id,
            action_ref="test-actions.echo.run",
            input_json={"name": "Ada"},
            credential_ref=credential_ref,
            metadata_json={"access_token": "meta-leak", "safe": "yes"},
        )
    ).data

    assert fake.calls == 1
    assert fake.saw_secret == b"daemon-only-secret"
    assert out.output_json == {
        "echo": {"name": "Ada"},
        "authorization": "[redacted]",
        "nested": {"api_key": "[redacted]"},
    }
    assert out.metadata_json == {
        "access_token": "[redacted]",
        "safe": "ok",
        "refresh_token": "[redacted]",
    }
    assert out.action_call.credential_ref == credential_ref
    assert out.action_call.request_json == {"name": "Ada"}
    assert out.action_call.response_json == out.output_json
    assert out.cost_cents == 34
    assert "credential_id" not in out.model_dump(mode="json")["action_call"]
    assert "daemon-only-secret" not in json.dumps(out.model_dump(mode="json"))

    call = session.exec(select(ActionCall)).one()
    usage = session.exec(select(CredentialUsageEvent)).one()
    assert call.id == out.action_call.id
    assert call.credential_ref == credential_ref
    assert usage.operation == "action.test-actions.echo.run"
    assert "daemon-only-secret" not in json.dumps(call.request_json)
    assert "daemon-only-secret" not in json.dumps(call.response_json)


def test_provider_context_is_manifest_typed_passed_to_connector_and_audited(
    session: Session,
    project_id: int,
) -> None:
    _seed_action(session)
    credential_ref = _credential_ref(session, project_id)
    fake = _FakeConnector()
    registry = ActionConnectorRegistry()
    registry.register(fake)
    repo = ActionRepository(session, connectors=registry)

    rejected = repo.validate(
        project_id=project_id,
        action_ref="test-actions.echo.run",
        input_json={"name": "Ada"},
        provider_context_json={"workspace_id": "ws_1"},
        credential_ref=credential_ref,
    )
    assert any(issue.code == "provider_context_not_allowed" for issue in rejected.issues)

    action = session.exec(select(Action).where(Action.key == "echo.run")).one()
    action.config_json = {
        **action.config_json,
        "provider_context_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"workspace_id": {"type": "string"}},
        },
    }
    session.add(action)
    session.commit()

    invalid = repo.validate(
        project_id=project_id,
        action_ref="test-actions.echo.run",
        input_json={"name": "Ada"},
        provider_context_json={"workspace_id": "ws_1", "extra": "no"},
        credential_ref=credential_ref,
    )
    assert any(
        issue.path == "$.provider_context_json.extra" and issue.code == "additional_property"
        for issue in invalid.issues
    )

    out = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="test-actions.echo.run",
            input_json={"name": "Ada"},
            provider_context_json={"workspace_id": "ws_1"},
            credential_ref=credential_ref,
        )
    ).data

    assert fake.saw_provider_context == {"workspace_id": "ws_1"}
    assert out.action_call.provider_context_json == {"workspace_id": "ws_1"}
    call = session.exec(select(ActionCall).where(ActionCall.id == out.action_call.id)).one()
    assert call.provider_context_json == {"workspace_id": "ws_1"}


def test_action_execute_uses_context_ref_defaults_for_credential_provider_context_and_audit(
    session: Session,
    project_id: int,
) -> None:
    _seed_action(session)
    _enable_workspace_provider_context_schema(session)
    credential_ref = _credential_ref(session, project_id)
    ExecutionContextRepository(session).create(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        name="Provider analysis context",
        action_ref="test-actions.echo.run",
        credential_ref=credential_ref,
        provider_context_json={"workspace_id": "ws_1", "surface": "analysis"},
        output_policy_json={"mode": "file_if_large", "max_inline_bytes": 16000},
        request_budget_json={"max_parallel": 3},
        artifact_namespace="provider-analysis",
    )
    fake = _FakeConnector()
    registry = ActionConnectorRegistry()
    registry.register(fake)
    repo = ActionRepository(session, connectors=registry)

    validation = repo.validate(
        project_id=project_id,
        action_ref="test-actions.echo.run",
        input_json={"name": "Ada"},
        context_ref="ctx_provider_analysis",
    )
    assert validation.valid is True
    assert validation.credential_ref == credential_ref

    out = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="test-actions.echo.run",
            input_json={"name": "Ada"},
            context_ref="ctx_provider_analysis",
        )
    ).data

    assert fake.saw_provider_context == {"workspace_id": "ws_1", "surface": "analysis"}
    assert out.credential_ref == credential_ref
    assert out.action_call.provider_context_json == {
        "workspace_id": "ws_1",
        "surface": "analysis",
    }
    assert out.action_call.metadata_json["execution_context"] == {
        "context_ref": "ctx_provider_analysis",
        "output_policy_json": {"mode": "file_if_large", "max_inline_bytes": 16000},
        "request_budget_json": {"max_parallel": 3},
        "artifact_namespace": "provider-analysis",
    }


def test_action_execute_file_backs_context_output_and_registers_artifact(
    session: Session,
    project_id: int,
    tmp_path: Path,
) -> None:
    _seed_action(session)
    credential_ref = _credential_ref(session, project_id)
    ExecutionContextRepository(session).create(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        name="Provider analysis context",
        action_ref="test-actions.echo.run",
        credential_ref=credential_ref,
        output_policy_json={"mode": "always_file", "semantic_name": "analysis-output"},
        artifact_namespace="provider-analysis",
    )
    fake = _FakeConnector()
    registry = ActionConnectorRegistry()
    registry.register(fake)
    repo = ActionRepository(session, connectors=registry, asset_dir=tmp_path)

    out = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="test-actions.echo.run",
            input_json={"name": "Ada"},
            context_ref="ctx_provider_analysis",
        )
    ).data

    assert out.output_json["output_mode"] == "file"
    pointer = out.output_json["file"]
    assert pointer["absolute_path"].startswith(str(tmp_path))
    assert pointer["uri"].startswith("/generated-assets/action-outputs/project-")
    assert pointer["semantic_name"].startswith("analysis-output_")
    assert pointer["bytes"] > 0
    assert pointer["top_level_shape"]["keys"] == ["authorization", "echo", "nested"]
    assert pointer["read"]["operation"] == "executionContext.artifact.read"
    assert pointer["read"]["arguments"] == {
        "context_ref": "ctx_provider_analysis",
        "json_path": "$.authorization",
    }
    assert pointer["read"]["json_path_examples"] == [
        "$",
        "$.authorization",
        "$.echo",
        "$.nested",
    ]
    assert "without rerunning" in pointer["read"]["instructions"]
    saved = json.loads(Path(pointer["absolute_path"]).read_text(encoding="utf-8"))
    assert saved == {
        "echo": {"name": "Ada"},
        "authorization": "[redacted]",
        "nested": {"api_key": "[redacted]"},
    }
    context_artifacts = ExecutionContextRepository(session).list_artifacts(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        action_ref="test-actions.echo.run",
    )
    assert context_artifacts.total_estimate == 1
    registered = context_artifacts.items[0]
    assert registered.artifact_id == pointer["artifact_id"]
    assert registered.action_call_id == out.action_call.id
    assert registered.semantic_name == pointer["semantic_name"]
    assert registered.action_ref == "test-actions.echo.run"
    assert registered.artifact["metadata_json"]["sha256"] == pointer["sha256"]
    assert out.action_call.response_json == out.output_json
    assert (
        out.action_call.metadata_json["file_backed_output"]["artifact_id"] == pointer["artifact_id"]
    )


def test_action_validate_rejects_context_locked_provider_context_override(
    session: Session,
    project_id: int,
) -> None:
    _seed_action(session)
    _enable_workspace_provider_context_schema(session)
    credential_ref = _credential_ref(session, project_id)
    ExecutionContextRepository(session).create(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        name="Provider analysis context",
        action_ref="test-actions.echo.run",
        credential_ref=credential_ref,
        provider_context_json={"workspace_id": "ws_1"},
        provider_context_locked_fields_json=["workspace_id"],
    )
    registry = ActionConnectorRegistry()
    registry.register(_FakeConnector())
    repo = ActionRepository(session, connectors=registry)

    validation = repo.validate(
        project_id=project_id,
        action_ref="test-actions.echo.run",
        input_json={"name": "Ada"},
        context_ref="ctx_provider_analysis",
        provider_context_json={"workspace_id": "ws_2"},
    )

    assert validation.valid is False
    assert any(
        issue.path == "$.provider_context_json.workspace_id"
        and issue.code == "execution_context_field_locked"
        for issue in validation.issues
    )


def test_action_execute_rejects_failed_credential_profile(
    session: Session,
    project_id: int,
) -> None:
    _seed_action(session)
    credential_ref = _credential_ref(session, project_id)
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    credential.status = "failed"
    session.add(credential)
    session.commit()
    fake = _FakeConnector()
    registry = ActionConnectorRegistry()
    registry.register(fake)

    with pytest.raises(ValidationError) as excinfo:
        asyncio.run(
            ActionRepository(session, connectors=registry).execute(
                project_id=project_id,
                action_ref="test-actions.echo.run",
                input_json={"name": "Ada"},
                credential_ref=credential_ref,
            )
        )

    assert fake.calls == 0
    assert excinfo.value.data["issues"][0]["code"] == "credential_not_connected"


def test_action_execute_idempotency_replays_without_second_connector_call(
    session: Session,
    project_id: int,
) -> None:
    _seed_action(session)
    credential_ref = _credential_ref(session, project_id)
    fake = _FakeConnector()
    registry = ActionConnectorRegistry()
    registry.register(fake)
    repo = ActionRepository(session, connectors=registry)

    first = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="test-actions.echo.run",
            input_json={"name": "Ada"},
            credential_ref=credential_ref,
            idempotency_key="same-action",
        )
    ).data
    second = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="test-actions.echo.run",
            input_json={"name": "Ada"},
            credential_ref=credential_ref,
            idempotency_key="same-action",
        )
    ).data

    assert fake.calls == 1
    assert first.replayed is False
    assert second.replayed is True
    assert second.action_call.id == first.action_call.id
    assert len(session.exec(select(ActionCall)).all()) == 1


def test_builtin_action_connectors_describe_availability(session: Session) -> None:
    action_refs = {
        "utils.image.generate": ("openai-images", True, "unknown"),
        "utils.xai.image.generate": ("xai-imagine", True, "unknown"),
        "utils.xai.image.edit": ("xai-imagine", True, "unknown"),
        "utils.xai.video.generate": ("xai-imagine", True, "unknown"),
        "utils.reve.image.generate": ("reve", True, "unknown"),
        "utils.reve.image.edit": ("reve", True, "unknown"),
        "utils.reve.image.remix": ("reve", True, "unknown"),
        "utils.google.image.generate": ("google-gemini-image", True, "unknown"),
        "utils.google.image.edit": ("google-gemini-image", True, "unknown"),
        "utils.google.video.generate": ("google-veo", True, "unknown"),
        "utils.ideogram.image.generate": ("ideogram", True, "unknown"),
        "utils.ideogram.image.remix": ("ideogram", True, "unknown"),
        "utils.byteplus.image.generate": ("byteplus-seedream", True, "unknown"),
        "utils.byteplus.image.edit": ("byteplus-seedream", True, "unknown"),
        "utils.byteplus.video.generate": ("byteplus-seedance", True, "unknown"),
        "utils.alibaba.video.generate": ("alibaba-wan", True, "unknown"),
        "utils.kling.video.generate": ("kling-video", True, "unknown"),
        "utils.web.scrape": ("firecrawl", True, "unknown"),
        "utils.web.read": ("jina", False, "ready"),
        "utils.sitemap.fetch": ("sitemap", False, "ready"),
        "utils.reddit.search-subreddit": ("reddit", True, "unknown"),
        "trackbooth.catalog.sync": ("trackbooth", True, "unknown"),
        "trackbooth.catalog.search": ("trackbooth", True, "unknown"),
        "trackbooth.operation.describe": ("trackbooth", True, "unknown"),
        "seo.keyword.research": ("dataforseo", True, "unknown"),
        "seo.serp.analyze": ("dataforseo", True, "unknown"),
        "seo.paa.extract": ("dataforseo", True, "unknown"),
        "seo.serper.search": ("serper", True, "unknown"),
        "seo.competitor.keywords": ("ahrefs", True, "unknown"),
        "seo.backlink.research": ("ahrefs", True, "unknown"),
        "publishing.wordpress.post.create": ("wordpress", True, "unknown"),
        "publishing.ghost.post.create": ("ghost", True, "unknown"),
    }
    repo = ActionRepository(session)

    for action_ref, (connector, requires_credential, status) in action_refs.items():
        described = repo.describe(action_ref=action_ref)

        assert described.connector_registered is True
        assert described.availability.status == status
        assert described.execution_available is (status == "ready")
        assert described.manifest.connector_key == connector
        assert described.manifest.requires_credential is requires_credential


def test_openai_image_action_rejects_legacy_quality_for_gpt_profiles(
    session: Session,
    project_id: int,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="openai-images",
        secret_payload=b"sk-openai",
    )
    credential_ref = _provider_credential_ref(session, project_id, "openai-images")

    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.image.generate",
        input_json={
            "prompt": "asset prompt",
            "model": "gpt-image-2",
            "quality": "hd",
        },
        credential_ref=credential_ref,
    )

    assert validation.valid is False
    assert any(
        issue.path == "$.quality" and issue.code == "enum_mismatch" for issue in validation.issues
    )


def test_openai_image_action_rejects_gpt_image_2_freeform_size_until_budget_modeled(
    session: Session,
    project_id: int,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="openai-images",
        secret_payload=b"sk-openai",
    )
    credential_ref = _provider_credential_ref(session, project_id, "openai-images")

    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.image.generate",
        input_json={
            "prompt": "asset prompt",
            "model": "gpt-image-2",
            "size": "1088x1920",
        },
        credential_ref=credential_ref,
    )

    assert validation.valid is False
    assert any(
        issue.path == "$.size" and issue.code == "enum_mismatch" for issue in validation.issues
    )


def test_openai_image_action_rejects_prompt_over_openai_limit(
    session: Session,
    project_id: int,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="openai-images",
        secret_payload=b"sk-openai",
    )
    credential_ref = _provider_credential_ref(session, project_id, "openai-images")

    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.image.generate",
        input_json={
            "prompt": "x" * 32_001,
            "model": "gpt-image-2",
        },
        credential_ref=credential_ref,
    )

    assert validation.valid is False
    assert any(issue.path == "$.prompt" and issue.code == "range" for issue in validation.issues)


def test_openai_image_edit_action_rejects_ref_cap_and_wrong_fidelity_model(
    session: Session,
    project_id: int,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="openai-images",
        secret_payload=b"sk-openai",
    )
    credential_ref = _provider_credential_ref(session, project_id, "openai-images")

    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.image.edit",
        input_json={
            "prompt": "asset prompt",
            "model": "gpt-image-2",
            "input_image_refs": [f"/generated-assets/ref-{index}.png" for index in range(17)],
            "input_fidelity": "high",
        },
        credential_ref=credential_ref,
    )

    assert validation.valid is False
    assert any(
        issue.path == "$.input_image_refs" and issue.code == "range" for issue in validation.issues
    )
    assert any(
        issue.path == "$.input_fidelity" and issue.code == "model_mismatch"
        for issue in validation.issues
    )

    mini_validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.image.edit",
        input_json={
            "prompt": "asset prompt",
            "model": "gpt-image-1-mini",
            "input_image_refs": ["/generated-assets/ref-0.png"],
            "input_fidelity": "high",
        },
        credential_ref=credential_ref,
    )

    assert mini_validation.valid is True


def test_xai_video_action_rejects_mode_mismatches_and_reference_duration_cap(
    session: Session,
    project_id: int,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="xai-imagine",
        secret_payload=b"xai-key",
    )
    credential_ref = _provider_credential_ref(session, project_id, "xai-imagine")

    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.xai.video.generate",
        input_json={
            "prompt": "runway walk",
            "mode": "reference-to-video",
            "duration": 12,
            "input_image_ref": "/generated-assets/uploads/source.png",
            "reference_image_refs": ["/generated-assets/uploads/ref.png"],
        },
        credential_ref=credential_ref,
    )

    assert validation.valid is False
    assert any(issue.path == "$.duration" and issue.code == "range" for issue in validation.issues)
    assert any(
        issue.path == "$.input_image_ref" and issue.code == "mode_mismatch"
        for issue in validation.issues
    )

    preview_validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.xai.video.generate",
        input_json={
            "prompt": "runway walk",
            "model": "grok-imagine-video-1.5-preview",
            "mode": "text-to-video",
        },
        credential_ref=credential_ref,
    )
    assert preview_validation.valid is False
    assert any(
        issue.path == "$.model" and issue.code == "enum_mismatch"
        for issue in preview_validation.issues
    )


def test_xai_actions_reject_single_image_edit_aspect_and_unsafe_poll_bounds(
    session: Session,
    project_id: int,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="xai-imagine",
        secret_payload=b"xai-key",
    )
    credential_ref = _provider_credential_ref(session, project_id, "xai-imagine")

    edit_validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.xai.image.edit",
        input_json={
            "prompt": "make it cinematic",
            "input_image_refs": ["/generated-assets/uploads/source.png"],
            "aspect_ratio": "1:1",
        },
        credential_ref=credential_ref,
    )
    video_validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.xai.video.generate",
        input_json={
            "prompt": "runway walk",
            "poll_interval_seconds": 0,
            "poll_timeout_seconds": 9999,
        },
        credential_ref=credential_ref,
    )

    assert edit_validation.valid is False
    assert any(
        issue.path == "$.aspect_ratio" and issue.code == "mode_mismatch"
        for issue in edit_validation.issues
    )
    assert video_validation.valid is False
    assert any(
        issue.path == "$.poll_interval_seconds" and issue.code == "range"
        for issue in video_validation.issues
    )
    assert any(
        issue.path == "$.poll_timeout_seconds" and issue.code == "range"
        for issue in video_validation.issues
    )


def test_xai_image_generate_action_executes_and_registers_artifact(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="xai-imagine",
        secret_payload=b"xai-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="xai-imagine",
        monthly_budget_usd=10.0,
    )
    credential_ref = _provider_credential_ref(session, project_id, "xai-imagine")
    httpx_mock.add_response(
        method="POST",
        url="https://api.x.ai/v1/images/generations",
        json={
            "data": [{"b64_json": base64.b64encode(b"image-bytes").decode("ascii")}],
            "usage": {"cost_in_usd_ticks": 300000000},
        },
    )

    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="utils.xai.image.generate",
            input_json={"prompt": "poster", "aspect_ratio": "1:1", "resolution": "1k"},
            credential_ref=credential_ref,
        )
    ).data

    item = result.output_json["data"][0]
    rendered = json.dumps(result.model_dump(mode="json"))
    assert result.cost_cents == 3
    assert item["url"].startswith("/generated-assets/xai-imagine/xai-image-")
    assert item["artifact_ref"] == item["url"]
    assert isinstance(item["artifact_id"], int)
    assert result.output_json["artifact_refs"] == [item["url"]]
    assert "b64_json" not in item
    assert "xai-key" not in rendered
    path = tmp_path / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"image-bytes"


def test_xai_image_edit_action_executes_without_single_image_aspect_and_registers_artifact(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    source_dir = tmp_path / "uploads"
    source_dir.mkdir()
    source = source_dir / "source.png"
    source.write_bytes(b"source-png")
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="xai-imagine",
        secret_payload=b"xai-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="xai-imagine",
        monthly_budget_usd=10.0,
    )
    credential_ref = _provider_credential_ref(session, project_id, "xai-imagine")
    httpx_mock.add_response(
        method="POST",
        url="https://api.x.ai/v1/images/edits",
        json={
            "data": [{"b64_json": base64.b64encode(b"edited-image").decode("ascii")}],
            "usage": {"cost_in_usd_ticks": 600000000},
        },
    )

    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="utils.xai.image.edit",
            input_json={
                "prompt": "make it cinematic",
                "input_image_refs": ["/generated-assets/uploads/source.png"],
            },
            credential_ref=credential_ref,
        )
    ).data

    body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    item = result.output_json["data"][0]
    rendered = json.dumps(result.model_dump(mode="json"))
    assert "aspect_ratio" not in body
    assert body["image"]["url"].startswith("data:image/png;base64,")
    assert result.cost_cents == 6
    assert item["url"].startswith("/generated-assets/xai-imagine/xai-image-")
    assert item["artifact_ref"] == item["url"]
    assert isinstance(item["artifact_id"], int)
    assert result.output_json["artifact_refs"] == [item["url"]]
    assert "b64_json" not in item
    assert "xai-key" not in rendered
    path = tmp_path / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"edited-image"


def test_xai_video_generate_action_executes_and_registers_artifact(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="xai-imagine",
        secret_payload=b"xai-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="xai-imagine",
        monthly_budget_usd=10.0,
    )
    credential_ref = _provider_credential_ref(session, project_id, "xai-imagine")
    httpx_mock.add_response(
        method="POST",
        url="https://api.x.ai/v1/videos/generations",
        json={"request_id": "req_123"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.x.ai/v1/videos/req_123",
        json={
            "status": "done",
            "model": "grok-imagine-video",
            "video": {"url": "https://cdn.x.ai/video.mp4", "duration": 5},
            "usage": {"cost_in_usd_ticks": 3100000000},
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://cdn.x.ai/video.mp4",
        content=b"video-bytes",
        headers={"content-type": "video/mp4"},
    )

    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="utils.xai.video.generate",
            input_json={
                "prompt": "video",
                "duration": 5,
                "poll_interval_seconds": 1,
                "poll_timeout_seconds": 60,
            },
            credential_ref=credential_ref,
        )
    ).data

    item = result.output_json["data"][0]
    rendered = json.dumps(result.model_dump(mode="json"))
    assert result.cost_cents == 31
    assert result.output_json["request_id"] == "req_123"
    assert item["url"].startswith("/generated-assets/xai-imagine/xai-video-")
    assert item["artifact_ref"] == item["url"]
    assert isinstance(item["artifact_id"], int)
    assert result.output_json["artifact_refs"] == [item["url"]]
    assert "https://cdn.x.ai/video.mp4" not in rendered
    assert "xai-key" not in rendered
    path = tmp_path / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"video-bytes"


def test_reve_actions_reject_invalid_versions_and_ref_counts(
    session: Session,
    project_id: int,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="reve",
        secret_payload=b"reve-key",
    )
    credential_ref = _provider_credential_ref(session, project_id, "reve")

    edit_validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.reve.image.edit",
        input_json={
            "edit_instruction": "make it cinematic",
            "input_image_ref": "/generated-assets/uploads/source.png",
            "version": "reve-create@20250915",
            "test_time_scaling": 16,
        },
        credential_ref=credential_ref,
    )
    remix_validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.reve.image.remix",
        input_json={
            "prompt": "mix",
            "input_image_refs": [
                f"/generated-assets/uploads/ref-{index}.png" for index in range(7)
            ],
        },
        credential_ref=credential_ref,
    )

    assert edit_validation.valid is False
    assert any(issue.path == "$.version" for issue in edit_validation.issues)
    assert any(issue.path == "$.test_time_scaling" for issue in edit_validation.issues)
    assert remix_validation.valid is False
    assert any(
        issue.path == "$.input_image_refs" and issue.code == "range"
        for issue in remix_validation.issues
    )


def test_reve_image_generate_action_executes_and_registers_artifact(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="reve",
        secret_payload=b"reve-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="reve",
        monthly_budget_usd=10.0,
    )
    credential_ref = _provider_credential_ref(session, project_id, "reve")
    httpx_mock.add_response(
        method="POST",
        url="https://api.reve.com/v1/image/create",
        json={
            "image": base64.b64encode(b"reve-image").decode("ascii"),
            "version": "reve-create@20250915",
            "content_violation": False,
            "request_id": "rsid-create",
            "credits_used": 18,
            "credits_remaining": 982,
        },
    )

    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="utils.reve.image.generate",
            input_json={"prompt": "poster", "aspect_ratio": "16:9"},
            credential_ref=credential_ref,
        )
    ).data

    body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    item = result.output_json["data"][0]
    rendered = json.dumps(result.model_dump(mode="json"))
    assert body["prompt"] == "poster"
    assert body["aspect_ratio"] == "16:9"
    assert result.cost_cents == 2
    assert item["url"].startswith("/generated-assets/reve/reve-image-")
    assert item["artifact_ref"] == item["url"]
    assert isinstance(item["artifact_id"], int)
    assert result.output_json["artifact_refs"] == [item["url"]]
    assert result.output_json["usage"]["credits_used"] == 18
    assert "image" not in result.output_json
    assert "reve-key" not in rendered
    path = tmp_path / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"reve-image"


def test_reve_image_edit_action_executes_and_registers_artifact(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    source_dir = tmp_path / "uploads"
    source_dir.mkdir()
    source = source_dir / "source.png"
    source.write_bytes(b"source-png")
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="reve",
        secret_payload=b"reve-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="reve",
        monthly_budget_usd=10.0,
    )
    credential_ref = _provider_credential_ref(session, project_id, "reve")
    httpx_mock.add_response(
        method="POST",
        url="https://api.reve.com/v1/image/edit",
        json={
            "image": base64.b64encode(b"reve-edit").decode("ascii"),
            "version": "reve-edit@20250915",
            "content_violation": False,
            "request_id": "rsid-edit",
            "credits_used": 30,
            "credits_remaining": 970,
        },
    )

    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="utils.reve.image.edit",
            input_json={
                "edit_instruction": "make it cinematic",
                "input_image_ref": "/generated-assets/uploads/source.png",
            },
            credential_ref=credential_ref,
        )
    ).data

    body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    item = result.output_json["data"][0]
    rendered = json.dumps(result.model_dump(mode="json"))
    assert body["reference_image"] == base64.b64encode(b"source-png").decode("ascii")
    assert body["edit_instruction"] == "make it cinematic"
    assert result.cost_cents == 4
    assert item["url"].startswith("/generated-assets/reve/reve-image-")
    assert item["artifact_ref"] == item["url"]
    assert isinstance(item["artifact_id"], int)
    assert "image" not in result.output_json
    assert "reve-key" not in rendered
    path = tmp_path / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"reve-edit"


def test_reve_image_remix_action_executes_and_registers_artifact(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    source_dir = tmp_path / "uploads"
    source_dir.mkdir()
    first_bytes = _png_header(1, 1)
    second_bytes = _png_header(2, 1)
    first = source_dir / "first.png"
    second = source_dir / "second.png"
    first.write_bytes(first_bytes)
    second.write_bytes(second_bytes)
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="reve",
        secret_payload=b"reve-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="reve",
        monthly_budget_usd=10.0,
    )
    credential_ref = _provider_credential_ref(session, project_id, "reve")
    httpx_mock.add_response(
        method="POST",
        url="https://api.reve.com/v1/image/remix",
        json={
            "image": base64.b64encode(b"reve-remix").decode("ascii"),
            "version": "reve-remix-fast@20251030",
            "content_violation": False,
            "request_id": "rsid-remix",
            "credits_used": 5,
            "credits_remaining": 995,
        },
    )

    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="utils.reve.image.remix",
            input_json={
                "prompt": "combine",
                "input_image_refs": [
                    "/generated-assets/uploads/first.png",
                    "/generated-assets/uploads/second.png",
                ],
                "version": "reve-remix-fast@20251030",
            },
            credential_ref=credential_ref,
        )
    ).data

    body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    item = result.output_json["data"][0]
    rendered = json.dumps(result.model_dump(mode="json"))
    assert body["reference_images"] == [
        base64.b64encode(first_bytes).decode("ascii"),
        base64.b64encode(second_bytes).decode("ascii"),
    ]
    assert result.cost_cents == 1
    assert item["url"].startswith("/generated-assets/reve/reve-image-")
    assert item["artifact_ref"] == item["url"]
    assert isinstance(item["artifact_id"], int)
    assert "image" not in result.output_json
    assert "reve-key" not in rendered
    path = tmp_path / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"reve-remix"


def test_reve_remix_pixel_preflight_fails_before_budget_or_action_call(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    source_dir = tmp_path / "uploads"
    source_dir.mkdir()
    oversized = source_dir / "oversized.png"
    oversized.write_bytes(_png_header(8000, 4001))
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="reve",
        secret_payload=b"reve-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="reve",
        monthly_budget_usd=10.0,
    )
    credential_ref = _provider_credential_ref(session, project_id, "reve")
    repo = ActionRepository(session, asset_dir=tmp_path)

    validation = repo.validate(
        project_id=project_id,
        action_ref="utils.reve.image.remix",
        input_json={
            "prompt": "combine",
            "input_image_refs": ["/generated-assets/uploads/oversized.png"],
        },
        credential_ref=credential_ref,
    )
    with pytest.raises(ValidationError) as exc_info:
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="utils.reve.image.remix",
                input_json={
                    "prompt": "combine",
                    "input_image_refs": ["/generated-assets/uploads/oversized.png"],
                },
                credential_ref=credential_ref,
            )
        )

    assert validation.valid is False
    assert any(
        issue.path == "$.input_image_refs"
        and issue.code == "invalid_image_ref"
        and "32 million pixels" in issue.message
        for issue in validation.issues
    )
    assert "32 million pixels" in json.dumps(exc_info.value.data)
    assert httpx_mock.get_requests() == []
    assert session.exec(select(ActionCall)).all() == []
    budget = IntegrationBudgetRepository(session).get(project_id, "reve")
    assert budget.current_month_spend == 0
    assert budget.current_month_calls == 0


def test_google_gemini_image_actions_reject_invalid_model_specific_controls(
    session: Session,
    project_id: int,
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "uploads"
    source_dir.mkdir()
    for index in range(4):
        (source_dir / f"ref-{index}.png").write_bytes(b"ref")
    (source_dir / "unsupported.heic").write_bytes(b"heic")
    (source_dir / "unsupported.heif").write_bytes(b"heif")
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="google-gemini-image",
        secret_payload=b"gemini-key",
    )
    credential_ref = _provider_credential_ref(session, project_id, "google-gemini-image")

    validation = ActionRepository(session, asset_dir=tmp_path).validate(
        project_id=project_id,
        action_ref="utils.google.image.edit",
        input_json={
            "prompt": "edit with legacy model",
            "model": "gemini-2.5-flash-image",
            "image_size": "2K",
            "aspect_ratio": "1:8",
            "input_image_refs": [
                f"/generated-assets/uploads/ref-{index}.png" for index in range(4)
            ],
        },
        credential_ref=credential_ref,
    )

    assert validation.valid is False
    assert any(issue.path == "$.image_size" for issue in validation.issues)
    assert any(issue.path == "$.aspect_ratio" for issue in validation.issues)
    assert any(
        issue.path == "$.input_image_refs" and issue.code == "range" for issue in validation.issues
    )

    pro_validation = ActionRepository(session, asset_dir=tmp_path).validate(
        project_id=project_id,
        action_ref="utils.google.image.generate",
        input_json={
            "prompt": "pro model at unsupported size",
            "model": "gemini-3-pro-image",
            "image_size": "512",
        },
        credential_ref=credential_ref,
    )

    assert pro_validation.valid is False
    assert any(issue.path == "$.image_size" for issue in pro_validation.issues)

    unsupported_format_validations = [
        ActionRepository(session, asset_dir=tmp_path).validate(
            project_id=project_id,
            action_ref="utils.google.image.edit",
            input_json={
                "prompt": f"edit unsupported local {suffix} format",
                "input_image_refs": [f"/generated-assets/uploads/unsupported.{suffix}"],
            },
            credential_ref=credential_ref,
        )
        for suffix in ("heic", "heif")
    ]
    traversal_validation = ActionRepository(session, asset_dir=tmp_path).validate(
        project_id=project_id,
        action_ref="utils.google.image.edit",
        input_json={
            "prompt": "escape generated assets",
            "input_image_refs": ["/generated-assets/../outside.png"],
        },
        credential_ref=credential_ref,
    )

    for format_validation in unsupported_format_validations:
        assert format_validation.valid is False
        assert any(
            issue.path == "$.input_image_refs" and issue.code == "invalid_image_ref"
            for issue in format_validation.issues
        )
    assert traversal_validation.valid is False
    assert any(
        issue.path == "$.input_image_refs" and issue.code == "invalid_image_ref"
        for issue in traversal_validation.issues
    )


def test_google_gemini_image_generate_action_executes_and_registers_artifact(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="google-gemini-image",
        secret_payload=b"gemini-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="google-gemini-image",
        monthly_budget_usd=10.0,
    )
    credential_ref = _provider_credential_ref(session, project_id, "google-gemini-image")
    httpx_mock.add_response(
        method="POST",
        url=(
            "https://generativelanguage.googleapis.com/v1/models/"
            "gemini-3.1-flash-image:generateContent"
        ),
        json={
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": base64.b64encode(b"gemini-image").decode("ascii"),
                                }
                            }
                        ]
                    }
                }
            ],
            "usageMetadata": {
                "totalTokenCount": 1680,
                "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 84}],
            },
        },
    )

    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="utils.google.image.generate",
            input_json={
                "prompt": "poster",
                "aspect_ratio": "16:9",
                "image_size": "2K",
            },
            credential_ref=credential_ref,
        )
    ).data

    body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    item = result.output_json["data"][0]
    rendered = json.dumps(result.model_dump(mode="json"))
    assert body["generationConfig"]["responseFormat"]["image"] == {
        "aspectRatio": "16:9",
        "imageSize": "2K",
    }
    assert result.cost_cents == 10
    assert item["url"].startswith("/generated-assets/google-gemini-image/google-gemini-image-")
    assert item["artifact_ref"] == item["url"]
    assert isinstance(item["artifact_id"], int)
    assert result.output_json["artifact_refs"] == [item["url"]]
    assert result.output_json["usage"] == {
        "total_count": 1680,
        "prompt_units_details": [{"modality": "TEXT", "count": 84}],
    }
    assert "usageMetadata" not in result.output_json
    assert "gemini-key" not in rendered
    assert "tokenCount" not in rendered
    path = tmp_path / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"gemini-image"


def test_google_gemini_image_generate_action_omits_image_size_when_unset(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="google-gemini-image",
        secret_payload=b"gemini-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="google-gemini-image",
        monthly_budget_usd=10.0,
    )
    credential_ref = _provider_credential_ref(session, project_id, "google-gemini-image")
    httpx_mock.add_response(
        method="POST",
        url=(
            "https://generativelanguage.googleapis.com/v1/models/"
            "gemini-3.1-flash-image:generateContent"
        ),
        json={
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": base64.b64encode(b"default-size").decode("ascii"),
                                }
                            }
                        ]
                    }
                }
            ],
        },
    )

    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="utils.google.image.generate",
            input_json={"prompt": "poster", "aspect_ratio": "16:9"},
            credential_ref=credential_ref,
        )
    ).data

    body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    assert body["generationConfig"]["responseFormat"]["image"] == {"aspectRatio": "16:9"}
    assert result.cost_cents == 7


def test_google_gemini_image_edit_action_executes_and_registers_artifact(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    source_dir = tmp_path / "uploads"
    source_dir.mkdir()
    source = source_dir / "source.webp"
    source.write_bytes(b"source-webp")
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="google-gemini-image",
        secret_payload=b"gemini-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="google-gemini-image",
        monthly_budget_usd=10.0,
    )
    credential_ref = _provider_credential_ref(session, project_id, "google-gemini-image")
    httpx_mock.add_response(
        method="POST",
        url=(
            "https://generativelanguage.googleapis.com/v1/models/gemini-3-pro-image:generateContent"
        ),
        json={
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "image/jpeg",
                                    "data": base64.b64encode(b"gemini-edit").decode("ascii"),
                                }
                            }
                        ]
                    }
                }
            ]
        },
    )

    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="utils.google.image.edit",
            input_json={
                "prompt": "keep product, change set",
                "model": "gemini-3-pro-image",
                "input_image_refs": ["/generated-assets/uploads/source.webp"],
                "aspect_ratio": "3:2",
                "image_size": "4K",
            },
            credential_ref=credential_ref,
        )
    ).data

    body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    parts = body["contents"][0]["parts"]
    item = result.output_json["data"][0]
    rendered = json.dumps(result.model_dump(mode="json"))
    assert parts[1]["inline_data"] == {
        "mime_type": "image/webp",
        "data": base64.b64encode(b"source-webp").decode("ascii"),
    }
    assert body["generationConfig"]["responseFormat"]["image"] == {
        "aspectRatio": "3:2",
        "imageSize": "4K",
    }
    assert result.cost_cents == 24
    assert item["file_format"] == "jpg"
    assert item["artifact_ref"] == item["url"]
    assert isinstance(item["artifact_id"], int)
    assert "gemini-key" not in rendered
    path = tmp_path / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"gemini-edit"


def test_google_gemini_inline_preflight_fails_before_budget_or_action_call(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    source_dir = tmp_path / "uploads"
    source_dir.mkdir()
    source = source_dir / "too-large.png"
    source.write_bytes(b"x" * 15_000_000)
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="google-gemini-image",
        secret_payload=b"gemini-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="google-gemini-image",
        monthly_budget_usd=10.0,
    )
    credential_ref = _provider_credential_ref(session, project_id, "google-gemini-image")
    repo = ActionRepository(session, asset_dir=tmp_path)

    validation = repo.validate(
        project_id=project_id,
        action_ref="utils.google.image.edit",
        input_json={
            "prompt": "oversized",
            "input_image_refs": ["/generated-assets/uploads/too-large.png"],
        },
        credential_ref=credential_ref,
    )
    with pytest.raises(ValidationError) as exc_info:
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="utils.google.image.edit",
                input_json={
                    "prompt": "oversized",
                    "input_image_refs": ["/generated-assets/uploads/too-large.png"],
                },
                credential_ref=credential_ref,
            )
        )

    assert validation.valid is False
    assert any(
        issue.path == "$.input_image_refs"
        and issue.code == "invalid_image_ref"
        and "under 20 MB" in issue.message
        for issue in validation.issues
    )
    assert "under 20 MB" in json.dumps(exc_info.value.data)
    assert httpx_mock.get_requests() == []
    assert session.exec(select(ActionCall)).all() == []
    budget = IntegrationBudgetRepository(session).get(project_id, "google-gemini-image")
    assert budget.current_month_spend == 0
    assert budget.current_month_calls == 0


def test_ideogram_image_actions_reject_invalid_contract_controls(
    session: Session,
    project_id: int,
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "uploads"
    source_dir.mkdir()
    (source_dir / "source.heic").write_bytes(b"heic")
    (source_dir / "source.png").write_bytes(b"png")
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="ideogram",
        secret_payload=b"ideo-key",
    )
    credential_ref = _provider_credential_ref(session, project_id, "ideogram")

    generate_validation = ActionRepository(session, asset_dir=tmp_path).validate(
        project_id=project_id,
        action_ref="utils.ideogram.image.generate",
        input_json={
            "text_prompt": "poster",
            "resolution": "1024x1024",
            "rendering_speed": "FLASH",
            "json_prompt": {"scene": "unsupported"},
        },
        credential_ref=credential_ref,
    )
    remix_validation = ActionRepository(session, asset_dir=tmp_path).validate(
        project_id=project_id,
        action_ref="utils.ideogram.image.remix",
        input_json={
            "text_prompt": "remix",
            "input_image_ref": "/generated-assets/uploads/source.heic",
            "image_weight": 101,
        },
        credential_ref=credential_ref,
    )
    traversal_validation = ActionRepository(session, asset_dir=tmp_path).validate(
        project_id=project_id,
        action_ref="utils.ideogram.image.remix",
        input_json={
            "text_prompt": "remix",
            "input_image_ref": "/generated-assets/../outside.png",
        },
        credential_ref=credential_ref,
    )

    assert generate_validation.valid is False
    assert any(issue.path == "$.resolution" for issue in generate_validation.issues)
    assert any(issue.path == "$.rendering_speed" for issue in generate_validation.issues)
    assert any(issue.path == "$.json_prompt" for issue in generate_validation.issues)
    assert remix_validation.valid is False
    assert any(issue.path == "$.image_weight" for issue in remix_validation.issues)
    assert any(issue.path == "$.input_image_ref" for issue in remix_validation.issues)
    assert traversal_validation.valid is False
    assert any(issue.path == "$.input_image_ref" for issue in traversal_validation.issues)


def test_ideogram_image_generate_action_executes_and_registers_artifact(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    provider_url = "https://ideogram.ai/api/images/ephemeral/generated.png?sig=secret"
    provider_url_2 = "https://ideogram.ai/api/images/ephemeral/generated-2.png?sig=secret"
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="ideogram",
        secret_payload=b"ideo-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="ideogram",
        monthly_budget_usd=10.0,
    )
    credential_ref = _provider_credential_ref(session, project_id, "ideogram")
    httpx_mock.add_response(
        method="POST",
        url="https://api.ideogram.ai/v1/ideogram-v4/generate",
        json={
            "created": "2026-06-10 00:00:00+00:00",
            "data": [
                {
                    "prompt": "poster",
                    "resolution": "2048x2048",
                    "is_image_safe": True,
                    "seed": 12345,
                    "url": provider_url,
                },
                {
                    "prompt": "poster",
                    "resolution": "2048x2048",
                    "is_image_safe": True,
                    "seed": 67890,
                    "url": provider_url_2,
                },
            ],
            "response_type": "url",
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=provider_url,
        content=b"ideogram-image",
        headers={"content-type": "image/png"},
    )
    httpx_mock.add_response(
        method="GET",
        url=provider_url_2,
        content=b"ideogram-image-2",
        headers={"content-type": "image/png"},
    )

    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="utils.ideogram.image.generate",
            input_json={
                "text_prompt": "poster",
                "resolution": "2048x2048",
                "rendering_speed": "TURBO",
                "enable_copyright_detection": True,
            },
            credential_ref=credential_ref,
        )
    ).data

    post_request = httpx_mock.get_requests()[0]
    item = result.output_json["data"][0]
    item_2 = result.output_json["data"][1]
    rendered = json.dumps(result.model_dump(mode="json"))
    assert post_request.headers["Api-Key"] == "ideo-key"
    assert b'name="rendering_speed"\r\n\r\nTURBO' in post_request.content
    assert result.cost_cents == 6
    assert item["url"].startswith("/generated-assets/ideogram/ideogram-")
    assert item["artifact_ref"] == item["url"]
    assert isinstance(item["artifact_id"], int)
    assert item_2["artifact_ref"] == item_2["url"]
    assert isinstance(item_2["artifact_id"], int)
    assert result.output_json["artifact_refs"] == [item["url"], item_2["url"]]
    assert provider_url not in rendered
    assert provider_url_2 not in rendered
    assert "ideo-key" not in rendered
    path = tmp_path / item["url"].removeprefix("/generated-assets/")
    path_2 = tmp_path / item_2["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"ideogram-image"
    assert path_2.read_bytes() == b"ideogram-image-2"


def test_ideogram_image_remix_action_executes_and_registers_artifact(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    source_dir = tmp_path / "uploads"
    source_dir.mkdir()
    (source_dir / "source.webp").write_bytes(_webp_header())
    provider_url = "https://ideogram.ai/api/images/ephemeral/remix.webp?sig=secret"
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="ideogram",
        secret_payload=b"ideo-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="ideogram",
        monthly_budget_usd=10.0,
    )
    credential_ref = _provider_credential_ref(session, project_id, "ideogram")
    httpx_mock.add_response(
        method="POST",
        url="https://api.ideogram.ai/v1/ideogram-v4/remix",
        json={
            "created": "2026-06-10 00:00:00+00:00",
            "data": [
                {
                    "prompt": "remix",
                    "resolution": "3072x1024",
                    "is_image_safe": True,
                    "seed": 12345,
                    "url": provider_url,
                }
            ],
            "response_type": "url",
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=provider_url,
        content=b"ideogram-remix",
        headers={"content-type": "image/webp"},
    )

    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="utils.ideogram.image.remix",
            input_json={
                "text_prompt": "remix",
                "input_image_ref": "/generated-assets/uploads/source.webp",
                "image_weight": 75,
                "resolution": "3072x1024",
                "rendering_speed": "QUALITY",
            },
            credential_ref=credential_ref,
        )
    ).data

    post_request = httpx_mock.get_requests()[0]
    item = result.output_json["data"][0]
    rendered = json.dumps(result.model_dump(mode="json"))
    assert (
        b'name="image"; filename="source.webp"\r\nContent-Type: image/webp' in post_request.content
    )
    assert b'name="image_weight"\r\n\r\n75' in post_request.content
    assert result.cost_cents == 10
    assert item["file_format"] == "webp"
    assert item["artifact_ref"] == item["url"]
    assert provider_url not in rendered
    assert "ideo-key" not in rendered
    path = tmp_path / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"ideogram-remix"


def test_ideogram_remix_preflight_fails_before_budget_or_action_call(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    source_dir = tmp_path / "uploads"
    source_dir.mkdir()
    source = source_dir / "too-large.png"
    source.write_bytes(b"x" * 10_000_001)
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="ideogram",
        secret_payload=b"ideo-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="ideogram",
        monthly_budget_usd=10.0,
    )
    credential_ref = _provider_credential_ref(session, project_id, "ideogram")
    repo = ActionRepository(session, asset_dir=tmp_path)

    validation = repo.validate(
        project_id=project_id,
        action_ref="utils.ideogram.image.remix",
        input_json={
            "text_prompt": "oversized remix",
            "input_image_ref": "/generated-assets/uploads/too-large.png",
        },
        credential_ref=credential_ref,
    )
    with pytest.raises(ValidationError) as exc_info:
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="utils.ideogram.image.remix",
                input_json={
                    "text_prompt": "oversized remix",
                    "input_image_ref": "/generated-assets/uploads/too-large.png",
                },
                credential_ref=credential_ref,
            )
        )

    assert validation.valid is False
    assert any(
        issue.path == "$.input_image_ref"
        and issue.code == "invalid_image_ref"
        and "at most 10 MB" in issue.message
        for issue in validation.issues
    )
    assert "at most 10 MB" in json.dumps(exc_info.value.data)
    assert httpx_mock.get_requests() == []
    assert session.exec(select(ActionCall)).all() == []
    budget = IntegrationBudgetRepository(session).get(project_id, "ideogram")
    assert budget.current_month_spend == 0
    assert budget.current_month_calls == 0


def test_byteplus_seedream_image_actions_reject_invalid_contract_controls(
    session: Session,
    project_id: int,
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "uploads"
    source_dir.mkdir()
    (source_dir / "source.png").write_bytes(_png_header(32, 32))
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="byteplus-ark",
        secret_payload=b"ark-key",
    )
    credential_ref = _provider_credential_ref(session, project_id, "byteplus-ark")
    repo = ActionRepository(session, asset_dir=tmp_path)

    invalid_generate = repo.validate(
        project_id=project_id,
        action_ref="utils.byteplus.image.generate",
        input_json={
            "prompt": "poster",
            "model": "seedream-5-0-260128",
            "region": "eu-west-1",
            "size": "1500x1500",
            "sequential_image_generation": "disabled",
            "max_images": 2,
            "output_format": "webp",
        },
        credential_ref=credential_ref,
    )
    invalid_edit = repo.validate(
        project_id=project_id,
        action_ref="utils.byteplus.image.edit",
        input_json={
            "prompt": "edit",
            "input_image_refs": ["/generated-assets/uploads/source.png"],
            "model": "seedream-4-0-250828",
            "region": "eu-west-1",
            "output_format": "png",
        },
        credential_ref=credential_ref,
    )
    invalid_combined_edit = repo.validate(
        project_id=project_id,
        action_ref="utils.byteplus.image.edit",
        input_json={
            "prompt": "edit",
            "input_image_refs": ["/generated-assets/uploads/source.png"] * 14,
            "sequential_image_generation": "auto",
            "max_images": 2,
        },
        credential_ref=credential_ref,
    )

    assert invalid_generate.valid is False
    assert any(issue.path == "$.model" for issue in invalid_generate.issues)
    assert any(issue.path == "$.size" for issue in invalid_generate.issues)
    assert any(issue.path == "$.max_images" for issue in invalid_generate.issues)
    assert any(issue.path == "$.output_format" for issue in invalid_generate.issues)
    assert invalid_edit.valid is False
    assert any(issue.path == "$.region" for issue in invalid_edit.issues)
    assert any(issue.path == "$.output_format" for issue in invalid_edit.issues)
    assert invalid_combined_edit.valid is False
    assert any(
        issue.path == "$.max_images" and "plus requested generated images" in issue.message
        for issue in invalid_combined_edit.issues
    )
    assert (
        repo.validate(
            project_id=project_id,
            action_ref="utils.byteplus.image.generate",
            input_json={
                "prompt": "poster",
                "model": "seedream-4-5-251128",
                "size": "3K",
            },
            credential_ref=credential_ref,
        ).valid
        is False
    )
    assert (
        repo.validate(
            project_id=project_id,
            action_ref="utils.byteplus.image.generate",
            input_json={
                "prompt": "poster",
                "model": "seedream-4-0-250828",
                "size": "1K",
            },
            credential_ref=credential_ref,
        ).valid
        is True
    )


def test_byteplus_seedream_generate_action_executes_and_registers_artifacts(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    provider_url = "https://ark-output.byteplus.test/generated-1.jpeg?token=secret"
    provider_url_2 = "https://ark-output.byteplus.test/generated-2.jpeg?token=secret"
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="byteplus-ark",
        secret_payload=b"ark-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="byteplus-ark",
        monthly_budget_usd=10.0,
    )
    credential_ref = _provider_credential_ref(session, project_id, "byteplus-ark")
    httpx_mock.add_response(
        method="POST",
        url="https://ark.ap-southeast.bytepluses.com/api/v3/images/generations",
        json={
            "model": "seedream-4-5-251128",
            "data": [
                {"url": provider_url, "size": "2048x2048"},
                {"url": provider_url_2, "size": "2048x2048"},
            ],
            "usage": {"generated_images": 2},
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=provider_url,
        content=b"byteplus-image-one",
        headers={"content-type": "image/jpeg"},
    )
    httpx_mock.add_response(
        method="GET",
        url=provider_url_2,
        content=b"byteplus-image-two",
        headers={"content-type": "image/jpeg"},
    )

    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="utils.byteplus.image.generate",
            input_json={
                "prompt": "poster",
                "model": "seedream-4-5-251128",
                "size": "2K",
                "sequential_image_generation": "auto",
                "max_images": 2,
                "watermark": False,
            },
            credential_ref=credential_ref,
        )
    ).data

    post_request = httpx_mock.get_requests()[0]
    body = json.loads(post_request.content.decode("utf-8"))
    item = result.output_json["data"][0]
    item_2 = result.output_json["data"][1]
    rendered = json.dumps(result.model_dump(mode="json"))

    assert post_request.headers["Authorization"] == "Bearer ark-key"
    assert body["model"] == "seedream-4-5-251128"
    assert body["response_format"] == "url"
    assert body["sequential_image_generation"] == "auto"
    assert body["sequential_image_generation_options"] == {"max_images": 2}
    assert result.cost_cents == 8
    assert item["url"].startswith("/generated-assets/byteplus-ark/byteplus-ark-")
    assert item["artifact_ref"] == item["url"]
    assert isinstance(item["artifact_id"], int)
    assert item_2["artifact_ref"] == item_2["url"]
    assert isinstance(item_2["artifact_id"], int)
    assert result.output_json["artifact_refs"] == [item["url"], item_2["url"]]
    assert provider_url not in rendered
    assert provider_url_2 not in rendered
    assert "ark-key" not in rendered
    path = tmp_path / item["url"].removeprefix("/generated-assets/")
    path_2 = tmp_path / item_2["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"byteplus-image-one"
    assert path_2.read_bytes() == b"byteplus-image-two"


def test_byteplus_seedream_multi_output_estimate_preempts_budget(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="byteplus-ark",
        secret_payload=b"ark-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="byteplus-ark",
        monthly_budget_usd=0.04,
    )
    credential_ref = _provider_credential_ref(session, project_id, "byteplus-ark")
    repo = ActionRepository(session, asset_dir=tmp_path)
    validation = repo.validate(
        project_id=project_id,
        action_ref="utils.byteplus.image.generate",
        input_json={
            "prompt": "poster",
            "model": "seedream-4-5-251128",
            "sequential_image_generation": "auto",
            "max_images": 2,
        },
        credential_ref=credential_ref,
    )

    with pytest.raises(BudgetExceededError) as exc_info:
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="utils.byteplus.image.generate",
                input_json={
                    "prompt": "poster",
                    "model": "seedream-4-5-251128",
                    "sequential_image_generation": "auto",
                    "max_images": 2,
                },
                credential_ref=credential_ref,
            )
        )

    budget = IntegrationBudgetRepository(session).get(project_id, "byteplus-ark")
    assert validation.estimated_cost_cents == 8
    assert "budget exceeded" in str(exc_info.value)
    assert httpx_mock.get_requests() == []
    assert budget.current_month_spend == 0
    assert budget.current_month_calls == 0


def test_byteplus_seedream_edit_action_executes_and_registers_artifact(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    source_dir = tmp_path / "uploads"
    source_dir.mkdir()
    (source_dir / "source.webp").write_bytes(_webp_header())
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="byteplus-ark",
        secret_payload=b"ark-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="byteplus-ark",
        monthly_budget_usd=10.0,
    )
    credential_ref = _provider_credential_ref(session, project_id, "byteplus-ark")
    httpx_mock.add_response(
        method="POST",
        url="https://ark.ap-southeast.bytepluses.com/api/v3/images/generations",
        json={
            "model": "seedream-4-0-250828",
            "data": [
                {
                    "b64_json": base64.b64encode(b"byteplus-edit").decode("ascii"),
                    "size": "2048x2048",
                }
            ],
            "usage": {"generated_images": 1},
        },
    )

    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="utils.byteplus.image.edit",
            input_json={
                "prompt": "keep the product, change the background",
                "input_image_refs": ["/generated-assets/uploads/source.webp"],
                "model": "seedream-4-0-250828",
                "size": "2048x2048",
            },
            credential_ref=credential_ref,
        )
    ).data

    post_request = httpx_mock.get_requests()[0]
    body = json.loads(post_request.content.decode("utf-8"))
    item = result.output_json["data"][0]
    rendered = json.dumps(result.model_dump(mode="json"))

    assert body["image"].startswith("data:image/webp;base64,")
    assert result.cost_cents == 3
    assert item["provider_b64_persisted"] is True
    assert item["artifact_ref"] == item["url"]
    assert "b64_json" not in rendered
    assert "ark-key" not in rendered
    path = tmp_path / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"byteplus-edit"


def test_byteplus_seedream_preflight_fails_before_budget_or_action_call(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    source_dir = tmp_path / "uploads"
    source_dir.mkdir()
    source = source_dir / "fake.png"
    source.write_bytes(b"not-png")
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="byteplus-ark",
        secret_payload=b"ark-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="byteplus-ark",
        monthly_budget_usd=10.0,
    )
    credential_ref = _provider_credential_ref(session, project_id, "byteplus-ark")
    repo = ActionRepository(session, asset_dir=tmp_path)

    validation = repo.validate(
        project_id=project_id,
        action_ref="utils.byteplus.image.edit",
        input_json={
            "prompt": "bad edit",
            "input_image_refs": ["/generated-assets/uploads/fake.png"],
        },
        credential_ref=credential_ref,
    )
    with pytest.raises(ValidationError) as exc_info:
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="utils.byteplus.image.edit",
                input_json={
                    "prompt": "bad edit",
                    "input_image_refs": ["/generated-assets/uploads/fake.png"],
                },
                credential_ref=credential_ref,
            )
        )

    assert validation.valid is False
    assert any(
        issue.path == "$.input_image_refs"
        and issue.code == "invalid_image_ref"
        and "valid JPEG, PNG, or WEBP bytes" in issue.message
        for issue in validation.issues
    )
    assert "valid JPEG, PNG, or WEBP bytes" in json.dumps(exc_info.value.data)
    assert httpx_mock.get_requests() == []
    assert session.exec(select(ActionCall)).all() == []
    budget = IntegrationBudgetRepository(session).get(project_id, "byteplus-ark")
    assert budget.current_month_spend == 0
    assert budget.current_month_calls == 0


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


def test_trackbooth_generated_read_action_calls_endpoint_without_catalog_preflight(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    _add_trackbooth_sync_responses(httpx_mock, _trackbooth_offers_findbyid_detail())
    sync_output = _sync_trackbooth_catalog(session, project_id, credential_ref)
    action_ref = _trackbooth_generated_action_ref(sync_output, "OffersController.findById")
    httpx_mock.add_response(
        method="GET",
        url="https://trackbooth.local.test/api/offers/offer-123",
        json={"data": {"id": "offer-123"}},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref=action_ref,
            input_json={"path_params": {"id": "offer-123"}},
            credential_ref=credential_ref,
        )
    ).data

    assert out.output_json["operation_id"] == "OffersController.findById"
    assert out.output_json["data"] == {"data": {"id": "offer-123"}}
    requests = httpx_mock.get_requests()
    assert requests[-1].url.path == "/api/offers/offer-123"
    assert [
        request.url.path
        for request in requests
        if request.url.path == "/api/agent-api/catalog/OffersController.findById"
    ] == []
    assert [
        request.url.path
        for request in requests
        if request.url.path == "/api/agent-api/catalog/export"
    ] == ["/api/agent-api/catalog/export"]


def test_trackbooth_generated_write_action_sends_body_without_catalog_preflight(
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
    httpx_mock.add_response(
        method="POST",
        url="https://trackbooth.local.test/api/links",
        json={"data": {"id": "link-1"}},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref=action_ref,
            input_json={
                "body": {
                    "campaign_id": "campaign-1",
                    "name": "Generated action link",
                    "routing_mode": "direct",
                    "offer_id": "offer-1",
                },
            },
            provider_context_json={"acting_as_account": "acct-managed"},
            credential_ref=credential_ref,
        )
    ).data

    assert out.output_json["operation_id"] == "LinksController.create"
    requests = httpx_mock.get_requests()
    actual = requests[-1]
    assert actual.method == "POST"
    assert actual.url.path == "/api/links"
    assert actual.headers["X-Acting-As-Account"] == "acct-managed"
    assert json.loads(actual.content)["routing_mode"] == "direct"


def test_trackbooth_generated_action_uses_provider_context_for_acting_account(
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
    httpx_mock.add_response(method="POST", json={"data": {"id": "link-acting"}})

    asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref=action_ref,
            input_json={
                "body": {
                    "campaign_id": "campaign-1",
                    "name": "Synced acting account",
                    "routing_mode": "direct",
                },
            },
            provider_context_json={"acting_as_account": "acct-managed"},
            credential_ref=credential_ref,
        )
    )
    assert httpx_mock.get_requests()[-1].headers["X-Acting-As-Account"] == "acct-managed"
    call = session.exec(
        select(ActionCall).where(ActionCall.action_key == action_ref.split(".", 1)[1])
    ).first()
    assert call is not None
    assert call.provider_context_json == {"acting_as_account": "acct-managed"}

    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref=action_ref,
        input_json={
            "acting_as_account": "acct-other",
            "body": {
                "campaign_id": "campaign-1",
                "name": "Wrong acting account",
                "routing_mode": "direct",
            },
        },
        credential_ref=credential_ref,
    )
    assert any(issue.code == "additional_property" for issue in validation.issues)


def test_trackbooth_validation_rejects_invalid_enum_and_missing_required_body_field(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    repo = ActionRepository(session)
    _add_trackbooth_sync_responses(httpx_mock, _trackbooth_links_create_detail())
    sync_output = _sync_trackbooth_catalog(session, project_id, credential_ref)
    action_ref = _trackbooth_generated_action_ref(sync_output, "LinksController.create")

    invalid_enum = repo.validate(
        project_id=project_id,
        action_ref=action_ref,
        input_json={
            "body": {
                "campaign_id": "campaign-1",
                "name": "Bad link",
                "routing_mode": "sideways",
            },
        },
        credential_ref=credential_ref,
    )
    assert any(
        issue.path == "$.body.routing_mode" and issue.code == "enum_mismatch"
        for issue in invalid_enum.issues
    )

    missing_body = repo.validate(
        project_id=project_id,
        action_ref=action_ref,
        input_json={},
        credential_ref=credential_ref,
    )
    assert {
        issue.path
        for issue in missing_body.issues
        if issue.code == "required" and issue.path.startswith("$.body.")
    } >= {"$.body.campaign_id", "$.body.name"}

    generated_invalid_enum = repo.validate(
        project_id=project_id,
        action_ref=action_ref,
        input_json={
            "body": {
                "campaign_id": "campaign-1",
                "name": "Bad generated link",
                "routing_mode": "sideways",
            },
        },
        credential_ref=credential_ref,
    )
    assert any(
        issue.path == "$.body.routing_mode" and issue.code == "enum_mismatch"
        for issue in generated_invalid_enum.issues
    )


def test_trackbooth_rejects_unsafe_base_url_without_outbound_request(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(
        session,
        project_id,
        api_base_url="http://10.0.0.1",
    )

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="trackbooth.catalog.search",
                input_json={},
                credential_ref=credential_ref,
            )
        )

    assert "private" in excinfo.value.data["error"]
    assert httpx_mock.get_requests() == []


def test_trackbooth_blocks_api_key_reveal_and_generate_operations(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)

    _add_trackbooth_sync_responses(
        httpx_mock,
        _trackbooth_api_key_reveal_detail(),
        _trackbooth_api_key_generate_detail(),
    )
    sync_output = _sync_trackbooth_catalog(session, project_id, credential_ref)

    assert sync_output["synced"] == 0
    assert sync_output["blocked_operation_ids"] == [
        "AccountApiKeyController.revealApiKey",
        "AccountApiKeyController.generateApiKey",
    ]
    assert (
        session.exec(select(Action).where(Action.key.like("%accountapikey_revealapikey%"))).first()
        is None
    )
    assert (
        session.exec(
            select(Action).where(Action.key.like("%accountapikey_generateapikey%"))
        ).first()
        is None
    )


def test_trackbooth_permission_error_is_preserved_for_agent_repair(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    httpx_mock.add_response(
        method="GET",
        url="https://trackbooth.local.test/api/agent-api/catalog/LinksController.create",
        status_code=403,
        text='{"message":"agent_api.access is required"}',
    )

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="trackbooth.operation.describe",
                input_json={"operation_id": "LinksController.create"},
                credential_ref=credential_ref,
            )
        )

    assert "403" in excinfo.value.data["error"]
    assert "agent_api.access" in excinfo.value.data["error"]


def test_action_describe_reports_project_readiness_and_provider_disabled(
    session: Session,
    project_id: int,
) -> None:
    _seed_action(session)
    credential_ref = _credential_ref(session, project_id)
    registry = ActionConnectorRegistry()
    registry.register(_FakeConnector())
    repo = ActionRepository(session, connectors=registry)

    ready = repo.describe(action_ref="test-actions.echo.run", project_id=project_id)

    assert ready.availability.status == "ready"
    assert ready.availability.executable is True
    assert ready.availability.credential_state == "available"

    provider = session.exec(select(Provider).where(Provider.key == "fake-provider")).one()
    provider.config_json = {"enabled": False}
    session.add(provider)
    session.commit()

    disabled = repo.describe(action_ref="test-actions.echo.run", project_id=project_id)

    assert disabled.availability.status == "provider_disabled"
    assert disabled.availability.executable is False
    assert disabled.availability.reasons[0] == "provider_disabled"
    with pytest.raises(ValidationError):
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="test-actions.echo.run",
                input_json={"name": "Ada"},
                credential_ref=credential_ref,
            )
        )


def test_action_describe_reports_disabled_project_plugin(
    session: Session,
    project_id: int,
) -> None:
    from stackos.repositories.plugins import PluginRepository

    PluginRepository(session).enable(project_id=project_id, plugin_slug="utils")
    PluginRepository(session).disable(project_id=project_id, plugin_slug="utils")

    described = ActionRepository(session).describe(
        project_id=project_id,
        action_ref="utils.web.read",
    )

    assert described.availability.status == "plugin_disabled"
    assert described.availability.executable is False
    assert described.availability.reasons[0] == "plugin_disabled"


def test_project_aware_action_describe_requires_existing_project(session: Session) -> None:
    with pytest.raises(NotFoundError):
        ActionRepository(session).describe(
            project_id=999999,
            action_ref="utils.web.read",
        )


def test_jina_action_preserves_optional_credentials(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.web.read",
        input_json={"url": "https://example.com"},
    )
    assert validation.valid is True
    assert validation.credential_ref is None

    httpx_mock.add_response(
        method="GET",
        url="https://r.jina.ai/https://example.com",
        text="# Example",
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="utils.web.read",
            input_json={"url": "https://example.com"},
        )
    ).data

    assert out.output_json == {"data": "# Example"}
    assert out.credential_ref is None
    assert out.action_call.credential_ref is None


def test_sitemap_action_executes_through_generic_connector(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://example.com/sitemap.xml",
        text=SITEMAP_URLSET,
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="utils.sitemap.fetch",
            input_json={"urls": ["https://example.com/sitemap.xml"], "max_entries": 10},
        )
    ).data

    assert out.credential_ref is None
    assert out.action_call.provider_key is None
    assert out.action_call.connector_key == "sitemap"
    assert out.output_json == {
        "entries": [
            {
                "url": "https://example.com/a",
                "lastmod": "2026-05-22",
                "changefreq": None,
                "priority": None,
                "source_sitemap": "https://example.com/sitemap.xml",
            },
            {
                "url": "https://example.com/b",
                "lastmod": None,
                "changefreq": None,
                "priority": None,
                "source_sitemap": "https://example.com/sitemap.xml",
            },
        ],
        "errors": [],
    }
    assert out.metadata_json == {"vendor": "sitemap", "operation": "fetch"}


def test_sitemap_action_rejects_empty_url_list(
    session: Session,
    project_id: int,
) -> None:
    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.sitemap.fetch",
        input_json={"urls": []},
    )

    assert validation.valid is False
    assert {issue.code for issue in validation.issues} == {"length"}


def test_custom_http_action_executes_static_webhook_with_daemon_side_auth(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    _seed_action(session)
    _seed_http_action(session)
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="internal-webhook",
        secret_payload=b"webhook-token",
        config_json={"label": "Internal webhook"},
    )
    from stackos.auth_providers import AuthRepository

    credential_ref = (
        AuthRepository(session)
        .status(
            project_id=project_id,
            provider_key="internal-webhook",
        )
        .connections[0]
        .credential_ref
    )
    httpx_mock.add_response(
        method="POST",
        url="https://hooks.example/internal/run",
        json={"ok": True, "authorization": "Bearer leaked-token"},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="test-actions.webhook.trigger",
            input_json={"task": "sync-catalog"},
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_requests()[0]
    request_body = json.loads(request.content.decode("utf-8"))
    rendered = json.dumps(out.model_dump(mode="json"))
    assert request.headers["authorization"] == "Bearer webhook-token"
    assert request_body == {"task": "sync-catalog"}
    assert out.action_call.provider_key == "internal-webhook"
    assert out.action_call.connector_key == "http"
    assert out.output_json == {
        "status_code": 200,
        "body": {"ok": True, "authorization": "[redacted]"},
    }
    assert out.metadata_json == {
        "vendor": "http",
        "operation": "request",
        "method": "POST",
    }
    assert "webhook-token" not in rendered
    assert "leaked-token" not in rendered


def test_hubspot_builtin_company_upsert_sends_documented_batch_body(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="hubspot",
        secret_payload=json.dumps({"access_token": "hubspot-secret"}).encode("utf-8"),
    )
    from stackos.auth_providers import AuthRepository

    credential_ref = (
        AuthRepository(session)
        .status(
            project_id=project_id,
            provider_key="hubspot",
        )
        .connections[0]
        .credential_ref
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/crm/objects/2026-03/companies/batch/upsert",
        json={"status": "COMPLETE", "results": [{"id": "123", "new": True}]},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="gtm.hubspot.crm.companies.batch_upsert",
            input_json={
                "id_property": "domain",
                "inputs": [
                    {
                        "properties": {
                            "domain": "example.com",
                            "name": "Example",
                        }
                    }
                ],
            },
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(out.model_dump(mode="json"))
    assert request.headers["authorization"] == "Bearer hubspot-secret"
    assert json.loads(request.content.decode("utf-8")) == {
        "inputs": [
            {
                "id": "example.com",
                "idProperty": "domain",
                "properties": {"domain": "example.com", "name": "Example"},
            }
        ]
    }
    assert out.action_call.connector_key == "hubspot"
    assert out.output_json["body"]["results"][0]["id"] == "123"
    assert "hubspot-secret" not in rendered


def test_meta_builtin_campaign_create_resolves_account_ref_and_sends_form_body(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="meta-ads",
        secret_payload=json.dumps({"access_token": "meta-secret"}).encode("utf-8"),
        config_json={"api_version": "v25.0", "accounts": {"primary": "act_123"}},
    )
    from stackos.auth_providers import AuthRepository

    credential_ref = (
        AuthRepository(session)
        .status(
            project_id=project_id,
            provider_key="meta-ads",
        )
        .connections[0]
        .credential_ref
    )
    httpx_mock.add_response(
        method="POST",
        url="https://graph.facebook.com/v25.0/act_123/campaigns",
        json={"id": "cmp_123"},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="media-buying.meta.campaign.create",
            input_json={
                "account_ref": "primary",
                "campaign": {
                    "name": "Launch",
                    "objective": "OUTCOME_TRAFFIC",
                    "status": "PAUSED",
                    "special_ad_categories": [],
                },
            },
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_requests()[0]
    form = parse_qs(request.content.decode("utf-8"))
    rendered = json.dumps(out.model_dump(mode="json"))
    assert request.headers["authorization"] == "Bearer meta-secret"
    assert form["name"] == ["Launch"]
    assert form["objective"] == ["OUTCOME_TRAFFIC"]
    assert json.loads(form["special_ad_categories"][0]) == []
    assert out.action_call.connector_key == "meta-ads"
    assert out.output_json["body"]["id"] == "cmp_123"
    assert "meta-secret" not in rendered
    assert "act_123" not in json.dumps(out.action_call.request_json)


def test_google_ads_builtin_report_search_sets_required_headers_and_body(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="google-ads",
        secret_payload=json.dumps(
            {"access_token": "google-access", "developer_token": "google-dev"}
        ).encode("utf-8"),
        config_json={
            "api_version": "v22",
            "manager_account_ref": "111-222-3333",
            "customers": {"main": "444-555-6666"},
        },
    )
    from stackos.auth_providers import AuthRepository

    credential_ref = (
        AuthRepository(session)
        .status(
            project_id=project_id,
            provider_key="google-ads",
        )
        .connections[0]
        .credential_ref
    )
    httpx_mock.add_response(
        method="POST",
        url="https://googleads.googleapis.com/v22/customers/4445556666/googleAds:search",
        json={"results": [{"campaign": {"id": "987"}}]},
        headers={"request-id": "req-1"},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="media-buying.google.report.search",
            input_json={
                "customer_ref": "main",
                "query": "SELECT campaign.id FROM campaign LIMIT 1",
            },
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(out.model_dump(mode="json"))
    assert request.headers["authorization"] == "Bearer google-access"
    assert request.headers["developer-token"] == "google-dev"
    assert request.headers["login-customer-id"] == "1112223333"
    assert json.loads(request.content.decode("utf-8")) == {
        "query": "SELECT campaign.id FROM campaign LIMIT 1",
    }
    assert out.metadata_json["request_id"] == "req-1"
    assert "google-access" not in rendered
    assert "google-dev" not in rendered


def test_taboola_builtin_campaign_create_uses_backstage_account_endpoint(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="taboola",
        secret_payload=json.dumps({"access_token": "taboola-access"}).encode("utf-8"),
        config_json={"accounts": {"main": "demo-account"}},
    )
    from stackos.auth_providers import AuthRepository

    credential_ref = (
        AuthRepository(session)
        .status(
            project_id=project_id,
            provider_key="taboola",
        )
        .connections[0]
        .credential_ref
    )
    httpx_mock.add_response(
        method="POST",
        url="https://backstage.taboola.com/backstage/api/1.0/demo-account/campaigns/",
        json={"id": "campaign-1", "name": "Demo"},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="media-buying.taboola.campaign.create",
            input_json={
                "account_ref": "main",
                "campaign": {
                    "name": "Demo",
                    "branding_text": "Example",
                    "cpc": 0.25,
                    "spending_limit": 1000,
                    "spending_limit_model": "MONTHLY",
                    "marketing_objective": "DRIVE_WEBSITE_TRAFFIC",
                },
            },
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(out.model_dump(mode="json"))
    assert request.headers["authorization"] == "Bearer taboola-access"
    assert json.loads(request.content.decode("utf-8"))["marketing_objective"] == (
        "DRIVE_WEBSITE_TRAFFIC"
    )
    assert out.action_call.connector_key == "taboola"
    assert out.output_json["body"]["id"] == "campaign-1"
    assert "taboola-access" not in rendered


def test_deferred_action_validation_reports_execution_mode(
    session: Session,
    project_id: int,
) -> None:
    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="media-buying.outbrain.campaign.create",
        input_json={"marketer_ref": "marketer", "campaign": {"name": "Deferred"}},
    )
    described = ActionRepository(session).describe(
        project_id=project_id,
        action_ref="media-buying.outbrain.campaign.create",
    )

    assert validation.valid is False
    assert validation.issues[0].code == "execution_deferred"
    assert described.availability.status == "deferred"
    assert described.availability.execution_mode == "deferred-partner-api"


def test_firecrawl_extract_is_manifest_deferred_until_status_action_exists(
    session: Session,
    project_id: int,
) -> None:
    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.web.extract",
        input_json={"url": "https://example.com", "schema": {"type": "object"}},
    )
    described = ActionRepository(session).describe(
        project_id=project_id,
        action_ref="utils.web.extract",
    )

    assert validation.valid is False
    assert validation.issues[0].code == "execution_deferred"
    assert described.manifest.connector_key is None
    assert described.availability.status == "deferred"
    assert described.availability.execution_mode == "deferred-firecrawl-async-extract"


def test_branding_evidence_actions_capture_raw_and_mark_sanitization(
    session: Session,
    project_id: int,
) -> None:
    repo = ActionRepository(session)
    capture_payload = {
        "title": "Kitchen run receipt",
        "evidence_type": "run",
        "source": {"system": "ops-ledger", "ref": "run-42", "url": "https://example.com/run-42"},
        "captured_at": "2026-06-11T10:00:00Z",
        "summary": "Service recovery run completed.",
        "evidence_payload": {"duration_minutes": 17},
        "stream_refs": ["ops-ground-truth"],
        "altitude": "A3",
    }

    validation = repo.validate(
        project_id=project_id,
        action_ref="branding.evidence.capture",
        input_json=capture_payload,
    )
    created = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="branding.evidence.capture",
            input_json=capture_payload,
        )
    ).data

    assert validation.valid is True
    assert validation.connector_registered is True
    assert created.output_json["sanitization_status"] == "raw"
    record_id = created.output_json["resource_record_id"]
    record = ResourceRepository(session).get_record(record_id)
    assert record.plugin_slug == "branding"
    assert record.resource_key == "evidence-item"
    assert record.data_json["sanitization_status"] == "raw"
    assert record.data_json["source"]["system"] == "ops-ledger"

    invalid_capture = repo.validate(
        project_id=project_id,
        action_ref="branding.evidence.capture",
        input_json={**capture_payload, "sanitization_status": "cleared"},
    )
    assert invalid_capture.valid is False
    assert {issue.code for issue in invalid_capture.issues} >= {
        "enum_mismatch",
        "fail_closed_clearance",
    }

    missing_decision = repo.validate(
        project_id=project_id,
        action_ref="branding.evidence.sanitize-mark",
        input_json={
            "evidence_ref": created.output_json["evidence_ref"],
            "verdict": "cleared",
            "reviewer": "operator",
            "reason": "Public-safe aggregate receipt.",
        },
    )
    assert missing_decision.valid is False
    assert any(issue.path == "$.decision_ref" for issue in missing_decision.issues)

    marked = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="branding.evidence.sanitize-mark",
            input_json={
                "evidence_ref": created.output_json["evidence_ref"],
                "verdict": "cleared",
                "reviewer": "operator",
                "reason": "Public-safe aggregate receipt.",
                "decision_ref": "decision:clear-run-42",
                "decided_at": "2026-06-11T11:00:00Z",
            },
        )
    ).data
    updated = ResourceRepository(session).get_record(record_id)

    assert marked.output_json["verdict"] == "cleared"
    assert updated.data_json["sanitization_status"] == "cleared"
    assert updated.data_json["sanitization_verdict"] == {
        "verdict": "cleared",
        "reviewer": "operator",
        "reason": "Public-safe aggregate receipt.",
        "decision_ref": "decision:clear-run-42",
        "decided_at": "2026-06-11T11:00:00Z",
    }


def test_salesforce_builtin_account_upsert_uses_external_id_endpoint(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="salesforce",
        secret_payload=json.dumps({"access_token": "sf-secret"}).encode("utf-8"),
        config_json={
            "instance_url": "https://example.my.salesforce.com",
            "api_version": "v61.0",
            "external_id_fields": {"domain": "External_Id__c"},
        },
    )
    credential_ref = _provider_credential_ref(session, project_id, "salesforce")
    httpx_mock.add_response(
        method="PATCH",
        url=(
            "https://example.my.salesforce.com/services/data/v61.0/"
            "sobjects/Account/External_Id__c/acme?updateOnly=true"
        ),
        json={"id": "001xx", "success": True},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="gtm.salesforce.account.upsert_by_external_id",
            input_json={
                "external_id_policy_ref": "domain",
                "external_id_value": "acme",
                "account": {"properties": {"Name": "Acme", "External_Id__c": "acme"}},
                "update_only": True,
            },
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(out.model_dump(mode="json"))
    assert request.headers["authorization"] == "Bearer sf-secret"
    assert json.loads(request.content.decode("utf-8")) == {"Name": "Acme"}
    assert out.action_call.connector_key == "salesforce"
    assert "sf-secret" not in rendered


def test_apollo_builtin_people_enrich_sends_single_record_params(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="apollo",
        secret_payload=json.dumps({"api_key": "apollo-secret"}).encode("utf-8"),
    )
    credential_ref = _provider_credential_ref(session, project_id, "apollo")
    httpx_mock.add_response(method="POST", json={"person": {"id": "p1"}})

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="gtm.apollo.people.enrich",
            input_json={"record": {"email": "ada@example.com"}},
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(out.model_dump(mode="json"))
    assert str(request.url) == "https://api.apollo.io/api/v1/people/match?email=ada%40example.com"
    assert request.headers["authorization"] == "Bearer apollo-secret"
    assert out.action_call.connector_key == "apollo"
    assert "apollo-secret" not in rendered


def test_apollo_phone_reveal_requires_webhook_url(
    session: Session,
    project_id: int,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="apollo",
        secret_payload=json.dumps({"api_key": "apollo-secret"}).encode("utf-8"),
        config_json={"access_scope": "endpoint"},
    )
    credential_ref = _provider_credential_ref(session, project_id, "apollo")

    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="gtm.apollo.people.enrich",
        input_json={"record": {"email": "ada@example.com"}, "reveal_phone_number": True},
        credential_ref=credential_ref,
    )

    assert validation.valid is False
    assert any(
        issue.path == "$.webhook_url" and issue.code == "validation_error"
        for issue in validation.issues
    )


def test_apollo_people_search_requires_master_key_scope(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="apollo",
        secret_payload=json.dumps({"api_key": "apollo-secret"}).encode("utf-8"),
        config_json={"access_scope": "endpoint"},
    )
    credential_ref = _provider_credential_ref(session, project_id, "apollo")

    with pytest.raises(ConflictError) as exc_info:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.apollo.people.search",
                input_json={"filters": {"person_titles": ["Founder"]}},
                credential_ref=credential_ref,
            )
        )

    assert "master" in exc_info.value.data["error"]
    assert httpx_mock.get_requests() == []


def test_pipedrive_builtin_deal_search_uses_search_whitelist(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="pipedrive",
        secret_payload=json.dumps({"api_token": "pd-secret"}).encode("utf-8"),
        config_json={"company_domain": "acme", "organizations": {"org": 42}},
    )
    credential_ref = _provider_credential_ref(session, project_id, "pipedrive")
    httpx_mock.add_response(method="GET", json={"data": [{"id": 1}]})

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="gtm.pipedrive.deals.search",
            input_json={
                "term": "Acme",
                "organization_ref": "org",
                "status": "open",
                "limit": 10,
            },
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(out.model_dump(mode="json"))
    assert request.headers["x-api-token"] == "pd-secret"
    assert request.url.path == "/api/v2/deals/search"
    assert request.url.params["term"] == "Acme"
    assert request.url.params["organization_id"] == "42"
    assert "pipeline_id" not in request.url.params
    assert out.action_call.connector_key == "pipedrive"
    assert "pd-secret" not in rendered


def test_clay_builtin_table_webhook_submits_configured_rows(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="clay",
        secret_payload=json.dumps({}).encode("utf-8"),
        config_json={"webhooks": {"leads": "https://hooks.clay.com/t/leads"}},
    )
    credential_ref = _provider_credential_ref(session, project_id, "clay")
    httpx_mock.add_response(method="POST", json={"accepted": True})

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="gtm.clay.table.webhook.submit",
            input_json={"table_ref": "leads", "rows": [{"email": "ada@example.com"}]},
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_requests()[0]
    assert str(request.url) == "https://hooks.clay.com/t/leads"
    assert json.loads(request.content.decode("utf-8")) == {"rows": [{"email": "ada@example.com"}]}
    assert out.action_call.connector_key == "clay"


def test_outreach_builtin_sequence_state_posts_json_api_relationships(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="outreach",
        secret_payload=json.dumps({"access_token": "outreach-secret"}).encode("utf-8"),
        config_json={
            "sequences": {"seq": 1},
            "prospects": {"prospect": 2},
            "mailboxes": {"mailbox": 3},
        },
    )
    credential_ref = _provider_credential_ref(session, project_id, "outreach")
    httpx_mock.add_response(method="POST", json={"data": {"id": "state-1"}})

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="gtm.outreach.sequence_state.create",
            input_json={
                "sequence_ref": "seq",
                "prospect_ref": "prospect",
                "mailbox_ref": "mailbox",
            },
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(out.model_dump(mode="json"))
    body = json.loads(request.content.decode("utf-8"))
    assert str(request.url) == "https://api.outreach.io/api/v2/sequenceStates"
    assert request.headers["content-type"] == "application/vnd.api+json"
    assert body["data"]["relationships"]["sequence"]["data"]["id"] == "1"
    assert out.action_call.connector_key == "outreach"
    assert "outreach-secret" not in rendered


def test_salesloft_builtin_cadence_membership_posts_ids(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="salesloft",
        secret_payload=json.dumps({"access_token": "salesloft-secret"}).encode("utf-8"),
        config_json={"cadences": {"cadence": 10}, "persons": {"person": 20}},
    )
    credential_ref = _provider_credential_ref(session, project_id, "salesloft")
    httpx_mock.add_response(method="POST", json={"data": {"id": 30}})

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="gtm.salesloft.cadence_membership.create",
            input_json={"cadence_ref": "cadence", "person_ref": "person"},
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(out.model_dump(mode="json"))
    assert str(request.url) == "https://api.salesloft.com/v2/cadence_memberships"
    assert json.loads(request.content.decode("utf-8")) == {
        "cadence_id": 10,
        "person_id": 20,
    }
    assert out.action_call.connector_key == "salesloft"
    assert "salesloft-secret" not in rendered


def test_google_workspace_builtin_gmail_send_posts_raw_message(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="google-workspace",
        secret_payload=json.dumps({"access_token": "workspace-secret"}).encode("utf-8"),
    )
    credential_ref = _provider_credential_ref(session, project_id, "google-workspace")
    httpx_mock.add_response(method="POST", json={"id": "msg-1"})

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="gtm.google-workspace.gmail.message.send",
            input_json={"message": {"raw": "UmF3"}},
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(out.model_dump(mode="json"))
    assert str(request.url) == "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    assert json.loads(request.content.decode("utf-8")) == {"raw": "UmF3"}
    assert out.action_call.connector_key == "google-workspace"
    assert "workspace-secret" not in rendered


def test_microsoft_graph_builtin_mail_send_requires_user_for_application_auth(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="microsoft-365",
        secret_payload=json.dumps({"access_token": "graph-secret"}).encode("utf-8"),
        config_json={"auth_mode": "application", "users": {"primary": "ada@example.com"}},
    )
    credential_ref = _provider_credential_ref(session, project_id, "microsoft-365")
    httpx_mock.add_response(method="POST", status_code=202, json={})

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="gtm.microsoft-365.graph.mail.send",
            input_json={
                "user_ref": "primary",
                "message": {
                    "subject": "Hello",
                    "toRecipients": [],
                    "body": {"contentType": "Text", "content": "Hi"},
                },
            },
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(out.model_dump(mode="json"))
    assert str(request.url) == "https://graph.microsoft.com/v1.0/users/ada%40example.com/sendMail"
    assert out.action_call.connector_key == "microsoft-365"
    assert "graph-secret" not in rendered


def test_firecrawl_action_executes_through_generic_connector(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="firecrawl",
        secret_payload=b"fc-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="firecrawl",
        monthly_budget_usd=10.0,
    )
    from stackos.auth_providers import AuthRepository

    credential_ref = (
        AuthRepository(session)
        .status(
            project_id=project_id,
            provider_key="firecrawl",
        )
        .connections[0]
        .credential_ref
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.firecrawl.dev/v2/scrape",
        json={"data": {"markdown": "# Hello"}},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="utils.web.scrape",
            input_json={"url": "https://example.com"},
            credential_ref=credential_ref,
        )
    ).data

    assert out.output_json == {"data": {"markdown": "# Hello"}}
    assert out.action_call.provider_key == "firecrawl"
    assert out.action_call.connector_key == "firecrawl"


def test_dataforseo_action_executes_with_daemon_side_login_config(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="dataforseo",
        secret_payload=b"password",
        config_json={"login": "login@example.com"},
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="dataforseo",
        monthly_budget_usd=10.0,
    )
    from stackos.auth_providers import AuthRepository

    credential_ref = (
        AuthRepository(session)
        .status(
            project_id=project_id,
            provider_key="dataforseo",
        )
        .connections[0]
        .credential_ref
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.dataforseo.com/v3/serp/google/organic/live/advanced",
        json={"tasks": [{"cost": 0.002, "result": [{"title": "Example"}]}]},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="seo.serp.analyze",
            input_json={"keyword": "stackos", "depth": 10},
            credential_ref=credential_ref,
        )
    ).data

    rendered = json.dumps(out.model_dump(mode="json"))
    assert out.action_call.provider_key == "dataforseo"
    assert out.action_call.connector_key == "dataforseo"
    assert out.output_json["tasks"][0]["result"][0]["title"] == "Example"
    assert "password" not in rendered
    assert "login@example.com" not in rendered


def test_dataforseo_keyword_and_serp_limits_match_live_contracts(
    session: Session,
    project_id: int,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="dataforseo",
        secret_payload=b"password",
        config_json={"login": "login@example.com"},
    )
    credential_ref = _provider_credential_ref(session, project_id, "dataforseo")
    repo = ActionRepository(session)

    keyword_validation = repo.validate(
        project_id=project_id,
        action_ref="seo.keyword.research",
        input_json={"keywords": [f"keyword-{idx}" for idx in range(1001)]},
        credential_ref=credential_ref,
    )
    serp_validation = repo.validate(
        project_id=project_id,
        action_ref="seo.serp.analyze",
        input_json={"keyword": "stackos", "depth": 101},
        credential_ref=credential_ref,
    )

    assert keyword_validation.valid is False
    assert any(
        issue.path == "$.keywords" and issue.code == "length" for issue in keyword_validation.issues
    )
    assert serp_validation.valid is False
    assert any(
        issue.path == "$.depth" and issue.code == "range" for issue in serp_validation.issues
    )


def test_dataforseo_paa_action_uses_explicit_action_contract(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="dataforseo",
        secret_payload=b"password",
        config_json={"login": "login@example.com"},
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="dataforseo",
        monthly_budget_usd=10.0,
    )
    from stackos.auth_providers import AuthRepository

    credential_ref = (
        AuthRepository(session)
        .status(
            project_id=project_id,
            provider_key="dataforseo",
        )
        .connections[0]
        .credential_ref
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.dataforseo.com/v3/serp/google/organic/live/advanced",
        json={
            "tasks": [
                {
                    "cost": 0.001,
                    "result": [{"items": [{"type": "people_also_ask", "title": "What is SEO?"}]}],
                }
            ]
        },
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="seo.paa.extract",
            input_json={"keyword": "seo tools"},
            credential_ref=credential_ref,
        )
    ).data

    request_body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    rendered = json.dumps(out.model_dump(mode="json"))
    assert request_body[0]["keyword"] == "seo tools"
    assert request_body[0]["people_also_ask_click_depth"] == 1
    assert out.action_call.provider_key == "dataforseo"
    assert out.action_call.connector_key == "dataforseo"
    assert out.output_json["tasks"][0]["result"][0]["items"][0]["title"] == "What is SEO?"
    assert "password" not in rendered
    assert "login@example.com" not in rendered


def test_serper_action_executes_with_daemon_side_credential(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="serper",
        secret_payload=b"serper-secret",
    )
    credential_ref = _provider_credential_ref(session, project_id, "serper")
    httpx_mock.add_response(
        method="POST",
        url="https://google.serper.dev/search",
        json={"organic": [{"title": "StackOS"}], "credits": 1},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="seo.serper.search",
            input_json={
                "query": "stackos",
                "num": 3,
                "country": "us",
                "language": "en",
            },
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_requests()[0]
    request_body = json.loads(request.content.decode("utf-8"))
    rendered = json.dumps(out.model_dump(mode="json"))
    assert request.headers["X-API-KEY"] == "serper-secret"
    assert request_body == {"q": "stackos", "num": 3, "gl": "us", "hl": "en"}
    assert out.action_call.provider_key == "serper"
    assert out.action_call.connector_key == "serper"
    assert out.output_json["organic"][0]["title"] == "StackOS"
    assert "serper-secret" not in rendered


def test_serper_action_validation_bounds_provider_inputs(
    session: Session,
    project_id: int,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="serper",
        secret_payload=b"serper-secret",
    )
    credential_ref = _provider_credential_ref(session, project_id, "serper")

    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="seo.serper.search",
        input_json={"query": "stackos", "num": 101, "page": 11},
        credential_ref=credential_ref,
    )

    assert validation.valid is False
    assert any(issue.path == "$.num" and issue.code == "range" for issue in validation.issues)
    assert any(issue.path == "$.page" and issue.code == "range" for issue in validation.issues)


def test_wordpress_post_create_action_uses_daemon_side_site_config(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="wordpress",
        secret_payload=json.dumps(
            {"username": "editor", "application_password": "app pass"}
        ).encode("utf-8"),
        config_json={"wp_url": "https://wp.example"},
    )
    from stackos.auth_providers import AuthRepository

    credential_ref = (
        AuthRepository(session)
        .status(
            project_id=project_id,
            provider_key="wordpress",
        )
        .connections[0]
        .credential_ref
    )
    httpx_mock.add_response(
        method="POST",
        url="https://wp.example/wp-json/wp/v2/posts",
        json={"id": 42, "link": "https://wp.example/hello-world/"},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="publishing.wordpress.post.create",
            input_json={
                "post": {
                    "title": "Hello world",
                    "content": "<p>Body</p>",
                    "status": "draft",
                }
            },
            credential_ref=credential_ref,
        )
    ).data

    request_body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    rendered = json.dumps(out.model_dump(mode="json"))
    assert request_body == {
        "title": "Hello world",
        "content": "<p>Body</p>",
        "status": "draft",
    }
    assert out.action_call.provider_key == "wordpress"
    assert out.action_call.connector_key == "wordpress"
    assert out.output_json["id"] == 42
    assert "app pass" not in rendered
    assert "editor" not in rendered


def test_ghost_post_create_action_uses_daemon_side_admin_config(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="ghost",
        secret_payload=b"keyid:00112233445566778899aabbccddeeff",
        config_json={"ghost_url": "https://ghost.example", "api_version": "v5.0"},
    )
    from stackos.auth_providers import AuthRepository

    credential_ref = (
        AuthRepository(session)
        .status(
            project_id=project_id,
            provider_key="ghost",
        )
        .connections[0]
        .credential_ref
    )
    httpx_mock.add_response(
        method="POST",
        url="https://ghost.example/ghost/api/admin/posts/?source=html",
        json={"posts": [{"id": "post-1", "url": "https://ghost.example/hello/"}]},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="publishing.ghost.post.create",
            input_json={
                "post": {
                    "title": "Hello world",
                    "html": "<p>Body</p>",
                    "status": "draft",
                }
            },
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_requests()[0]
    request_body = json.loads(request.content.decode("utf-8"))
    rendered = json.dumps(out.model_dump(mode="json"))
    assert request_body == {
        "posts": [
            {
                "title": "Hello world",
                "html": "<p>Body</p>",
                "status": "draft",
            }
        ]
    }
    assert request.headers["authorization"].startswith("Ghost ")
    assert out.action_call.provider_key == "ghost"
    assert out.action_call.connector_key == "ghost"
    assert out.output_json["posts"][0]["id"] == "post-1"
    assert "00112233445566778899aabbccddeeff" not in rendered
    assert "keyid" not in rendered


def test_action_validate_reports_schema_connector_and_credential_issues(
    session: Session,
    project_id: int,
) -> None:
    _seed_action(session)
    registry = ActionConnectorRegistry()
    registry.register(_FakeConnector())

    validation = ActionRepository(session, connectors=registry).validate(
        project_id=project_id,
        action_ref="test-actions.echo.run",
        input_json={"extra": True},
    )

    codes = {issue.code for issue in validation.issues}
    assert validation.valid is False
    assert {"required", "additional_property", "credential_required"} <= codes
    assert validation.connector_registered is True
    assert validation.estimated_cost_cents == 12


def test_action_inputs_and_manifests_reject_raw_secrets(
    session: Session,
    project_id: int,
) -> None:
    _seed_action(session)

    with pytest.raises(ValidationError):
        ActionRepository(session).validate(
            project_id=project_id,
            action_ref="test-actions.echo.run",
            input_json={"name": "Ada", "api_key": "sk-leak"},
        )

    action = session.exec(select(Action).where(Action.key == "echo.run")).one()
    action.config_json = {"connector": "fake.echo", "client_secret": "leak"}
    session.add(action)
    session.commit()

    with pytest.raises(ValidationError):
        ActionRepository(session).describe(action_ref="test-actions.echo.run")


def test_noauth_action_rejects_unallowed_credential_ref(
    session: Session,
    project_id: int,
) -> None:
    _seed_action(session)
    _seed_noauth_action(session)
    credential_ref = _credential_ref(session, project_id)
    fake = _NoAuthConnector()
    registry = ActionConnectorRegistry()
    registry.register(fake)
    repo = ActionRepository(session, connectors=registry)

    validation = repo.validate(
        project_id=project_id,
        action_ref="test-actions.noauth.run",
        input_json={},
        credential_ref=credential_ref,
    )

    assert validation.valid is False
    assert {issue.code for issue in validation.issues} == {"credential_not_allowed"}
    with pytest.raises(ValidationError):
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="test-actions.noauth.run",
                input_json={},
                credential_ref=credential_ref,
            )
        )
    assert fake.calls == 0


def test_action_validate_enforces_credential_policy_without_project_id(
    session: Session,
    project_id: int,
) -> None:
    _seed_action(session)
    _seed_noauth_action(session)
    credential_ref = _credential_ref(session, project_id)

    registry = ActionConnectorRegistry()
    registry.register(_NoAuthConnector())

    validation = ActionRepository(session, connectors=registry).validate(
        action_ref="test-actions.noauth.run",
        input_json={},
        credential_ref=credential_ref,
    )

    assert validation.valid is False
    assert {issue.code for issue in validation.issues} == {"credential_not_allowed"}


def test_idempotency_replay_rejects_different_payload(
    session: Session,
    project_id: int,
) -> None:
    _seed_action(session)
    credential_ref = _credential_ref(session, project_id)
    registry = ActionConnectorRegistry()
    registry.register(_FakeConnector())
    repo = ActionRepository(session, connectors=registry)

    asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="test-actions.echo.run",
            input_json={"name": "Ada"},
            credential_ref=credential_ref,
            idempotency_key="same-action",
        )
    )

    with pytest.raises(ConflictError):
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="test-actions.echo.run",
                input_json={"name": "Grace"},
                credential_ref=credential_ref,
                idempotency_key="same-action",
            )
        )


def test_action_call_step_scope_requires_parent_project_match(
    session: Session,
    project_id: int,
) -> None:
    from stackos.repositories.projects import ProjectRepository

    _seed_action(session)
    _seed_noauth_action(session)
    other_project_id = (
        ProjectRepository(session)
        .create(
            slug="other-project",
            name="Other Project",
            domain="other.example",
            locale="en-US",
        )
        .data.id
    )
    assert other_project_id is not None
    other_plan = (
        RunPlanRepository(session)
        .create(
            project_id=other_project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "other.scope.run",
                "title": "Other scope run",
                "steps": [{"id": "step", "title": "Step"}],
            },
        )
        .data
    )
    other_step_id = other_plan.steps[0].id
    registry = ActionConnectorRegistry()
    registry.register(_NoAuthConnector())

    with pytest.raises(NotFoundError):
        asyncio.run(
            ActionRepository(session, connectors=registry).execute(
                project_id=project_id,
                action_ref="test-actions.noauth.run",
                input_json={},
                run_plan_step_id=other_step_id,
            )
        )
    assert session.exec(select(ActionCall)).all() == []


def test_idempotency_replay_still_enforces_step_scope(
    session: Session,
    project_id: int,
) -> None:
    from stackos.repositories.projects import ProjectRepository

    _seed_action(session)
    _seed_noauth_action(session)
    registry = ActionConnectorRegistry()
    registry.register(_NoAuthConnector())
    repo = ActionRepository(session, connectors=registry)
    asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="test-actions.noauth.run",
            input_json={},
            idempotency_key="same-noauth-action",
        )
    )

    other_project_id = (
        ProjectRepository(session)
        .create(
            slug="third-project",
            name="Third Project",
            domain="third.example",
            locale="en-US",
        )
        .data.id
    )
    assert other_project_id is not None
    other_plan = (
        RunPlanRepository(session)
        .create(
            project_id=other_project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "third.scope.run",
                "title": "Third scope run",
                "steps": [{"id": "step", "title": "Step"}],
            },
        )
        .data
    )

    with pytest.raises(NotFoundError):
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="test-actions.noauth.run",
                input_json={},
                idempotency_key="same-noauth-action",
                run_plan_step_id=other_plan.steps[0].id,
            )
        )
    assert len(session.exec(select(ActionCall)).all()) == 1
