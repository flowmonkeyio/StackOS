"""Repository tests for generic provider action execution contexts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.config import Settings
from stackos.db.models import (
    Action,
    ActionCall,
    ActionCallStatus,
    Artifact,
    ExecutionContext,
    Plugin,
    PluginSource,
    Project,
    Provider,
)
from stackos.mcp.context import MCPContext
from stackos.operations.execution_contexts import (
    ExecutionContextArtifactReadInput,
    execution_context_artifact_read,
)
from stackos.repositories.base import ValidationError
from stackos.repositories.execution_contexts import ExecutionContextRepository
from stackos.repositories.projects import IntegrationCredentialRepository


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


def _seed_action(session: Session) -> str:
    plugin = Plugin(
        slug="example-plugin",
        name="Example Plugin",
        version="0.1.0",
        description="Generic test plugin.",
        source=PluginSource.PROJECT,
        manifest_json={},
    )
    session.add(plugin)
    session.flush()
    assert plugin.id is not None
    provider = Provider(
        plugin_id=plugin.id,
        key="example-provider",
        name="Example Provider",
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
            key="analytics.query",
            name="Query Analytics",
            description="Generic read action.",
            risk_level="read",
            input_schema_json={
                "type": "object",
                "additionalProperties": False,
                "properties": {"date_range": {"type": "string"}},
            },
            output_schema_json={"type": "object", "additionalProperties": True},
            config_json={
                "schema_version": "stackos.action.v1",
                "connector": "example-provider",
                "operation": "analytics.query",
                "requires_credential": True,
                "provider_context_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["account_ref"],
                    "properties": {
                        "account_ref": {"type": "string"},
                        "surface": {"type": "string", "enum": ["default", "reporting"]},
                    },
                },
            },
        )
    )
    session.commit()
    return "example-plugin.analytics.query"


def _seed_no_context_action(session: Session) -> str:
    plugin = session.exec(select(Plugin).where(Plugin.slug == "example-plugin")).first()
    if plugin is None:
        _seed_action(session)
        plugin = session.exec(select(Plugin).where(Plugin.slug == "example-plugin")).one()
    assert plugin is not None
    provider = session.exec(
        select(Provider).where(Provider.plugin_id == plugin.id, Provider.key == "example-provider")
    ).one()
    session.add(
        Action(
            plugin_id=plugin.id,
            provider_id=provider.id,
            key="simple.read",
            name="Simple Read",
            description="Action that does not allow provider context.",
            risk_level="read",
            input_schema_json={"type": "object", "additionalProperties": True},
            output_schema_json={"type": "object", "additionalProperties": True},
            config_json={
                "schema_version": "stackos.action.v1",
                "connector": "example-provider",
                "operation": "simple.read",
                "requires_credential": True,
            },
        )
    )
    session.commit()
    return "example-plugin.simple.read"


def _seed_region_context_action(session: Session) -> str:
    plugin = session.exec(select(Plugin).where(Plugin.slug == "example-plugin")).first()
    if plugin is None:
        _seed_action(session)
        plugin = session.exec(select(Plugin).where(Plugin.slug == "example-plugin")).one()
    assert plugin is not None
    provider = session.exec(
        select(Provider).where(Provider.plugin_id == plugin.id, Provider.key == "example-provider")
    ).one()
    session.add(
        Action(
            plugin_id=plugin.id,
            provider_id=provider.id,
            key="analytics.region.query",
            name="Query Regional Analytics",
            description="Generic regional read action.",
            risk_level="read",
            input_schema_json={"type": "object", "additionalProperties": True},
            output_schema_json={"type": "object", "additionalProperties": True},
            config_json={
                "schema_version": "stackos.action.v1",
                "connector": "example-provider",
                "operation": "analytics.region.query",
                "requires_credential": True,
                "provider_context_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["account_ref", "region"],
                    "properties": {
                        "account_ref": {"type": "string"},
                        "region": {"type": "string", "enum": ["na", "eu"]},
                    },
                },
            },
        )
    )
    session.commit()
    return "example-plugin.analytics.region.query"


def _credential_ref(session: Session, project_id: int, provider_key: str) -> str:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind=provider_key,
        secret_payload=b"daemon-only-secret",
        config_json={"label": "Example credential"},
    )
    status = AuthRepository(session).status(project_id=project_id, provider_key=provider_key)
    return status.connections[0].credential_ref


def test_execution_context_create_resolve_and_list_by_task(
    session: Session,
    project_id: int,
) -> None:
    action_ref = _seed_action(session)
    credential_ref = _credential_ref(session, project_id, "example-provider")
    repo = ExecutionContextRepository(session)

    created = repo.create(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        name="Provider analysis context",
        action_ref=action_ref,
        credential_ref=credential_ref,
        provider_context_json={"account_ref": "acct_123", "surface": "reporting"},
        output_policy_json={"mode": "file_if_large", "max_inline_bytes": 16000},
        request_budget_json={"max_parallel": 3},
        artifact_namespace="provider-analysis",
        links_json=[
            {"link_type": "task", "link_ref": "workflow-123"},
            {"link_type": "run_plan", "link_ref": "123"},
        ],
        created_by="test-agent",
    ).data

    assert created.plugin_slug == "example-plugin"
    assert created.provider_key == "example-provider"
    assert created.action_ref == action_ref
    assert created.credential_ref == credential_ref
    assert created.provider_context_json == {"account_ref": "acct_123", "surface": "reporting"}
    assert {link.link_type for link in created.links} == {"task", "run_plan"}

    by_task = repo.list(project_id=project_id, task_key="workflow-123")
    assert by_task.total_estimate == 1
    assert by_task.items[0].context_ref == "ctx_provider_analysis"

    resolved = repo.resolve(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        action_ref=action_ref,
    )
    assert resolved.compatible is True
    assert resolved.issues == []
    assert resolved.provider_context_schema_source == action_ref
    assert resolved.provider_context_schema_json["required"] == ["account_ref"]
    assert resolved.next_call == {
        "context_ref": "ctx_provider_analysis",
        "action_ref": action_ref,
    }


def test_execution_context_resolve_uses_selected_action_provider_context_schema(
    session: Session,
    project_id: int,
) -> None:
    default_action_ref = _seed_action(session)
    regional_action_ref = _seed_region_context_action(session)
    credential_ref = _credential_ref(session, project_id, "example-provider")
    repo = ExecutionContextRepository(session)
    repo.create(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        name="Provider analysis context",
        action_ref=default_action_ref,
        credential_ref=credential_ref,
        provider_context_json={"account_ref": "acct_123", "surface": "reporting"},
    )

    resolved = repo.resolve(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        action_ref=regional_action_ref,
    )

    assert resolved.compatible is False
    assert resolved.provider_context_schema_source == regional_action_ref
    assert resolved.provider_context_schema_json["required"] == ["account_ref", "region"]
    assert any(issue["path"] == "$.provider_context_json.region" for issue in resolved.issues)
    assert any(issue["path"] == "$.provider_context_json.surface" for issue in resolved.issues)


def test_execution_context_refs_are_project_scoped(
    session: Session,
    project_id: int,
) -> None:
    other = Project(
        slug="other-project",
        name="Other Project",
        domain="other.example",
        locale="en-US",
        is_active=True,
    )
    session.add(other)
    session.commit()
    session.refresh(other)
    assert other.id is not None
    repo = ExecutionContextRepository(session)

    first = repo.create(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        name="Provider analysis context",
    ).data
    second = repo.create(
        project_id=other.id,
        context_ref="ctx_provider_analysis",
        name="Provider analysis context",
    ).data

    assert first.project_id == project_id
    assert second.project_id == other.id
    assert first.context_ref == second.context_ref
    with pytest.raises(ValidationError):
        repo.get(project_id=None, context_ref="ctx_provider_analysis")  # type: ignore[arg-type]


def test_execution_context_rejects_secrets_and_invalid_provider_context(
    session: Session,
    project_id: int,
) -> None:
    action_ref = _seed_action(session)
    credential_ref = _credential_ref(session, project_id, "example-provider")
    repo = ExecutionContextRepository(session)

    with pytest.raises(ValidationError) as secret_exc:
        repo.create(
            project_id=project_id,
            name="Secret context",
            action_ref=action_ref,
            credential_ref=credential_ref,
            provider_context_json={"account_ref": "acct_123", "api_key": "leak"},
        )
    assert "$.api_key" in secret_exc.value.data["paths"]

    with pytest.raises(ValidationError) as schema_exc:
        repo.create(
            project_id=project_id,
            name="Invalid context",
            action_ref=action_ref,
            credential_ref=credential_ref,
            provider_context_json={"account_ref": "acct_123", "surface": "unknown"},
        )
    assert schema_exc.value.data["issues"][0]["path"] == "$.provider_context_json.surface"

    with pytest.raises(ValidationError) as missing_schema_exc:
        repo.create(
            project_id=project_id,
            name="Unscoped context",
            provider_context_json={"account_ref": "acct_123"},
        )
    assert missing_schema_exc.value.data["issues"][0]["code"] == (
        "provider_context_schema_required"
    )

    no_context_action_ref = _seed_no_context_action(session)
    with pytest.raises(ValidationError) as not_allowed_exc:
        repo.create(
            project_id=project_id,
            name="Unsupported context",
            action_ref=no_context_action_ref,
            provider_context_json={"account_ref": "acct_123"},
        )
    assert not_allowed_exc.value.data["issues"][0]["code"] == "provider_context_not_allowed"


def test_execution_context_rejects_unsupported_locked_field_paths(
    session: Session,
    project_id: int,
) -> None:
    action_ref = _seed_action(session)
    repo = ExecutionContextRepository(session)

    with pytest.raises(ValidationError) as create_exc:
        repo.create(
            project_id=project_id,
            name="Nested lock context",
            action_ref=action_ref,
            provider_context_locked_fields_json=["$.account.id"],
        )
    assert create_exc.value.data["fields"] == ["$.account.id"]

    repo.create(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        name="Provider analysis context",
        action_ref=action_ref,
        provider_context_locked_fields_json=["account_ref"],
    )
    with pytest.raises(ValidationError) as update_exc:
        repo.update(
            project_id=project_id,
            context_ref="ctx_provider_analysis",
            patch_json={"provider_context_locked_fields_json": ["account[id]"]},
        )
    assert update_exc.value.data["fields"] == ["account[id]"]


def test_execution_context_rejects_invalid_output_policy(
    session: Session,
    project_id: int,
) -> None:
    repo = ExecutionContextRepository(session)

    with pytest.raises(ValidationError) as bad_mode:
        repo.create(
            project_id=project_id,
            context_ref="ctx_provider_analysis",
            name="Provider analysis context",
            output_policy_json={"mode": "file-large"},
        )
    assert bad_mode.value.data["accepted"] == ["always_file", "file_if_large", "inline"]

    with pytest.raises(ValidationError) as bad_field:
        repo.create(
            project_id=project_id,
            context_ref="ctx_provider_analysis",
            name="Provider analysis context",
            output_policy_json={"mode": "inline", "provider_specific": True},
        )
    assert bad_field.value.data["fields"] == ["provider_specific"]

    with pytest.raises(ValidationError) as bad_content_type:
        repo.create(
            project_id=project_id,
            context_ref="ctx_provider_analysis",
            name="Provider analysis context",
            output_policy_json={"mode": "always_file", "content_type": "text/plain"},
        )
    assert bad_content_type.value.data["content_type"] == "text/plain"


def test_execution_context_rejects_invalid_request_budget(
    session: Session,
    project_id: int,
) -> None:
    repo = ExecutionContextRepository(session)

    with pytest.raises(ValidationError) as unknown_field:
        repo.create(
            project_id=project_id,
            context_ref="ctx_provider_analysis",
            name="Provider analysis context",
            request_budget_json={"max_parallel": 3, "provider_specific": True},
        )
    assert unknown_field.value.data["fields"] == ["provider_specific"]

    with pytest.raises(ValidationError):
        repo.create(
            project_id=project_id,
            context_ref="ctx_provider_analysis",
            name="Provider analysis context",
            request_budget_json={"max_parallel": 0},
        )

    repo.create(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        name="Provider analysis context",
        request_budget_json={
            "max_parallel": 3,
            "max_calls": 10,
            "max_calls_per_run": 5,
            "window_seconds": 60,
            "notes": "Coordinate provider request bursts for this context.",
        },
    )
    with pytest.raises(ValidationError):
        repo.update(
            project_id=project_id,
            context_ref="ctx_provider_analysis",
            patch_json={"request_budget_json": {"window_seconds": False}},
        )


def test_execution_context_resolve_reports_stale_provider_context_without_schema(
    session: Session,
    project_id: int,
) -> None:
    stale = ExecutionContext(
        project_id=project_id,
        context_ref="ctx_stale_provider_context",
        name="Stale provider context",
        provider_context_json={"account_ref": "acct_123"},
    )
    session.add(stale)
    session.commit()
    repo = ExecutionContextRepository(session)

    resolved = repo.resolve(
        project_id=project_id,
        context_ref="ctx_stale_provider_context",
    )

    assert resolved.compatible is False
    assert resolved.issues[0]["code"] == "provider_context_schema_required"


def test_execution_context_resolve_reports_stale_action_as_compatibility_issue(
    session: Session,
    project_id: int,
) -> None:
    stale = ExecutionContext(
        project_id=project_id,
        context_ref="ctx_stale_action",
        name="Stale action context",
        action_ref="missing-plugin.missing-action",
    )
    session.add(stale)
    session.commit()

    resolved = ExecutionContextRepository(session).resolve(
        project_id=project_id,
        context_ref="ctx_stale_action",
    )

    assert resolved.compatible is False
    assert resolved.issues[0]["path"] == "$.action_ref"
    assert resolved.issues[0]["code"] == "action_not_found"


def test_execution_context_resolve_rejects_internal_generated_inventory_action_ref(
    session: Session,
    project_id: int,
) -> None:
    _seed_action(session)
    plugin = session.exec(select(Plugin).where(Plugin.slug == "example-plugin")).one()
    provider = session.exec(
        select(Provider).where(
            Provider.plugin_id == plugin.id,
            Provider.key == "example-provider",
        )
    ).one()
    internal_key = "api.ctx_pruned_scope.links_create"
    session.add(
        Action(
            plugin_id=plugin.id,
            provider_id=provider.id,
            key=internal_key,
            name="Removed generated action",
            description="Synthetic generated action row with an internal inventory scope key.",
            risk_level="read",
            input_schema_json={"type": "object", "additionalProperties": True},
            output_schema_json={"type": "object", "additionalProperties": True},
            config_json={
                "schema_version": "stackos.action.v1",
                "connector": "example-provider",
                "operation": "links.create",
                "requires_credential": True,
            },
        )
    )
    session.add(
        ExecutionContext(
            project_id=project_id,
            context_ref="ctx_removed_inventory_action",
            name="Removed generated action context",
            action_ref=f"example-plugin.{internal_key}",
        )
    )
    session.commit()

    resolved = ExecutionContextRepository(session).resolve(
        project_id=project_id,
        context_ref="ctx_removed_inventory_action",
    )

    assert resolved.compatible is False
    assert resolved.issues[0]["path"] == "$.action_ref"
    assert resolved.issues[0]["code"] == "action_not_found"


def test_execution_context_resolve_reports_stale_credential_as_compatibility_issue(
    session: Session,
    project_id: int,
) -> None:
    stale = ExecutionContext(
        project_id=project_id,
        context_ref="ctx_stale_credential",
        name="Stale credential context",
        credential_ref="cred_missing",
    )
    session.add(stale)
    session.commit()

    resolved = ExecutionContextRepository(session).resolve(
        project_id=project_id,
        context_ref="ctx_stale_credential",
    )

    assert resolved.compatible is False
    assert resolved.issues[0]["path"] == "$.credential_ref"
    assert resolved.issues[0]["code"] == "credential_invalid"


def test_execution_context_link_filters_are_narrowed_by_all_requested_links(
    session: Session,
    project_id: int,
) -> None:
    repo = ExecutionContextRepository(session)
    repo.create(
        project_id=project_id,
        context_ref="ctx_both",
        name="Both links",
        links_json=[
            {"link_type": "task", "link_ref": "workflow-123"},
            {"link_type": "run_plan", "link_ref": "123"},
        ],
    )
    repo.create(
        project_id=project_id,
        context_ref="ctx_task_only",
        name="Task only",
        links_json=[{"link_type": "task", "link_ref": "workflow-123"}],
    )
    repo.create(
        project_id=project_id,
        context_ref="ctx_plan_only",
        name="Run plan only",
        links_json=[{"link_type": "run_plan", "link_ref": "123"}],
    )

    page = repo.list(project_id=project_id, task_key="workflow-123", run_plan_id=123)

    assert page.total_estimate == 1
    assert [item.context_ref for item in page.items] == ["ctx_both"]


def test_execution_context_discover_returns_refs_filters_and_next_calls(
    session: Session,
    project_id: int,
) -> None:
    repo = ExecutionContextRepository(session)
    repo.create(
        project_id=project_id,
        context_ref="ctx_linked",
        name="Linked context",
        action_ref=_seed_action(session),
        links_json=[
            {"link_type": "task", "link_ref": "workflow-123"},
            {"link_type": "run", "link_ref": "456"},
        ],
    )
    repo.create(
        project_id=project_id,
        context_ref="ctx_other",
        name="Other context",
        links_json=[{"link_type": "task", "link_ref": "workflow-123"}],
    )

    discovered = repo.discover(project_id=project_id, task_key="workflow-123", run_id=456)

    assert discovered.filters_json == {
        "status": "active",
        "task_key": "workflow-123",
        "run_id": 456,
    }
    assert discovered.context_refs == ["ctx_linked"]
    assert discovered.total_estimate == 1
    assert discovered.next_calls["link"]["operation"] == "executionContext.link"
    assert discovered.next_calls["link"]["arguments"] == {
        "project_id": project_id,
        "context_ref": "ctx_existing",
        "task_key": "workflow-123",
    }
    assert discovered.next_calls["link_all_supplied_scopes"] == [
        {
            "operation": "executionContext.link",
            "arguments": {
                "project_id": project_id,
                "context_ref": "ctx_existing",
                "task_key": "workflow-123",
            },
        },
        {
            "operation": "executionContext.link",
            "arguments": {
                "project_id": project_id,
                "context_ref": "ctx_existing",
                "run_id": 456,
            },
        },
    ]
    assert discovered.next_calls["resolve"] == [
        {
            "operation": "executionContext.resolve",
            "arguments": {
                "project_id": project_id,
                "context_ref": "ctx_linked",
            },
        }
    ]
    assert discovered.next_calls["create"]["operation"] == "executionContext.create"
    assert discovered.next_calls["create"]["arguments"] == {
        "project_id": project_id,
        "name": "provider action context",
        "task_key": "workflow-123",
        "run_id": 456,
    }


def test_execution_context_discover_returns_create_next_call_when_empty(
    session: Session,
    project_id: int,
) -> None:
    repo = ExecutionContextRepository(session)

    discovered = repo.discover(
        project_id=project_id,
        plugin_slug="trackbooth",
        provider_key="trackbooth",
        action_ref="trackbooth.api.offers_findall",
        ticket_key="copy-offers",
        run_plan_id=42,
    )

    assert discovered.context_refs == []
    assert discovered.next_calls["resolve"] == []
    assert discovered.next_calls["create"] == {
        "operation": "executionContext.create",
        "arguments": {
            "project_id": project_id,
            "name": "trackbooth.api.offers_findall context",
            "plugin_slug": "trackbooth",
            "provider_key": "trackbooth",
            "action_ref": "trackbooth.api.offers_findall",
            "ticket_key": "copy-offers",
            "run_plan_id": 42,
        },
        "notes": [
            "add credential_ref only when selecting a stored credential reference",
            "add provider_context_json only for provider/account scope, never endpoint payload",
        ],
    }


def test_execution_context_pagination_total_ignores_cursor(
    session: Session,
    project_id: int,
) -> None:
    repo = ExecutionContextRepository(session)
    for index in range(3):
        repo.create(project_id=project_id, context_ref=f"ctx_{index}", name=f"Context {index}")

    first = repo.list(project_id=project_id, limit=1)
    second = repo.list(project_id=project_id, limit=1, after_id=first.next_cursor)

    assert first.total_estimate == 3
    assert second.total_estimate == 3
    assert first.items[0].context_ref == "ctx_0"
    assert second.items[0].context_ref == "ctx_1"


def test_execution_context_artifacts_are_registered_and_filtered(
    session: Session,
    project_id: int,
) -> None:
    action_ref = _seed_action(session)
    repo = ExecutionContextRepository(session)
    repo.create(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        name="Provider analysis context",
        action_ref=action_ref,
    )
    artifact = Artifact(
        project_id=project_id,
        kind="action-output",
        uri="/tmp/provider-analysis.json",
        name="provider-analysis.json",
        mime_type="application/json",
        size_bytes=120,
        metadata_json={"sha256": "abc"},
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    assert artifact.id is not None

    registered = repo.register_artifact(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        artifact_id=artifact.id,
        semantic_name="provider-analysis",
        action_ref=action_ref,
        input_hash="input-hash",
    )

    assert registered.semantic_name == "provider-analysis"
    assert registered.artifact["uri"] == "/tmp/provider-analysis.json"
    page = repo.list_artifacts(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        action_ref=action_ref,
    )
    assert page.total_estimate == 1
    assert page.items[0].artifact_id == artifact.id
    assert repo.get(project_id=project_id, context_ref="ctx_provider_analysis").artifact_count == 1


def test_execution_context_artifact_lookup_is_not_page_window_limited(
    session: Session,
    project_id: int,
) -> None:
    repo = ExecutionContextRepository(session)
    repo.create(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        name="Provider analysis context",
    )
    last_artifact_id = 0
    for index in range(201):
        artifact = Artifact(
            project_id=project_id,
            kind="action-output",
            uri=f"/tmp/provider-analysis-{index}.json",
            name=f"provider-analysis-{index}.json",
        )
        session.add(artifact)
        session.commit()
        session.refresh(artifact)
        assert artifact.id is not None
        last_artifact_id = artifact.id
        repo.register_artifact(
            project_id=project_id,
            context_ref="ctx_provider_analysis",
            artifact_id=artifact.id,
        )

    found = repo.get_artifact(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        artifact_id=last_artifact_id,
    )
    after_cursor_page = repo.list_artifacts(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        limit=1,
        after_id=found.id - 1,
    )

    assert found.artifact_id == last_artifact_id
    assert found.artifact["uri"] == "/tmp/provider-analysis-200.json"
    assert after_cursor_page.total_estimate == 201


def test_execution_context_artifact_read_returns_bounded_json_path_content(
    session: Session,
    project_id: int,
    tmp_path: Path,
) -> None:
    repo = ExecutionContextRepository(session)
    repo.create(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        name="Provider analysis context",
    )
    path = tmp_path / "analysis-output.json"
    settings = Settings(data_dir=tmp_path, state_dir=tmp_path / "state")
    path = settings.generated_assets_dir / "action-outputs/project-1/analysis-output.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"data": {"items": [{"id": "one"}, {"id": "two"}]}, "summary": "ok"}
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    path.write_bytes(encoded)
    action_call = ActionCall(
        project_id=project_id,
        action_key="action.run",
        plugin_slug="provider",
        operation="run",
        status=ActionCallStatus.SUCCESS,
        dry_run=False,
    )
    session.add(action_call)
    session.commit()
    session.refresh(action_call)
    assert action_call.id is not None
    artifact = Artifact(
        project_id=project_id,
        kind="action-output",
        uri="/generated-assets/action-outputs/project-1/analysis-output.json",
        name="analysis-output.json",
        mime_type="application/json",
        size_bytes=len(encoded),
        metadata_json={
            "file_backed_action_output": True,
            "absolute_path": str(path),
            "sha256": "sha-for-test",
        },
        provenance_json={
            "action_call_id": action_call.id,
            "context_ref": "ctx_provider_analysis",
            "action_ref": "provider.action.run",
        },
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    assert artifact.id is not None
    repo.register_artifact(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        artifact_id=artifact.id,
        semantic_name="analysis-output",
        action_call_id=action_call.id,
        action_ref="provider.action.run",
    )

    result = asyncio.run(
        execution_context_artifact_read(
            ExecutionContextArtifactReadInput(
                project_id=project_id,
                context_ref="ctx_provider_analysis",
                artifact_id=artifact.id,
                json_path="$.data.items[1]",
                max_bytes=200,
            ),
            MCPContext(
                session=session,
                request_id="test",
                project_id=project_id,
                extras={"settings": settings},
            ),
            None,  # type: ignore[arg-type]
        )
    )

    assert result["content_available"] is True
    assert result["json_path"] == "$.data.items[1]"
    assert json.loads(result["content"]) == {"id": "two"}
    assert result["bytes"] == len(encoded)
    assert result["sha256"] == "sha-for-test"

    truncated = asyncio.run(
        execution_context_artifact_read(
            ExecutionContextArtifactReadInput(
                project_id=project_id,
                context_ref="ctx_provider_analysis",
                artifact_id=artifact.id,
                json_path="$",
                max_bytes=12,
            ),
            MCPContext(
                session=session,
                request_id="test",
                project_id=project_id,
                extras={"settings": settings},
            ),
            None,  # type: ignore[arg-type]
        )
    )
    assert truncated["content_available"] is True
    assert truncated["content_truncated"] is True
    assert len(truncated["content"].encode("utf-8")) <= 12

    artifact.mime_type = "text/plain"
    session.add(artifact)
    session.commit()
    with pytest.raises(ValidationError):
        asyncio.run(
            execution_context_artifact_read(
                ExecutionContextArtifactReadInput(
                    project_id=project_id,
                    context_ref="ctx_provider_analysis",
                    artifact_id=artifact.id,
                ),
                MCPContext(
                    session=session,
                    request_id="test",
                    project_id=project_id,
                    extras={"settings": settings},
                ),
                None,  # type: ignore[arg-type]
            )
        )


def test_execution_context_artifact_read_rejects_generated_asset_path_escape(
    session: Session,
    project_id: int,
    tmp_path: Path,
) -> None:
    repo = ExecutionContextRepository(session)
    repo.create(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        name="Provider analysis context",
    )
    settings = Settings(data_dir=tmp_path, state_dir=tmp_path / "state")
    escaped_path = tmp_path / "generated-assets_evil/out.json"
    escaped_path.parent.mkdir(parents=True, exist_ok=True)
    escaped_path.write_text("{}", encoding="utf-8")
    action_call = ActionCall(
        project_id=project_id,
        action_key="action.run",
        plugin_slug="provider",
        operation="run",
        status=ActionCallStatus.SUCCESS,
        dry_run=False,
    )
    session.add(action_call)
    session.commit()
    session.refresh(action_call)
    assert action_call.id is not None
    artifact = Artifact(
        project_id=project_id,
        kind="action-output",
        uri="/generated-assets/../generated-assets_evil/out.json",
        name="out.json",
        mime_type="application/json",
        size_bytes=2,
        metadata_json={
            "file_backed_action_output": True,
            "absolute_path": str(escaped_path),
            "sha256": "sha-for-test",
        },
        provenance_json={
            "action_call_id": action_call.id,
            "context_ref": "ctx_provider_analysis",
        },
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    assert artifact.id is not None
    repo.register_artifact(
        project_id=project_id,
        context_ref="ctx_provider_analysis",
        artifact_id=artifact.id,
        action_call_id=action_call.id,
    )

    with pytest.raises(ValidationError):
        asyncio.run(
            execution_context_artifact_read(
                ExecutionContextArtifactReadInput(
                    project_id=project_id,
                    context_ref="ctx_provider_analysis",
                    artifact_id=artifact.id,
                ),
                MCPContext(
                    session=session,
                    request_id="test",
                    project_id=project_id,
                    extras={"settings": settings},
                ),
                None,  # type: ignore[arg-type]
            )
        )
