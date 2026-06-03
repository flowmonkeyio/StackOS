"""Repository tests for the StackOS internal action executor."""

from __future__ import annotations

import asyncio
import json
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
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError
from stackos.repositories.projects import (
    IntegrationBudgetRepository,
    IntegrationCredentialRepository,
    ProjectRepository,
)
from stackos.repositories.run_plans import RunPlanRepository


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
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{base_url}/api/agent-api/catalog",
        json={"data": details},
    )
    for detail in details:
        httpx_mock.add_response(
            method="GET",
            url=f"{base_url}/api/agent-api/catalog/{detail['operation_id']}",
            json={"data": detail},
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
    assert action_ref.startswith("trackbooth.api.ctx_")
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
        issue.path == "$.provider_context_json.extra"
        and issue.code == "additional_property"
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
    actual = httpx_mock.get_requests()[2]
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
    actual = httpx_mock.get_requests()[2]
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
    assert out.output_json["blocked_operation_ids"] == []
    assert out.output_json["inventory_scope_key"].startswith("ctx_")
    links_action_ref = _trackbooth_generated_action_ref(out.output_json, "LinksController.create")
    offers_action_ref = _trackbooth_generated_action_ref(
        out.output_json,
        "OffersController.findById",
    )
    assert {ref.rsplit(".", 1)[-1] for ref in out.output_json["action_refs"]} == {
        "links_create",
        "offers_findbyid",
    }

    repo = ActionRepository(session)
    described = repo.describe(
        project_id=project_id,
        action_ref=links_action_ref,
    )

    assert described.manifest.operation == "operation.execute"
    assert described.manifest.config_json["inventory_source"] == "trackbooth.catalog.sync"
    assert described.manifest.config_json["inventory_state"] == "active"
    assert described.manifest.config_json["inventory_project_id"] == project_id
    assert described.manifest.config_json["inventory_credential_ref"] == credential_ref
    assert described.manifest.config_json["inventory_api_base_url"] == (
        "https://trackbooth.local.test"
    )
    assert "acting_as_account" not in described.manifest.input_schema_json["properties"]
    assert described.manifest.provider_context_schema_json["properties"][
        "acting_as_account"
    ]["type"] == "string"
    repo.describe(project_id=project_id, action_ref=offers_action_ref)
    assert described.manifest.input_schema_json["properties"]["body"]["properties"][
        "routing_mode"
    ]["enum"] == ["direct", "rules"]
    assert described.manifest.output_schema_json["properties"]["data"]["properties"]["data"][
        "x_trackbooth_type"
    ] == "LinkDetailItem"


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
    offers_key = offers_ref.removeprefix("trackbooth.")

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
    assert out.output_json["updated"] == 1
    assert out.output_json["pruned"] == 1

    repo = ActionRepository(session)
    repo.describe(project_id=project_id, action_ref=links_ref)
    with pytest.raises(NotFoundError):
        repo.describe(project_id=project_id, action_ref=offers_ref)
    retired = session.exec(select(Action).where(Action.key == offers_key)).first()
    assert retired is not None
    assert retired.config_json["inventory_state"] == "retired"
    assert retired.config_json["execution_mode"] == "deferred.retired"


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
    assert out.output_json["updated"] == 1
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

    other_project_id = ProjectRepository(session).create(
        slug="trackbooth-other",
        name="Trackbooth Other",
        domain="other.example",
        locale="en-US",
    ).data.id
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
    ] == ["/api/agent-api/catalog/OffersController.findById"]


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
    call = session.exec(select(ActionCall).where(ActionCall.action_key == action_ref.split(".", 1)[1])).first()
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
    assert session.exec(
        select(Action).where(Action.key.like("%accountapikey_revealapikey%"))
    ).first() is None
    assert session.exec(
        select(Action).where(Action.key.like("%accountapikey_generateapikey%"))
    ).first() is None


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
