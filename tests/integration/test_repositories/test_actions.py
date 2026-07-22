"""Repository tests for the StackOS internal action executor."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import timedelta
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
from stackos.auth_providers import AuthRepository
from stackos.auth_providers.repository.utils import utcnow
from stackos.db.models import (
    Action,
    ActionCall,
    Credential,
    CredentialScope,
    CredentialUsageEvent,
    IntegrationCredential,
    Plugin,
    PluginSource,
    Provider,
)
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
    credential_ref = status.connections[0].credential_ref
    provider_scopes = {
        "google-ads": ["https://www.googleapis.com/auth/adwords"],
        "google-workspace": [
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/calendar.events",
        ],
        "google-search-console": ["https://www.googleapis.com/auth/webmasters.readonly"],
        "google-analytics": ["https://www.googleapis.com/auth/analytics.readonly"],
        "google-tag-manager": ["https://www.googleapis.com/auth/tagmanager.readonly"],
        "meta-ads": ["ads_management", "ads_read", "business_management"],
        "salesforce": ["api", "refresh_token"],
        "pipedrive": ["deals:read", "search:read"],
        "outreach": ["sequenceStates.write"],
        "salesloft": ["cadences:write"],
        "microsoft-365": [
            "https://graph.microsoft.com/Mail.Send",
            "https://graph.microsoft.com/Calendars.ReadWrite",
            "offline_access",
        ],
    }
    if provider_key in provider_scopes:
        credential = session.exec(
            select(Credential).where(Credential.credential_ref == credential_ref)
        ).one()
        credential.config_json = {**(credential.config_json or {}), "scope_status": "known"}
        session.add(credential)
        assert credential.id is not None
        existing = {
            row.scope
            for row in session.exec(
                select(CredentialScope).where(CredentialScope.credential_id == credential.id)
            ).all()
        }
        for scope in provider_scopes[provider_key]:
            if scope not in existing:
                session.add(CredentialScope(credential_id=credential.id, scope=scope))
        session.commit()
    return credential_ref


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


def test_action_required_scopes_are_enforced_before_connector_execution(
    session: Session,
    project_id: int,
) -> None:
    _seed_action(session)
    action = session.exec(select(Action).where(Action.key == "echo.run")).one()
    action.config_json = {
        **action.config_json,
        "required_scopes": ["example.read"],
    }
    session.add(action)
    session.commit()
    credential_ref = _credential_ref(session, project_id)
    fake = _FakeConnector()
    registry = ActionConnectorRegistry()
    registry.register(fake)
    repo = ActionRepository(session, connectors=registry)

    assert repo.describe(action_ref="test-actions.echo.run").manifest.required_scopes == [
        "example.read"
    ]
    with pytest.raises(ConflictError, match="credential scopes are unknown"):
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="test-actions.echo.run",
                input_json={"name": "Ada"},
                credential_ref=credential_ref,
            )
        )

    assert fake.calls == 0


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


def test_action_execute_file_backs_context_output_as_plain_file(
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
    assert pointer["path"].startswith(str(tmp_path))
    assert pointer["semantic_name"].startswith("analysis-output-")
    assert pointer["bytes"] > 0
    assert pointer["schema_version"] == "stackos.action-output.v1"
    assert pointer["schema_ref"] == "stackos.action-output.v1"
    assert pointer["schema_operation"] == "schema.get"
    assert "artifact_id" not in pointer
    assert "context_artifact_id" not in pointer
    assert "read" not in pointer
    saved = json.loads(Path(pointer["path"]).read_text(encoding="utf-8"))
    assert saved["schema_version"] == "stackos.action-output.v1"
    assert saved["request"]["input_json"] == {"name": "Ada"}
    assert saved["request"]["context_ref"] == "ctx_provider_analysis"
    assert saved["response"]["output_json"] == {
        "echo": {"name": "Ada"},
        "authorization": "[redacted]",
        "nested": {"api_key": "[redacted]"},
    }
    context_artifacts = ExecutionContextRepository(session).list_artifacts(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        action_ref="test-actions.echo.run",
    )
    assert context_artifacts.total_estimate == 0
    assert out.action_call.response_json == out.output_json
    assert out.action_call.metadata_json["file_backed_output"]["path"] == pointer["path"]


def test_action_execute_rejects_explicit_file_path_for_file_backed_output(
    session: Session,
    project_id: int,
    tmp_path: Path,
) -> None:
    _seed_action(session)
    credential_ref = _credential_ref(session, project_id)
    fake = _FakeConnector()
    registry = ActionConnectorRegistry()
    registry.register(fake)
    repo = ActionRepository(session, connectors=registry, asset_dir=tmp_path)
    requested_path = tmp_path / "requested" / "echo-response.json"

    with pytest.raises(ValidationError, match="accepts only path"):
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="test-actions.echo.run",
                input_json={"name": "Ada"},
                credential_ref=credential_ref,
                output_policy_json={"mode": "always_file", "file_path": str(requested_path)},
                default_external_file_output=True,
            )
        )


def test_action_execute_file_backs_external_output_under_requested_directory_path(
    session: Session,
    project_id: int,
    tmp_path: Path,
) -> None:
    _seed_action(session)
    credential_ref = _credential_ref(session, project_id)
    fake = _FakeConnector()
    registry = ActionConnectorRegistry()
    registry.register(fake)
    repo = ActionRepository(session, connectors=registry, asset_dir=tmp_path)
    requested_dir = tmp_path / "requested-dir"

    out = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="test-actions.echo.run",
            input_json={"name": "Ada"},
            credential_ref=credential_ref,
            output_policy_json={"mode": "always_file", "path": str(requested_dir)},
            default_external_file_output=True,
        )
    ).data

    pointer = out.output_json["file"]
    path = Path(pointer["path"])
    assert path.parent == requested_dir
    assert path.name.startswith("fake-provider-echo.run-")
    assert path.name.endswith(f"-{out.action_call.id}.json")
    assert pointer["schema_version"] == "stackos.action-output.v1"
    assert pointer["schema_ref"] == "stackos.action-output.v1"
    assert pointer["schema_operation"] == "schema.get"
    assert "artifact_id" not in pointer
    assert "context_artifact_id" not in pointer
    assert "read" not in pointer
    assert path.is_file()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["request"]["input_json"] == {"name": "Ada"}
    assert saved["request"]["credential_ref"] == credential_ref
    assert saved["response"]["output_json"] == {
        "echo": {"name": "Ada"},
        "authorization": "[redacted]",
        "nested": {"api_key": "[redacted]"},
    }
    assert "leaked-token" not in json.dumps(saved)
    call = session.exec(select(ActionCall).where(ActionCall.id == out.action_call.id)).one()
    assert call.response_json == out.output_json


def test_action_execute_rejects_directory_path_aliases_for_file_backed_output(
    session: Session,
    project_id: int,
    tmp_path: Path,
) -> None:
    _seed_action(session)
    credential_ref = _credential_ref(session, project_id)
    fake = _FakeConnector()
    registry = ActionConnectorRegistry()
    registry.register(fake)
    repo = ActionRepository(session, connectors=registry, asset_dir=tmp_path)

    for alias in ("directory_path", "output_dir"):
        with pytest.raises(ValidationError, match="accepts only path"):
            asyncio.run(
                repo.execute(
                    project_id=project_id,
                    action_ref="test-actions.echo.run",
                    input_json={"name": "Ada"},
                    credential_ref=credential_ref,
                    output_policy_json={"mode": "always_file", alias: str(tmp_path / alias)},
                    default_external_file_output=True,
                )
            )


def test_action_execute_rejects_relative_directory_path_for_file_backed_output(
    session: Session,
    project_id: int,
) -> None:
    _seed_action(session)
    credential_ref = _credential_ref(session, project_id)
    fake = _FakeConnector()
    registry = ActionConnectorRegistry()
    registry.register(fake)
    repo = ActionRepository(session, connectors=registry)

    with pytest.raises(ValidationError, match="absolute directory path"):
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="test-actions.echo.run",
                input_json={"name": "Ada"},
                credential_ref=credential_ref,
                output_policy_json={"mode": "always_file", "path": "tmp/action-outputs"},
                default_external_file_output=True,
            )
        )


def test_action_execute_keeps_internal_no_provider_output_inline_by_default(
    session: Session,
    project_id: int,
) -> None:
    _seed_action(session)
    _seed_noauth_action(session)
    fake = _NoAuthConnector()
    registry = ActionConnectorRegistry()
    registry.register(fake)
    repo = ActionRepository(session, connectors=registry)

    out = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="test-actions.noauth.run",
            input_json={"name": "Ada"},
        )
    ).data

    assert out.output_json == {"ok": True}
    assert "output_mode" not in out.output_json


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
        "seo.search-console.sites.list": ("google-search-console", True, "unknown"),
        "seo.search-console.search-analytics.query": (
            "google-search-console",
            True,
            "unknown",
        ),
        "seo.search-console.sitemaps.list": ("google-search-console", True, "unknown"),
        "seo.search-console.url.inspect": ("google-search-console", True, "unknown"),
        "seo.ga4.account_summaries.list": ("google-analytics", True, "unknown"),
        "seo.ga4.properties.metadata.get": ("google-analytics", True, "unknown"),
        "seo.ga4.properties.run_report": ("google-analytics", True, "unknown"),
        "seo.ga4.properties.run_realtime_report": ("google-analytics", True, "unknown"),
        "seo.google-tag-manager.accounts.list": ("google-tag-manager", True, "unknown"),
        "seo.google-tag-manager.containers.list": ("google-tag-manager", True, "unknown"),
        "seo.google-tag-manager.container.snippet.get": (
            "google-tag-manager",
            True,
            "unknown",
        ),
        "seo.google-tag-manager.workspaces.list": ("google-tag-manager", True, "unknown"),
        "seo.google-tag-manager.workspace.tags.list": (
            "google-tag-manager",
            True,
            "unknown",
        ),
        "seo.google-tag-manager.workspace.triggers.list": (
            "google-tag-manager",
            True,
            "unknown",
        ),
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
    credential_ref = _provider_credential_ref(session, project_id, "meta-ads")
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
    credential_ref = _provider_credential_ref(session, project_id, "google-ads")
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
    stored = (
        IntegrationCredentialRepository(session)
        .set(
            project_id=project_id,
            kind="taboola",
            secret_payload=json.dumps(
                {"client_id": "taboola-client", "client_secret": "taboola-secret"}
            ).encode("utf-8"),
            config_json={"accounts": {"main": "demo-account"}},
        )
        .data
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
        url="https://backstage.taboola.com/backstage/oauth/token",
        json={"access_token": "taboola-access", "expires_in": 3600},
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

    token_request, request = httpx_mock.get_requests()
    rendered = json.dumps(out.model_dump(mode="json"))
    token_form = parse_qs(token_request.content.decode("utf-8"))
    assert token_form["grant_type"] == ["client_credentials"]
    assert token_form["client_id"] == ["taboola-client"]
    assert request.headers["authorization"] == "Bearer taboola-access"
    assert json.loads(request.content.decode("utf-8"))["marketing_objective"] == (
        "DRIVE_WEBSITE_TRAFFIC"
    )
    assert out.action_call.connector_key == "taboola"
    assert out.output_json["body"]["id"] == "campaign-1"
    assert "taboola-access" not in rendered
    payload = json.loads(
        IntegrationCredentialRepository(session).get_decrypted(stored.id).decode("utf-8")
    )
    assert payload["access_token"] == "taboola-access"
    row = session.get(IntegrationCredential, stored.id)
    assert row is not None and row.expires_at is not None


def test_reddit_action_acquires_once_in_core_then_uses_bearer_token(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    stored = (
        IntegrationCredentialRepository(session)
        .set(
            project_id=project_id,
            kind="reddit",
            secret_payload=json.dumps(
                {
                    "client_id": "reddit-client",
                    "client_secret": "reddit-secret",
                    "user_agent": "stackos-test/1.0",
                }
            ).encode("utf-8"),
        )
        .data
    )
    credential_ref = _provider_credential_ref(session, project_id, "reddit")
    httpx_mock.add_response(
        method="POST",
        url="https://www.reddit.com/api/v1/access_token",
        json={"access_token": "reddit-access", "expires_in": 3600},
    )
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://oauth.reddit.com/r/python/search?q=oauth&restrict_sr=true"
            "&sort=relevance&limit=25"
        ),
        json={"data": {"children": [{"data": {"title": "OAuth?"}}]}},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="utils.reddit.search-subreddit",
            input_json={"subreddit": "python", "query": "oauth"},
            credential_ref=credential_ref,
        )
    ).data

    token_request, api_request = httpx_mock.get_requests()
    assert token_request.headers["User-Agent"] == "stackos-test/1.0"
    assert api_request.headers["Authorization"] == "Bearer reddit-access"
    assert out.output_json["data"]["children"][0]["data"]["title"] == "OAuth?"
    payload = json.loads(
        IntegrationCredentialRepository(session).get_decrypted(stored.id).decode("utf-8")
    )
    assert payload["access_token"] == "reddit-access"


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
    assert request.headers["x-api-key"] == "apollo-secret"
    assert "authorization" not in request.headers
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
    from stackos.auth_providers import AuthRepository

    credential_ref = (
        AuthRepository(session)
        .store_credential(
            project_id=project_id,
            provider_key="clay",
            auth_method_key="webhook",
            fields={
                "webhook_url": "https://hooks.clay.com/t/leads",
                "webhook_token": "clay-secret",
            },
        )
        .data.credential_ref
    )
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
    assert request.headers["authorization"] == "Bearer clay-secret"
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
            input_json={
                "keyword": "stackos",
                "depth": 10,
                "location_name": "United States",
                "language_code": "en",
            },
            credential_ref=credential_ref,
        )
    ).data

    request_body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    rendered = json.dumps(out.model_dump(mode="json"))
    assert request_body[0]["location_name"] == "United States"
    assert request_body[0]["language_code"] == "en"
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
    valid_locale_validation = repo.validate(
        project_id=project_id,
        action_ref="seo.keyword.research",
        input_json={
            "keywords": ["seo automation"],
            "location_name": "United States",
            "language_code": "en",
        },
        credential_ref=credential_ref,
    )
    invalid_language_validation = repo.validate(
        project_id=project_id,
        action_ref="seo.keyword.research",
        input_json={
            "keywords": ["seo automation"],
            "location_name": "United States",
            "language_code": "English",
        },
        credential_ref=credential_ref,
    )
    legacy_locale_validation = repo.validate(
        project_id=project_id,
        action_ref="seo.keyword.research",
        input_json={
            "keywords": ["seo automation"],
            "location": "United States",
            "language": "en",
        },
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
    assert valid_locale_validation.valid is True
    assert invalid_language_validation.valid is False
    assert any(
        issue.path == "$.language_code" and issue.code == "enum_mismatch"
        for issue in invalid_language_validation.issues
    )
    assert legacy_locale_validation.valid is False
    assert any(
        issue.path in {"$.location", "$.language"} and issue.code == "additional_property"
        for issue in legacy_locale_validation.issues
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


def test_google_search_console_search_analytics_action_maps_google_contract(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    stored = (
        AuthRepository(session)
        .store_credential(
            project_id=project_id,
            provider_key="google-search-console",
            auth_method_key="oauth2_refresh_token",
            profile_key="manual-refresh-action",
            fields={
                "client_id": "gsc-client-id",
                "client_secret": "gsc-client-secret",
                "refresh_token": "gsc-refresh-token",
                "default_site_url": "https://example.com/",
            },
            expires_at=utcnow() - timedelta(minutes=5),
        )
        .data
    )
    httpx_mock.add_response(
        method="POST",
        url="https://oauth2.googleapis.com/token",
        json={
            "access_token": "gsc-token",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/webmasters.readonly",
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=(
            "https://www.googleapis.com/webmasters/v3/sites/"
            "https%3A%2F%2Fexample.com%2F/searchAnalytics/query"
        ),
        json={"rows": [{"keys": ["query"], "clicks": 3, "impressions": 9}]},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="seo.search-console.search-analytics.query",
            input_json={
                "site_url": "https://example.com/",
                "start_date": "2026-06-01",
                "end_date": "2026-06-07",
                "dimensions": ["query"],
                "row_limit": 1,
                "start_row": 10,
                "data_state": "final",
            },
            credential_ref=stored.credential_ref,
        )
    ).data

    token_request, request = httpx_mock.get_requests()
    token_form = parse_qs(token_request.content.decode())
    request_body = json.loads(request.content.decode("utf-8"))
    rendered = json.dumps(out.model_dump(mode="json"))
    assert token_form["grant_type"] == ["refresh_token"]
    assert token_form["refresh_token"] == ["gsc-refresh-token"]
    assert request.headers["Authorization"] == "Bearer gsc-token"
    assert request_body == {
        "startDate": "2026-06-01",
        "endDate": "2026-06-07",
        "dimensions": ["query"],
        "rowLimit": 1,
        "startRow": 10,
        "dataState": "final",
    }
    assert out.output_json["next_start_row"] == 11
    assert out.action_call.provider_key == "google-search-console"
    assert out.action_call.connector_key == "google-search-console"
    assert "gsc-token" not in rendered


def test_google_search_console_provider_error_is_preserved_for_agent_repair(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="google-search-console",
        secret_payload=json.dumps({"access_token": "gsc-token"}).encode("utf-8"),
    )
    credential_ref = _provider_credential_ref(session, project_id, "google-search-console")
    httpx_mock.add_response(
        method="GET",
        url="https://www.googleapis.com/webmasters/v3/sites",
        status_code=403,
        json={
            "error": {
                "code": 403,
                "message": "Request had insufficient authentication scopes.",
                "status": "PERMISSION_DENIED",
            }
        },
    )

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="seo.search-console.sites.list",
                input_json={},
                credential_ref=credential_ref,
            )
        )

    data = excinfo.value.data
    assert data["status"] == "failed"
    assert data["action_ref"] == "seo.search-console.sites.list"
    assert data["provider_status_code"] == 403
    assert data["provider_error"]["error"]["status"] == "PERMISSION_DENIED"
    call = session.exec(select(ActionCall).where(ActionCall.id == data["action_call_id"])).one()
    assert call.status.value == "failed"
    assert call.response_json == {
        "status": "failed",
        "provider_status_code": 403,
        "provider_error": data["provider_error"],
    }


def test_google_search_console_refresh_failure_stops_before_connector_execution(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="google-search-console",
        secret_payload=json.dumps(
            {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "refresh_token": "refresh-secret",
            }
        ).encode("utf-8"),
    )
    credential_ref = _provider_credential_ref(session, project_id, "google-search-console")
    httpx_mock.add_response(
        method="POST",
        url="https://oauth2.googleapis.com/token",
        status_code=400,
        json={
            "error": "invalid_grant",
            "error_description": "Token has been expired or revoked.",
        },
    )

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="seo.search-console.sites.list",
                input_json={},
                credential_ref=credential_ref,
            )
        )

    data = excinfo.value.data
    assert data["status"] == "repair-required"
    assert data["next_action"] == "Reconnect this provider credential."
    rendered = json.dumps(data)
    assert "client-secret" not in rendered
    assert "refresh-secret" not in rendered
    assert len(httpx_mock.get_requests()) == 1
    assert session.exec(select(ActionCall)).all() == []


def test_google_search_console_validation_rejects_malformed_google_requests(
    session: Session,
    project_id: int,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="google-search-console",
        secret_payload=json.dumps({"access_token": "gsc-token"}).encode("utf-8"),
    )
    credential_ref = _provider_credential_ref(session, project_id, "google-search-console")
    repo = ActionRepository(session)

    search_validation = repo.validate(
        project_id=project_id,
        action_ref="seo.search-console.search-analytics.query",
        input_json={
            "site_url": "sc-domain:   ",
            "start_date": "2026-06-10",
            "end_date": "2026-06-01",
            "dimensions": ["query", "query", "bad"],
            "dimension_filter_groups": [
                {
                    "groupType": "or",
                    "filters": [
                        {
                            "dimension": "date",
                            "operator": "startsWith",
                            "expression": "",
                        },
                        {
                            "dimension": "query",
                            "operator": "contains",
                            "expression": "x" * 4097,
                        },
                    ],
                }
            ],
        },
        credential_ref=credential_ref,
    )

    sitemap_validation = repo.validate(
        project_id=project_id,
        action_ref="seo.search-console.sitemaps.list",
        input_json={
            "site_url": "sc-domain:   ",
            "sitemap_index": "not-a-url",
        },
        credential_ref=credential_ref,
    )

    search_paths = {issue.path for issue in search_validation.issues}
    assert search_validation.valid is False
    assert "$.site_url" in search_paths
    assert "$.end_date" in search_paths
    assert "$.dimensions[1]" in search_paths
    assert "$.dimensions[2]" in search_paths
    assert "$.dimension_filter_groups[0].groupType" in search_paths
    assert "$.dimension_filter_groups[0].filters[0].dimension" in search_paths
    assert "$.dimension_filter_groups[0].filters[0].operator" in search_paths
    assert "$.dimension_filter_groups[0].filters[0].expression" in search_paths
    assert "$.dimension_filter_groups[0].filters[1].expression" in search_paths
    sitemap_paths = {issue.path for issue in sitemap_validation.issues}
    assert sitemap_validation.valid is False
    assert "$.site_url" in sitemap_paths
    assert "$.sitemap_index" in sitemap_paths


def test_google_search_console_validation_keeps_legacy_gsc_surfaces_out(
    session: Session,
    project_id: int,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="google-search-console",
        secret_payload=json.dumps({"access_token": "gsc-token"}).encode("utf-8"),
    )
    credential_ref = _provider_credential_ref(session, project_id, "google-search-console")
    repo = ActionRepository(session)

    validation = repo.validate(
        project_id=project_id,
        action_ref="seo.search-console.url.inspect",
        input_json={
            "site_url": "https://example.com/",
            "inspection_url": "https://other.example/page",
        },
        credential_ref=credential_ref,
    )

    assert validation.valid is False
    assert any(issue.path == "$.inspection_url" for issue in validation.issues)
    with pytest.raises(NotFoundError):
        repo.describe(action_ref="seo.gscOauth.start")


def test_google_analytics_run_report_action_maps_property_ref_and_body(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="google-analytics",
        secret_payload=json.dumps({"access_token": "ga-token"}).encode("utf-8"),
        config_json={"property_refs": {"main": "1234"}},
    )
    credential_ref = _provider_credential_ref(session, project_id, "google-analytics")
    httpx_mock.add_response(
        method="POST",
        url="https://analyticsdata.googleapis.com/v1beta/properties/1234:runReport",
        json={
            "dimensionHeaders": [{"name": "pagePath"}],
            "metricHeaders": [{"name": "sessions"}],
            "rows": [{"dimensionValues": [{"value": "/"}], "metricValues": [{"value": "7"}]}],
            "propertyQuota": {"tokensPerDay": {"remaining": 990}},
        },
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="seo.ga4.properties.run_report",
            input_json={
                "property_ref": "main",
                "request": {
                    "dateRanges": [{"startDate": "7daysAgo", "endDate": "yesterday"}],
                    "dimensions": [{"name": "pagePath"}],
                    "metrics": [{"name": "sessions"}],
                    "limit": "100",
                    "returnPropertyQuota": True,
                },
            },
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_requests()[0]
    request_body = json.loads(request.content.decode("utf-8"))
    rendered = json.dumps(out.model_dump(mode="json"))
    assert request.headers["Authorization"] == "Bearer ga-token"
    assert request_body["dateRanges"] == [{"startDate": "7daysAgo", "endDate": "yesterday"}]
    assert request_body["metrics"] == [{"name": "sessions"}]
    assert "property" not in request_body
    assert "propertyQuota" not in out.output_json
    assert out.output_json["quota"]["units_per_day"]["remaining"] == 990
    assert out.action_call.connector_key == "google-analytics"
    assert "ga-token" not in rendered


def test_google_analytics_account_summaries_action_maps_cursor_without_secret_key(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="google-analytics",
        secret_payload=json.dumps({"access_token": "ga-token"}).encode("utf-8"),
    )
    credential_ref = _provider_credential_ref(session, project_id, "google-analytics")
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://analyticsadmin.googleapis.com/v1beta/accountSummaries?"
            "pageSize=2&pageToken=cursor-1"
        ),
        json={
            "accountSummaries": [{"name": "accountSummaries/1"}],
            "nextPageToken": "cursor-2",
        },
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="seo.ga4.account_summaries.list",
            input_json={"page_size": 2, "page_cursor": "cursor-1"},
            credential_ref=credential_ref,
        )
    ).data

    rendered = json.dumps(out.model_dump(mode="json"))
    assert out.output_json["accountSummaries"][0]["name"] == "accountSummaries/1"
    assert out.output_json["next_page_cursor"] == "cursor-2"
    assert "nextPageToken" not in out.output_json
    assert "ga-token" not in rendered


def test_google_analytics_report_validation_rejects_nested_property_and_bad_limit(
    session: Session,
    project_id: int,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="google-analytics",
        secret_payload=json.dumps({"access_token": "ga-token"}).encode("utf-8"),
    )
    credential_ref = _provider_credential_ref(session, project_id, "google-analytics")

    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="seo.ga4.properties.run_report",
        input_json={
            "property_ref": "1234",
            "request": {
                "property": "properties/9999",
                "dateRanges": [{"startDate": "2026-06-01", "endDate": "2026-06-07"}],
                "metrics": [{"name": "sessions"}],
                "limit": "250001",
            },
        },
        credential_ref=credential_ref,
    )

    assert validation.valid is False
    assert any(issue.path == "$.request.property" for issue in validation.issues)
    assert any(
        issue.path == "$.request.limit" and issue.code == "range" for issue in validation.issues
    )


def test_google_tag_manager_workspace_tags_action_resolves_safe_refs(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="google-tag-manager",
        secret_payload=json.dumps({"access_token": "gtm-token"}).encode("utf-8"),
        config_json={
            "account_refs": {"main": "111"},
            "container_refs": {"web": "GTM-ABC"},
            "workspace_refs": {"live": "7"},
        },
    )
    credential_ref = _provider_credential_ref(session, project_id, "google-tag-manager")
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://tagmanager.googleapis.com/tagmanager/v2/"
            "accounts/111/containers/GTM-ABC/workspaces/7/tags?pageToken=next"
        ),
        json={"tag": [{"name": "GA4 config"}], "nextPageToken": "after"},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="seo.google-tag-manager.workspace.tags.list",
            input_json={
                "account_ref": "main",
                "container_ref": "web",
                "workspace_ref": "live",
                "page_cursor": "next",
            },
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(out.model_dump(mode="json"))
    assert request.headers["Authorization"] == "Bearer gtm-token"
    assert out.output_json["tag"][0]["name"] == "GA4 config"
    assert out.output_json["next_page_cursor"] == "after"
    assert "nextPageToken" not in out.output_json
    assert out.action_call.connector_key == "google-tag-manager"
    assert "gtm-token" not in rendered


def test_google_tag_manager_validation_bounds_refs_and_page_cursors(
    session: Session,
    project_id: int,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="google-tag-manager",
        secret_payload=json.dumps({"access_token": "gtm-token"}).encode("utf-8"),
    )
    credential_ref = _provider_credential_ref(session, project_id, "google-tag-manager")

    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="seo.google-tag-manager.workspace.triggers.list",
        input_json={
            "account_ref": "111",
            "container_ref": "GTM-ABC",
            "workspace_ref": "7",
            "page_cursor": 123,
        },
        credential_ref=credential_ref,
    )

    assert validation.valid is False
    assert any(issue.path == "$.page_cursor" for issue in validation.issues)


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
