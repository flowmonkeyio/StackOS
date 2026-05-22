"""Repository tests for the StackOS internal action executor."""

from __future__ import annotations

import asyncio
import json

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from content_stack.actions import (
    ActionConnectorRegistry,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionRepository,
    ActionValidationIssue,
)
from content_stack.db.models import (
    Action,
    ActionCall,
    CredentialUsageEvent,
    Plugin,
    PluginSource,
    Provider,
)
from content_stack.repositories.base import ConflictError, NotFoundError, ValidationError
from content_stack.repositories.projects import (
    IntegrationBudgetRepository,
    IntegrationCredentialRepository,
)
from content_stack.repositories.run_plans import RunPlanRepository


class _FakeConnector:
    key = "fake.echo"

    def __init__(self) -> None:
        self.calls = 0
        self.saw_plaintext: bytes | None = None

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
        self.saw_plaintext = request.credential.plaintext_payload
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


def _credential_ref(session: Session, project_id: int) -> str:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="fake-provider",
        plaintext_payload=b"daemon-only-secret",
        config_json={"api_key": "daemon-only-secret", "label": "Fake"},
    )
    from content_stack.auth_providers import AuthRepository

    status = AuthRepository(session).status(project_id=project_id, provider_key="fake-provider")
    return status.connections[0].credential_ref


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
    assert fake.saw_plaintext == b"daemon-only-secret"
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
        "utils.web.scrape": ("firecrawl", True, "unknown"),
        "utils.web.read": ("jina", False, "ready"),
        "utils.sitemap.fetch": ("sitemap", False, "ready"),
        "utils.reddit.search-subreddit": ("reddit", True, "unknown"),
        "seo.keyword.research": ("dataforseo", True, "unknown"),
        "seo.serp.analyze": ("dataforseo", True, "unknown"),
        "seo.paa.extract": ("dataforseo", True, "unknown"),
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
    from content_stack.repositories.plugins import PluginRepository

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


def test_firecrawl_action_executes_through_generic_connector(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="firecrawl",
        plaintext_payload=b"fc-key",
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="firecrawl",
        monthly_budget_usd=10.0,
    )
    from content_stack.auth_providers import AuthRepository

    credential_ref = AuthRepository(session).status(
        project_id=project_id,
        provider_key="firecrawl",
    ).connections[0].credential_ref
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
        plaintext_payload=b"password",
        config_json={"login": "login@example.com"},
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="dataforseo",
        monthly_budget_usd=10.0,
    )
    from content_stack.auth_providers import AuthRepository

    credential_ref = AuthRepository(session).status(
        project_id=project_id,
        provider_key="dataforseo",
    ).connections[0].credential_ref
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


def test_dataforseo_paa_action_uses_explicit_action_contract(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="dataforseo",
        plaintext_payload=b"password",
        config_json={"login": "login@example.com"},
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="dataforseo",
        monthly_budget_usd=10.0,
    )
    from content_stack.auth_providers import AuthRepository

    credential_ref = AuthRepository(session).status(
        project_id=project_id,
        provider_key="dataforseo",
    ).connections[0].credential_ref
    httpx_mock.add_response(
        method="POST",
        url="https://api.dataforseo.com/v3/serp/google/organic/live/advanced",
        json={
            "tasks": [
                {
                    "cost": 0.001,
                    "result": [
                        {
                            "items": [
                                {"type": "people_also_ask", "title": "What is SEO?"}
                            ]
                        }
                    ],
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
    assert (
        out.output_json["tasks"][0]["result"][0]["items"][0]["title"]
        == "What is SEO?"
    )
    assert "password" not in rendered
    assert "login@example.com" not in rendered


def test_wordpress_post_create_action_uses_daemon_side_site_config(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="wordpress",
        plaintext_payload=json.dumps(
            {"username": "editor", "application_password": "app pass"}
        ).encode("utf-8"),
        config_json={"wp_url": "https://wp.example"},
    )
    from content_stack.auth_providers import AuthRepository

    credential_ref = AuthRepository(session).status(
        project_id=project_id,
        provider_key="wordpress",
    ).connections[0].credential_ref
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
        plaintext_payload=b"keyid:00112233445566778899aabbccddeeff",
        config_json={"ghost_url": "https://ghost.example", "api_version": "v5.0"},
    )
    from content_stack.auth_providers import AuthRepository

    credential_ref = AuthRepository(session).status(
        project_id=project_id,
        provider_key="ghost",
    ).connections[0].credential_ref
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
    from content_stack.repositories.projects import ProjectRepository

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
    from content_stack.repositories.projects import ProjectRepository

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
