"""MCP tests for StackOS action describe/validate tools."""

from __future__ import annotations

import base64
import json
import wave
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select
from stackos_connectors import get_default_client

from stackos.actions.repository.background import BACKGROUND_ACTION_TASKS
from stackos.actions.repository.utils import _redact_for_audit
from stackos.config import Settings
from stackos.db.connection import make_engine
from stackos.db.models import (
    Action,
    ActionCall,
    ActionCallStatus,
    Artifact,
    IdempotencyKey,
    PayloadSecret,
)
from stackos.repositories.resources import ArtifactRepository
from tests.helpers.integration_clock import IntegrationClock, isolated_provider_clock

from .conftest import MCPClient


@pytest.fixture
def _media_provider_clock(
    mcp_client: MCPClient, monkeypatch: pytest.MonkeyPatch
) -> Iterator[IntegrationClock]:
    """Only mocked provider pacing is virtual; the app is already started."""
    with isolated_provider_clock(monkeypatch) as clock:
        yield clock


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


def _call_tool_raw(
    mcp: MCPClient,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return mcp.call_tool_structured(name, {**arguments, "response_mode": "raw"})


def _call_action_execute_raw(
    mcp: MCPClient,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return mcp.call_tool_structured(
        "action.execute",
        {
            **arguments,
            "output_policy_json": {"mode": "inline"},
            "response_mode": "raw",
        },
    )


def test_action_describe_and_validate_are_read_only_discovery_tools(
    mcp_client: MCPClient,
) -> None:
    tools = {tool["name"] for tool in mcp_client.list_tools()}

    assert "action.list" in tools
    assert "action.describe" in tools
    assert "action.validate" in tools
    assert "action.execute" in tools
    assert "action.run" in tools
    assert "actionCall.get" in tools
    assert "secret.set" in tools

    listed = mcp_client.call_tool_structured("action.list", {"query": "catalog"})
    described = _call_tool_raw(
        mcp_client,
        "action.describe",
        {"action_ref": "core.catalog.describe", "response_mode": "raw"},
    )
    validation = _call_tool_raw(
        mcp_client,
        "action.validate",
        {"action_ref": "core.catalog.describe", "input_json": {}, "response_mode": "raw"},
    )

    assert listed["count"] >= 1
    assert any(item["action_ref"] == "core.catalog.describe" for item in listed["items"])
    assert listed["items"][0]["availability_status"]
    assert described["manifest"]["action_ref"] == "core.catalog.describe"
    assert described["execution_available"] is False
    assert described["agent_execute_available"] is False
    assert validation["valid"] is True
    assert validation["issues"] == []


def test_action_call_get_is_project_scoped_and_returns_running_or_terminal_state(
    mcp_client: MCPClient,
    mcp_settings: Settings,
    seeded_project: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = seeded_project["data"]["id"]
    other_project = mcp_client.call_tool_structured(
        "project.create",
        {
            "slug": "other-action-call-project",
            "name": "Other Action Call Project",
            "domain": "other.example",
            "locale": "en-US",
        },
    )
    other_project_id = other_project["data"]["id"]

    engine = make_engine(mcp_settings.db_path)
    try:
        with Session(engine) as session:
            running = ActionCall(
                project_id=project_id,
                action_key="file.upload",
                plugin_slug="ftp",
                provider_key="ftp",
                connector_key="ftp",
                operation="file.upload",
                status=ActionCallStatus.RUNNING,
                dry_run=False,
                credential_ref="cred_private",
                request_json={"local_path": "/private/local.txt", "password": "secret"},
            )
            failed = ActionCall(
                project_id=project_id,
                action_key="file.download",
                plugin_slug="ftp",
                provider_key="ftp",
                connector_key="ftp",
                operation="file.download",
                status=ActionCallStatus.FAILED,
                dry_run=False,
                response_json={
                    "status": "failed",
                    "provider_error": {"outcome_unknown": True, "retry_safe": False},
                },
                error="daemon-restart-orphan",
            )
            session.add(running)
            session.add(failed)
            session.commit()
            session.refresh(running)
            session.refresh(failed)
            running_id = int(running.id or 0)
            failed_id = int(failed.id or 0)
    finally:
        engine.dispose()

    monkeypatch.setattr(
        BACKGROUND_ACTION_TASKS,
        "progress",
        lambda action_call_id: (
            {
                "phase": "transferring",
                "operation": "file.upload",
                "current_source_path": "/private/ftp-path-canary/source.txt",
                "current_target_path": "/remote/ftp-secret-canary/target.txt",
                "bytes_transferred": 12,
                "completed_count": 1,
                "skipped_count": 0,
                "failed_count": 0,
            }
            if action_call_id == running_id
            else None
        ),
    )

    running_envelope = mcp_client.call_tool_structured(
        "actionCall.get",
        {"project_id": project_id, "action_call_id": running_id},
    )
    running_result = running_envelope["data"]
    assert running_result["action_call_id"] == running_id
    assert running_result["status"] == "running"
    assert running_result["poll_operation"] == "actionCall.get"
    assert running_result["poll_arguments"] == {"action_call_id": running_id}
    assert running_result["next_poll_after_ms"] == 500
    assert running_result["progress"] == {
        "phase": "transferring",
        "operation": "file.upload",
        "bytes_transferred": 12,
        "completed_count": 1,
        "skipped_count": 0,
        "failed_count": 0,
    }
    assert "credential_ref" not in running_result
    assert "request_json" not in running_result
    assert "/private/local.txt" not in json.dumps(running_result)
    assert "ftp-path-canary" not in json.dumps(running_result)
    assert "ftp-secret-canary" not in json.dumps(running_result)

    terminal_result = _call_tool_raw(
        mcp_client,
        "actionCall.get",
        {"project_id": project_id, "action_call_id": failed_id},
    )
    assert terminal_result["status"] == "failed"
    assert terminal_result["error"] == "daemon-restart-orphan"
    assert terminal_result["outcome_unknown"] is True
    assert terminal_result["retry_safe"] is False
    assert terminal_result["poll_operation"] is None

    wrong_project = mcp_client.call_tool_error(
        "actionCall.get",
        {"project_id": other_project_id, "action_call_id": running_id},
    )
    assert wrong_project["code"] == -32004


def test_secret_set_is_write_only_mcp_ingress_with_safe_replay(
    mcp_client: MCPClient,
    mcp_settings: Settings,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    tools = {tool["name"]: tool for tool in mcp_client.list_tools()}
    schema = tools["secret.set"]["inputSchema"]
    assert schema["properties"]["value"]["writeOnly"] is True

    arguments = {
        "project_id": project_id,
        "value": "mcp-transit-canary-2fdd9a47",
        "idempotency_key": "mcp-transit-create",
        "response_mode": "raw",
    }
    first = mcp_client.call_tool_structured("secret.set", arguments)
    replay = mcp_client.call_tool_structured("secret.set", arguments)
    distinct = mcp_client.call_tool_structured(
        "secret.set",
        {
            **arguments,
            "idempotency_key": "mcp-transit-create-distinct",
        },
    )

    assert first["data"] == replay["data"]
    assert replay["idempotency_replay"] is True
    assert first["data"]["secret_ref"].startswith("secret_")
    assert distinct["data"]["secret_ref"] != first["data"]["secret_ref"]
    assert "mcp-transit-canary-2fdd9a47" not in json.dumps(first)
    assert "mcp-transit-canary-2fdd9a47" not in json.dumps(replay)
    assert "mcp-transit-canary-2fdd9a47" not in json.dumps(distinct)
    engine = make_engine(mcp_settings.db_path)
    try:
        with Session(engine) as session:
            idempotency_rows = list(
                session.exec(
                    select(IdempotencyKey).where(IdempotencyKey.tool_name == "secret.set")
                ).all()
            )
            secret_rows = list(session.exec(select(PayloadSecret)).all())
            assert len(idempotency_rows) == 2
            assert len(secret_rows) == 2
            assert "mcp-transit-canary-2fdd9a47" not in json.dumps(
                [row.response_json for row in idempotency_rows]
            )
            assert all(
                b"mcp-transit-canary-2fdd9a47" not in row.encrypted_payload for row in secret_rows
            )
    finally:
        engine.dispose()


def test_action_describe_reports_project_availability(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    _create_openai_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "openai-images", "monthly_budget_usd": 10.0},
        headers=mcp_client._headers(),
    )
    assert budget_resp.status_code == 200

    described = _call_tool_raw(
        mcp_client,
        "action.describe",
        {
            "project_id": project_id,
            "action_ref": "utils.image.generate",
            "response_mode": "raw",
        },
    )
    listed = mcp_client.call_tool_structured(
        "action.list",
        {"project_id": project_id, "plugin_slug": "utils", "query": "image", "executable": True},
    )

    assert described["availability"]["status"] == "ready"
    assert described["exposure"]["state"] == "external_connected"
    assert described["exposure"]["visible_by_default"] is True
    assert described["execution_available"] is True
    assert described["availability"]["credential_state"] == "available"
    assert described["availability"]["budget_state"] == "available"
    assert described["provider_setup"]["provider_key"] == "openai-images"
    assert described["provider_setup"]["local_setup_url"].endswith(
        f"/projects/{project_id}/connections?provider_key=openai-images"
    )
    assert described["provider_setup"]["credential_url"] == "https://platform.openai.com/api-keys"
    assert described["provider_setup"]["billing_url"].startswith("https://platform.openai.com/")
    assert described["execution_context"]["discover"]["operation"] == "executionContext.discover"
    assert described["execution_context"]["discover"]["arguments"] == {
        "action_ref": "utils.image.generate",
        "project_id": project_id,
        "provider_key": "openai-images",
    }
    assert described["execution_context"]["pass_context_ref_to"] == [
        "action.validate",
        "action.run",
        "action.execute",
    ]
    assert described["execution_context"]["payload_boundary"]["provider_context_json"].startswith(
        "reusable provider/account scope"
    )
    capability_metadata = described["manifest"]["config_json"]["capability_metadata"]
    assert capability_metadata["modes"] == ["text-to-image"]
    assert capability_metadata["limits"]["prompt_max_chars"] == 32000
    assert described["manifest"]["output_schema_json"]["properties"]["data"]["type"] == "array"
    image_items = [item for item in listed["items"] if item["action_ref"] == "utils.image.generate"]
    assert image_items
    assert image_items[0]["exposure"]["visible_by_default"] is True


def test_execution_context_discover_compact_returns_create_guidance(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    discovered = mcp_client.call_tool_structured(
        "executionContext.discover",
        {
            "project_id": project_id,
            "plugin_slug": "trackbooth",
            "provider_key": "trackbooth",
            "action_ref": "trackbooth.api.offers_findall",
            "ticket_key": "copy-offers",
        },
    )

    assert discovered["context_refs"] == []
    assert discovered["next_calls"]["create"]["operation"] == "executionContext.create"
    assert discovered["next_calls"]["create"]["arguments"] == {
        "project_id": project_id,
        "name": "trackbooth.api.offers_findall context",
        "plugin_slug": "trackbooth",
        "provider_key": "trackbooth",
        "action_ref": "trackbooth.api.offers_findall",
        "ticket_key": "copy-offers",
    }


def test_action_list_hides_disconnected_external_integrations_by_default(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    hidden = mcp_client.call_tool_structured(
        "action.list",
        {"project_id": project_id, "plugin_slug": "utils", "query": "image"},
    )
    full = mcp_client.call_tool_structured(
        "action.list",
        {
            "project_id": project_id,
            "plugin_slug": "utils",
            "query": "image",
            "include_unavailable_integrations": True,
        },
    )
    local = mcp_client.call_tool_structured(
        "action.list",
        {"project_id": project_id, "plugin_slug": "utils", "query": "sitemap"},
    )
    deferred = mcp_client.call_tool_structured(
        "action.list",
        {"project_id": project_id, "plugin_slug": "utils", "query": "extract"},
    )
    deferred_full = mcp_client.call_tool_structured(
        "action.list",
        {
            "project_id": project_id,
            "plugin_slug": "utils",
            "query": "extract",
            "include_unavailable_integrations": True,
        },
    )
    branding_internal = mcp_client.call_tool_structured(
        "action.list",
        {"project_id": project_id, "plugin_slug": "branding", "query": "evidence"},
    )
    branding_internal_full = mcp_client.call_tool_structured(
        "action.list",
        {
            "project_id": project_id,
            "plugin_slug": "branding",
            "query": "evidence",
            "include_unavailable_integrations": True,
        },
    )

    assert not any(item["action_ref"] == "utils.image.generate" for item in hidden["items"])
    assert hidden["hidden_count"] >= 1
    image_items = [item for item in full["items"] if item["action_ref"] == "utils.image.generate"]
    assert image_items
    assert image_items[0]["exposure"]["state"] == "external_not_connected"
    assert image_items[0]["exposure"]["visible_by_default"] is False
    assert image_items[0]["exposure"]["hidden_reason"] == "integration_not_connected"
    assert any(item["action_ref"] == "utils.sitemap.fetch" for item in local["items"])
    assert not any(item["action_ref"] == "utils.web.extract" for item in deferred["items"])
    extract_items = [
        item for item in deferred_full["items"] if item["action_ref"] == "utils.web.extract"
    ]
    assert extract_items
    assert extract_items[0]["exposure"]["visible_by_default"] is False
    assert extract_items[0]["exposure"]["hidden_reason"] == "action_deferred"
    visible_evidence_items = [
        item
        for item in branding_internal["items"]
        if item["action_ref"] == "branding.evidence.capture"
    ]
    assert visible_evidence_items
    assert visible_evidence_items[0]["availability_status"] == "ready"
    assert visible_evidence_items[0]["exposure"]["visible_by_default"] is True
    evidence_items = [
        item
        for item in branding_internal_full["items"]
        if item["action_ref"] == "branding.evidence.capture"
    ]
    assert evidence_items
    assert evidence_items[0]["availability_status"] == "ready"
    assert evidence_items[0]["exposure"]["visible_by_default"] is True


def test_action_list_exposes_serper_only_after_connection(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    hidden = mcp_client.call_tool_structured(
        "action.list",
        {"project_id": project_id, "plugin_slug": "seo", "provider_key": "serper"},
    )
    full = mcp_client.call_tool_structured(
        "action.list",
        {
            "project_id": project_id,
            "plugin_slug": "seo",
            "provider_key": "serper",
            "include_unavailable_integrations": True,
        },
    )

    assert not any(item["action_ref"] == "seo.serper.search" for item in hidden["items"])
    assert hidden["hidden_count"] >= 1
    serper_items = [item for item in full["items"] if item["action_ref"] == "seo.serper.search"]
    assert serper_items
    assert serper_items[0]["availability_status"] == "missing_credential"
    assert serper_items[0]["exposure"]["state"] == "external_not_connected"
    assert serper_items[0]["exposure"]["visible_by_default"] is False
    assert serper_items[0]["exposure"]["hidden_reason"] == "integration_not_connected"

    _create_serper_credential(mcp_client, project_id)
    ready = mcp_client.call_tool_structured(
        "action.list",
        {"project_id": project_id, "plugin_slug": "seo", "provider_key": "serper"},
    )

    ready_items = [item for item in ready["items"] if item["action_ref"] == "seo.serper.search"]
    assert ready_items
    assert ready_items[0]["availability_status"] == "ready"
    assert ready_items[0]["exposure"]["state"] == "external_connected"
    assert ready_items[0]["exposure"]["visible_by_default"] is True


def test_integration_list_summarizes_hidden_external_actions(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    listed = mcp_client.call_tool_structured(
        "integration.list",
        {
            "project_id": project_id,
            "provider_key": "openai-images",
            "include_actions": True,
        },
    )

    assert listed["count"] == 1
    row = listed["items"][0]
    assert row["provider_key"] == "openai-images"
    assert row["state"] == "not_connected"
    assert row["connected"] is False
    assert row["hidden_action_count"] >= 1
    assert row["next_action"]["tool"] == "connection.list"
    assert row["next_action"]["reason"] == (
        "Connect provider 'openai-images' before its required actions appear."
    )
    assert row["next_action"]["arguments"] == {
        "project_id": project_id,
        "provider_key": "openai-images",
    }
    assert row["next_action"]["ui_url"].endswith(
        f"/projects/{project_id}/connections?provider_key=openai-images"
    )
    assert row["setup"]["provider_key"] == "openai-images"
    assert row["setup"]["local_setup_url"].endswith(
        f"/projects/{project_id}/connections?provider_key=openai-images"
    )
    assert row["setup"]["credential_url"] == "https://platform.openai.com/api-keys"
    assert row["setup"]["docs_url"].endswith("/image-generation")
    assert row["next_action"]["setup"]["credential_url"] == "https://platform.openai.com/api-keys"
    assert any(action["action_ref"] == "utils.image.generate" for action in row["actions"])


def test_action_run_unavailable_external_action_reports_exposure_repair_context(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    err = mcp_client.call_tool_error(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "utils.image.generate",
            "input_json": {"prompt": "A tiny product mockup"},
            "confirm_direct": True,
            "intent_summary": "Verify unavailable integration repair context.",
        },
    )

    assert err["code"] == -32602
    assert err["data"]["status"] == "missing_credential"
    exposure = err["data"]["exposure"]
    assert exposure["state"] == "external_not_connected"
    assert exposure["hidden_reason"] == "integration_not_connected"
    assert exposure["next_action"]["ui_url"].endswith(
        f"/projects/{project_id}/connections?provider_key=openai-images"
    )


def test_action_validate_rejects_raw_secret_payloads(mcp_client: MCPClient) -> None:
    err = mcp_client.call_tool_error(
        "action.validate",
        {
            "action_ref": "core.catalog.describe",
            "input_json": {"api_key": "sk-leak"},
        },
    )

    assert err["code"] == -32602
    assert "must not contain secrets" in err["data"]["detail"]


def _create_account(
    mcp: MCPClient,
    project_id: int,
    *,
    provider_key: str,
    auth_method_key: str,
    fields: dict[str, object],
    display_name: str | None = None,
) -> str:
    response = mcp.test_client.post(
        f"/api/v1/auth/accounts/{provider_key}",
        json={
            "auth_method_key": auth_method_key,
            "display_name": display_name or f"{provider_key} - Default",
            "attach_project_id": project_id,
            "fields": fields,
        },
        headers=mcp._headers(),
    )
    response.raise_for_status()
    return str(response.json()["data"]["credential_ref"])


def _create_openai_credential(mcp: MCPClient, project_id: int) -> str:
    return _create_account(
        mcp,
        project_id,
        provider_key="openai-images",
        auth_method_key="api_key",
        fields={"api_key": "sk-openai"},
    )


def test_elevenlabs_discovery_projects_executable_actions_without_placeholders(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    filters = {"project_id": project_id, "provider_key": "elevenlabs"}
    inventory = mcp_client.call_tool_structured("integration.list", filters)
    (setup,) = inventory["items"]
    assert setup["state"] == "not_connected"
    assert setup["setup"]["credential_label"] == "ElevenLabs API key"
    assert setup["setup"]["local_setup_url"].endswith(
        f"/projects/{project_id}/connections?provider_key=elevenlabs"
    )
    assert mcp_client.call_tool_structured("action.list", filters)["items"] == []
    catalog = mcp_client.call_tool_structured(
        "action.list", {**filters, "include_unavailable_integrations": True}
    )["items"]
    assert len(catalog) == setup["hidden_action_count"]
    assert all(entry["connector_key"] for entry in catalog)
    denied = mcp_client.call_tool_error(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "utils.elevenlabs.subscription.get",
            "input_json": {},
        },
    )
    assert denied["data"]["status"] == "missing_credential"

    credential_ref = _create_account(
        mcp_client,
        project_id,
        provider_key="elevenlabs",
        auth_method_key="api_key",
        fields={"api_key": "synthetic-elevenlabs-action-key"},
    )
    listed = mcp_client.call_tool_structured("action.list", filters)
    expected_effects = {
        "subscription.get": "read",
        "models.list": "read",
        "voices.list": "read",
        "voices.settings.default.get": "read",
        "voices.settings.get": "read",
        "library.search": "read",
        "library.add": "write",
        "voices.create": "write",
        "dictionaries.create": "write",
        "speech.generate": "cost",
        "voices.design": "cost",
        "alignment.create": "cost",
    }
    expected_refs = {f"utils.elevenlabs.{operation}" for operation in expected_effects}
    assert expected_refs == {entry["action_ref"] for entry in listed["items"]}
    client = get_default_client()
    for operation, effect in expected_effects.items():
        key = f"elevenlabs.{operation}"
        (native,) = client.describe("elevenlabs", key)["actions"]
        manifest = _call_tool_raw(
            mcp_client, "action.describe", {"project_id": project_id, "action_ref": f"utils.{key}"}
        )["manifest"]
        if operation == "alignment.create":
            host_input = manifest["input_schema_json"]
            assert host_input["additionalProperties"] is False
            assert (
                set(host_input["required"])
                == set(host_input["properties"])
                == {
                    "audio_artifact_id",
                    "text",
                }
            )
            assert host_input["properties"]["text"] == native["input_schema"]["properties"]["text"]
            native["output_schema"]["$defs"]["timed_item"]["additionalProperties"] = False
        else:
            assert manifest["input_schema_json"] == native["input_schema"]
        if operation not in {"speech.generate", "voices.design"}:
            assert manifest["output_schema_json"] == native["output_schema"]
        assert manifest["operation"] == native["operation"]
        assert manifest["risk_level"] == native["risk_level"] == effect
        assert manifest["requires_credential"] is True
    arguments = {
        "project_id": project_id,
        "action_ref": "utils.elevenlabs.dictionaries.create",
        "credential_ref": credential_ref,
        "input_json": {
            "name": "Test",
            "rules": [{"type": "phoneme", "string_to_replace": "test", "phoneme": "test"}],
        },
    }
    assert _call_tool_raw(mcp_client, "action.validate", arguments)["valid"] is False
    invalid = mcp_client.call_tool_error(
        "action.run", {**arguments, "confirm_direct": True, "intent_summary": "Test invalid rules."}
    )
    assert invalid["code"] == -32602
    assert httpx_mock.get_requests() == []


@pytest.fixture
def elevenlabs_action_account(mcp_client: MCPClient, seeded_project: dict) -> str:
    return _create_account(
        mcp_client,
        seeded_project["data"]["id"],
        provider_key="elevenlabs",
        auth_method_key="api_key",
        fields={"api_key": "synthetic-elevenlabs-action-key"},
    )


@pytest.mark.parametrize(
    ("operation", "path", "inputs", "query", "response", "expected"),
    [
        (
            "subscription.get",
            "/v1/user/subscription",
            {},
            {},
            {"tier": "free", "character_count": 0, "character_limit": 0, "extra": "ignored"},
            {"tier": "free", "character_count": 0, "character_limit": 0},
        ),
        (
            "models.list",
            "/v1/models",
            {},
            {},
            [{"model_id": "test-model"}],
            {"models": [{"model_id": "test-model"}]},
        ),
        (
            "voices.settings.default.get",
            "/v1/voices/settings/default",
            {},
            {},
            {"stability": 0, "use_speaker_boost": False},
            {"stability": 0, "use_speaker_boost": False},
        ),
        (
            "voices.settings.get",
            "/v1/voices/saved-id/settings",
            {"voice_id": "saved-id"},
            {},
            {"speed": None},
            {"speed": None},
        ),
        (
            "library.search",
            "/v1/shared-voices",
            {"search": "narrator"},
            {"search": "narrator", "page": "0", "page_size": "30"},
            {
                "voices": [
                    {"voice_id": "shared-id", "public_owner_id": "owner-id", "name": "Voice"}
                ],
                "has_more": False,
            },
            {
                "voices": [
                    {"voice_id": "shared-id", "public_owner_id": "owner-id", "name": "Voice"}
                ],
                "has_more": False,
            },
        ),
    ],
)
def test_elevenlabs_direct_reads_cross_native_transport(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    elevenlabs_action_account: str,
    operation: str,
    path: str,
    inputs: dict,
    query: dict,
    response: object,
    expected: dict,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"https://api.elevenlabs.io{path}",
        match_params=query,
        match_headers={"xi-api-key": "synthetic-elevenlabs-action-key"},
        json=response,
    )
    out = _call_tool_raw(
        mcp_client,
        "action.run",
        {
            "project_id": seeded_project["data"]["id"],
            "action_ref": f"utils.elevenlabs.{operation}",
            "input_json": inputs,
            "credential_ref": elevenlabs_action_account,
            "output_policy_json": {"mode": "inline"},
        },
    )
    assert out["data"]["output_json"] == expected
    assert out["data"]["action_call"]["response_json"] == expected
    assert len(httpx_mock.get_requests()) == 1


def test_elevenlabs_returned_voice_cursor_fetches_next_page(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    elevenlabs_action_account: str,
) -> None:
    arguments = {
        "project_id": seeded_project["data"]["id"],
        "action_ref": "utils.elevenlabs.voices.list",
        "credential_ref": elevenlabs_action_account,
    }
    url = "https://api.elevenlabs.io/v2/voices"
    httpx_mock.add_response(
        method="GET",
        url=url,
        match_params={"page_size": "10"},
        json={"voices": [{"voice_id": "first"}], "has_more": True, "next_page_token": "page-2"},
    )
    httpx_mock.add_response(
        method="GET",
        url=url,
        match_params={"page_size": "10", "next_page_token": "page-2"},
        json={"voices": [{"voice_id": "second"}], "has_more": False},
    )
    first = _call_tool_raw(mcp_client, "action.run", arguments)["data"]
    path = Path(first["output_json"]["file"]["path"])
    page = json.loads(path.read_text(encoding="utf-8"))["response"]["output_json"]
    assert page["voices"] == [{"voice_id": "first"}]
    assert page["page_token"] is None and page["has_more"] is True
    cursor = page["next_page_token"]
    assert cursor == "page-2"

    second = _call_tool_raw(
        mcp_client,
        "action.run",
        {
            **arguments,
            "input_json": {"page_token": cursor},
            "output_policy_json": {"mode": "inline"},
        },
    )["data"]
    assert second["output_json"] == {
        "voices": [{"voice_id": "second"}],
        "has_more": False,
        "page_size": 10,
        "page_token": cursor,
        "next_page_token": None,
    }
    assert second["action_call"]["request_json"] == {"page_token": cursor}
    assert second["action_call"]["response_json"] == second["output_json"]
    denied = mcp_client.call_tool_error(
        "action.validate",
        {**arguments, "input_json": {"page_token": "api_key=synthetic-value"}},
    )
    assert denied["data"]["paths"] == ["$.page_token"]
    assert len(httpx_mock.get_requests()) == 2


def test_elevenlabs_cursor_policy_keeps_generic_audit_protection() -> None:
    assert _redact_for_audit({"page_token": "secret", "next_page_token": "secret"}) == {
        "page_token": "[redacted]",
        "next_page_token": "[redacted]",
    }


_ELEVENLABS_LIBRARY_ADD = {
    "public_owner_id": "owner-id",
    "voice_id": "shared-id",
    "new_name": "Reference narrator",
}
_ELEVENLABS_VOICE_CREATE = {
    "generated_voice_id": "preview-id",
    "voice_name": "Test narrator",
    "voice_description": "A fictional narrator for product tests",
}
_ELEVENLABS_DICTIONARY_CREATE = {
    "name": "Test names",
    "rules": [{"type": "alias", "string_to_replace": "UN", "alias": "United Nations"}],
}


@pytest.mark.parametrize("status_code", [200, 429, 503])
@pytest.mark.parametrize(
    ("operation", "path", "inputs", "body", "result"),
    [
        (
            "library.add",
            "/v1/voices/add/owner-id/shared-id",
            _ELEVENLABS_LIBRARY_ADD,
            {"new_name": "Reference narrator"},
            {"voice_id": "added-id"},
        ),
        (
            "voices.create",
            "/v1/text-to-voice",
            _ELEVENLABS_VOICE_CREATE,
            _ELEVENLABS_VOICE_CREATE,
            {"voice_id": "created-id"},
        ),
        (
            "dictionaries.create",
            "/v1/pronunciation-dictionaries/add-from-rules",
            _ELEVENLABS_DICTIONARY_CREATE,
            _ELEVENLABS_DICTIONARY_CREATE,
            {"id": "dict-id", "version_id": "version-id"},
        ),
    ],
)
def test_elevenlabs_direct_writes_require_confirmation_and_replay_once(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    elevenlabs_action_account: str,
    operation: str,
    path: str,
    inputs: dict,
    body: dict,
    result: dict,
    status_code: int,
) -> None:
    arguments = {
        "project_id": seeded_project["data"]["id"],
        "action_ref": f"utils.elevenlabs.{operation}",
        "input_json": inputs,
        "credential_ref": elevenlabs_action_account,
    }
    checked = _call_tool_raw(mcp_client, "action.validate", arguments)
    assert checked["valid"] is True
    denied = mcp_client.call_tool_error("action.run", arguments)
    assert denied["code"] == -32602
    assert "confirm_direct=true" in denied["data"]["detail"]
    assert httpx_mock.get_requests() == []

    httpx_mock.add_response(
        method="POST",
        url=f"https://api.elevenlabs.io{path}",
        match_json=body,
        match_headers={"xi-api-key": "synthetic-elevenlabs-action-key"},
        status_code=status_code,
        headers={"request-id": "synthetic-provider-request"},
        json={**result, "provider_echo": "synthetic-elevenlabs-action-key"},
        is_reusable=status_code == 503,
    )
    confirmed = {
        **arguments,
        "confirm_direct": True,
        "intent_summary": "Create one synthetic fixture.",
        "idempotency_key": "elevenlabs-test-mutation",
        "output_policy_json": {"mode": "inline"},
    }
    if status_code != 200:
        failure = mcp_client.call_tool_error("action.run", confirmed)
        assert failure["data"]["provider_status_code"] == status_code
        assert len(httpx_mock.get_requests()) == 1
        return
    first = _call_tool_raw(mcp_client, "action.run", confirmed)["data"]
    replay = _call_tool_raw(mcp_client, "action.run", confirmed)["data"]
    assert first["output_json"] == result
    assert replay["action_call"]["id"] == first["action_call"]["id"]
    assert first["action_call"]["connector_key"] == "elevenlabs"
    assert "synthetic-elevenlabs-action-key" not in json.dumps(first)
    assert len(httpx_mock.get_requests()) == 1


def test_elevenlabs_post_timeout_does_not_retry(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    elevenlabs_action_account: str,
) -> None:
    httpx_mock.add_exception(
        httpx.ReadTimeout("synthetic timeout"),
        method="POST",
        url="https://api.elevenlabs.io/v1/voices/add/owner-id/shared-id",
    )
    failed = mcp_client.call_tool_error(
        "action.run",
        {
            "project_id": seeded_project["data"]["id"],
            "action_ref": "utils.elevenlabs.library.add",
            "input_json": _ELEVENLABS_LIBRARY_ADD,
            "credential_ref": elevenlabs_action_account,
            "confirm_direct": True,
            "intent_summary": "Exercise a synthetic timeout.",
            "idempotency_key": "timeout-fixture",
        },
    )
    assert failed["data"]["status"] == "failed"
    assert len(httpx_mock.get_requests()) == 1


def _start_elevenlabs_write_step(
    mcp_client: MCPClient,
    project_id: int,
    granted_ref: str = "utils.elevenlabs.library.add",
    denied_ref: str = "utils.elevenlabs.voices.create",
) -> tuple[dict, dict]:
    plan = mcp_client.call_tool_structured(
        "runPlan.create",
        {
            "project_id": project_id,
            "run_plan_json": {
                "schema_version": "stackos.run-plan.v1",
                "key": "elevenlabs-grant-test",
                "title": "ElevenLabs grant test",
                "grants": {
                    "mcp_tool_grants": [
                        {
                            "step_id": "add-voice",
                            "tool": "action.execute",
                            "action_refs": [granted_ref],
                        },
                    ]
                },
                "steps": [
                    {
                        "id": "add-voice",
                        "title": "Add a voice",
                        "action_refs": [granted_ref, denied_ref],
                    }
                ],
            },
        },
    )["data"]
    started = mcp_client.call_tool_structured(
        "runPlan.start", {"project_id": project_id, "run_plan_id": plan["id"]}
    )["data"]
    run_scope = {"run_plan_id": plan["id"], "run_token": started["run_token"]}
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            **run_scope,
            "step_id": "add-voice",
        },
    )["data"]
    return started, claimed


_ELEVENLABS_SPEECH = {"voice_id": "test-voice", "text": "Hi", "output_format": "wav_44100"}
_ELEVENLABS_DESIGN = {
    "voice_description": "A warm and deliberate narrator",
    "auto_generate_text": True,
    "model_id": "eleven_ttv_v3",
    "reference_audio_base64": "cHJpdmF0ZS1yZWZlcmVuY2UtYXVkaW8=",
}
_ELEVENLABS_TIMINGS = {
    "alignment": {
        "characters": ["H", "i"],
        "character_start_times_seconds": [0, 0.2],
        "character_end_times_seconds": [0.2, 0.4],
    },
    "normalized_alignment": {
        "characters": ["h", "i", "."],
        "character_start_times_seconds": [0, 0.1, 0.3],
        "character_end_times_seconds": [0.1, 0.3, 0.4],
    },
}


def _mock_elevenlabs_audio(httpx_mock: HTTPXMock, operation: str) -> None:
    if operation == "speech.generate":
        httpx_mock.add_response(
            method="POST",
            url="https://api.elevenlabs.io/v1/text-to-speech/test-voice/with-timestamps"
            "?output_format=wav_44100",
            match_json={"text": "Hi"},
            match_headers={"xi-api-key": "synthetic-elevenlabs-action-key"},
            headers={"request-id": "request-123", "history-item-id": "history-456"},
            json={
                **_ELEVENLABS_TIMINGS,
                "alignment": {**_ELEVENLABS_TIMINGS["alignment"], "audio_base64": "unexpected"},
                "audio_base64": "c3ludGhldGljLXdhdg==",
            },
        )
    else:
        httpx_mock.add_response(
            method="POST",
            url="https://api.elevenlabs.io/v1/text-to-voice/design",
            match_json=_ELEVENLABS_DESIGN,
            match_headers={"xi-api-key": "synthetic-elevenlabs-action-key"},
            headers={"request-id": "request-123"},
            json={
                "text": "The provider's script",
                "previews": [
                    {
                        "generated_voice_id": voice_id,
                        "duration_secs": duration,
                        "media_type": mime,
                        "language": "en",
                        "audio_base_64": encoded,
                    }
                    for voice_id, duration, mime, encoded in (
                        ("preview-first", 1.2, "audio/mpeg", "Zmlyc3Q="),
                        ("preview-middle", 2.3, "audio/mpeg", "***"),
                        ("preview-last", 3.4, "audio/ogg", "bGFzdA=="),
                    )
                ],
            },
        )


def _stored_elevenlabs_audio(settings: Settings, artifact_id: int, project_id: int) -> Path:
    engine = make_engine(settings.db_path)
    try:
        with Session(engine) as session:
            artifact = session.get(Artifact, artifact_id)
            assert artifact is not None and artifact.project_id == project_id
            custody = artifact.managed_file_json
            assert custody is not None
            path = Path(custody["root"]) / custody["path"]
            assert path.is_relative_to(settings.generated_assets_dir.resolve())
            info = path.stat()
            assert (info.st_dev, info.st_ino) == (custody["device"], custody["inode"])
            return path
    finally:
        engine.dispose()


_ALIGNMENT_REF = "utils.elevenlabs.alignment.create"
_ALIGNMENT_TEXT = "  Caf\u00e9, go! "


def _alignment_wav() -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(b"offline-wave-sample-42")
    return buffer.getvalue()


def _seed_alignment_audio(
    settings: Settings,
    project_id: int,
    *,
    mime_type: str = "audio/wav",
    managed: bool = True,
    generated: bool = True,
    filename: str = "private-source.wav",
) -> tuple[int, Path]:
    root = settings.generated_assets_dir.resolve()
    relative = Path("telegram") / str(project_id) / filename
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    audio = _alignment_wav()
    path.write_bytes(audio)
    engine = make_engine(settings.db_path)
    try:
        with Session(engine) as session:
            repository = ArtifactRepository(session)
            fields = {
                "kind": "file" if generated else "audio",
                "project_id": project_id,
                "mime_type": mime_type,
                "size_bytes": len(audio),
            }
            generated_uri = f"/generated-assets/{relative.as_posix()}"
            if managed:
                artifact = repository.create_generated_file(
                    asset_root=root, path=path, uri=generated_uri, **fields
                )
            else:
                artifact = repository.create(uri=path.as_uri(), **fields)
            return artifact.data.id, path
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("granted", "generated", "mode"),
    [(False, True, "file"), (True, True, "inline")],
)
def test_elevenlabs_alignment_uploads_owned_audio_once(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
    elevenlabs_action_account: str,
    granted: bool,
    generated: bool,
    mode: str,
) -> None:
    project_id = seeded_project["data"]["id"]
    artifact_id, source = _seed_alignment_audio(mcp_settings, project_id, generated=generated)
    audio_bytes = source.read_bytes()
    arguments = {
        "project_id": project_id,
        "action_ref": _ALIGNMENT_REF,
        "credential_ref": elevenlabs_action_account,
        "input_json": {"audio_artifact_id": artifact_id, "text": _ALIGNMENT_TEXT},
    }
    validation_arguments = dict(arguments)
    validated = _call_tool_raw(mcp_client, "action.validate", validation_arguments)
    assert validated["valid"] is True, validated
    assert validated["manifest"]["risk_level"] == "cost"
    if mode == "inline":
        arguments["output_policy_json"] = {"mode": "inline"}

    if granted:
        tool = "action.execute"
        other = "utils.elevenlabs.speech.generate"
        started, claimed = _start_elevenlabs_write_step(
            mcp_client, project_id, other, _ALIGNMENT_REF
        )
        denied = mcp_client.call_tool_error(tool, {**arguments, "run_token": started["run_token"]})
        assert denied["code"] == -32007
        started, claimed = _start_elevenlabs_write_step(
            mcp_client, project_id, _ALIGNMENT_REF, other
        )
        arguments["run_token"] = started["run_token"]
    else:
        tool = "action.run"
        denied = mcp_client.call_tool_error(tool, arguments)
        assert denied["data"]["risk_level"] == "cost"
        arguments.update(
            confirm_direct=True,
            intent_summary="Align synthetic project audio.",
        )
        arguments["idempotency_key"] = "elevenlabs-alignment-intent"
    assert httpx_mock.get_requests() == []
    timings = {
        "words": [
            {"text": "Caf\u00e9", "start": 0, "end": 0.375, "loss": None},
            {"text": "go!", "start": 0.5, "end": 0.95},
        ],
        "characters": [{"text": "C", "start": 0, "end": 0.075, "loss": 0}],
        "loss": 0.125,
    }
    httpx_mock.add_response(
        method="POST",
        url="https://api.elevenlabs.io/v1/forced-alignment",
        match_headers={"xi-api-key": "synthetic-elevenlabs-action-key"},
        headers={"request-id": "alignment-fixture-request"},
        json={
            **timings,
            "words": [
                {**entry, "audio_base64": base64.b64encode(audio_bytes).decode()}
                for entry in timings["words"]
            ],
            "characters": [{**timings["characters"][0], "private_path": str(source)}],
            "private_path": str(source),
        },
    )
    first = mcp_client.call_tool_structured(tool, arguments)["data"]
    assert source.read_bytes() == audio_bytes
    replay = _call_tool_raw(mcp_client, tool, arguments)["data"]
    (sent,) = httpx_mock.get_requests()
    assert sent.headers["content-type"].startswith("multipart/form-data;")
    assert b'name="file"; filename="private-source.wav"' in sent.content
    assert b"Content-Type: audio/wav\r\n\r\n" + audio_bytes + b"\r\n" in sent.content
    assert b'name="text"\r\n\r\n' + _ALIGNMENT_TEXT.encode() + b"\r\n" in sent.content
    assert first["action_call_id"] == replay["action_call"]["id"]
    if granted:
        assert replay["action_call"]["run_plan_step_id"] == claimed["id"]
    if mode == "file":
        pointer = replay["output_json"]["file"]
        for key in ("path", "sha256", "schema_ref", "schema_operation"):
            assert first["output"][key] == pointer[key]
        content = json.loads(Path(pointer["path"]).read_text())
        assert content["request"]["input_json"] == arguments["input_json"]
        result = content["response"]
    else:
        result = replay
    assert result["output_json"] == timings
    Draft202012Validator(validated["manifest"]["output_schema_json"]).validate(
        result["output_json"]
    )
    assert result["metadata_json"]["provider_request_id"] == "alignment-fixture-request"
    rendered = json.dumps([first, replay, result])
    for private in (
        str(source),
        source.name,
        "offline-wave-sample-42",
        base64.b64encode(audio_bytes).decode(),
        "synthetic-elevenlabs-action-key",
        "stackos-alignment-",
    ):
        assert private not in rendered


@pytest.mark.parametrize(
    ("case", "granted", "generated"),
    [
        ("missing", False, False),
        ("foreign", False, False),
        ("foreign", True, False),
        ("foreign", False, True),
        ("non-audio", False, False),
        ("unmanaged", False, False),
        ("removed", False, False),
        ("escape", False, True),
        ("outside-symlink", False, True),
        ("private-staging", False, True),
        ("caller-path", False, False),
    ],
)
def test_elevenlabs_alignment_rejects_unsafe_inputs_before_transport(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
    elevenlabs_action_account: str,
    case: str,
    granted: bool,
    generated: bool,
) -> None:
    project_id = seeded_project["data"]["id"]
    owner = project_id
    if case == "foreign":
        owner = _call_tool_raw(
            mcp_client,
            "project.create",
            {
                "slug": "other-alignment-fixture",
                "name": "Other alignment fixture",
                "domain": "other-alignment.example",
                "locale": "en-US",
            },
        )["data"]["id"]
    artifact_id, source = _seed_alignment_audio(
        mcp_settings,
        owner,
        mime_type="text/plain" if case == "non-audio" else "audio/wav",
        managed=case != "unmanaged",
        generated=generated,
    )
    if case == "removed":
        source.unlink()
    if case == "outside-symlink":
        outside = mcp_settings.data_dir / "outside-generated-assets.wav"
        outside.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(outside)
    if case in {"escape", "private-staging"}:
        uri = "/generated-assets/../../outside.wav"
        if case == "private-staging":
            staged = mcp_settings.generated_assets_dir / "imap-transfers" / "input.wav"
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(source.read_bytes())
            uri = "/generated-assets/imap-transfers/input.wav"
        engine = make_engine(mcp_settings.db_path)
        try:
            with Session(engine) as session:
                artifact = session.get(Artifact, artifact_id)
                assert artifact is not None
                artifact.uri = uri
                session.add(artifact)
                session.commit()
        finally:
            engine.dispose()
    payload = {
        "audio_artifact_id": artifact_id + 1000 if case == "missing" else artifact_id,
        "text": _ALIGNMENT_TEXT,
    }
    if case == "caller-path":
        payload["audio_path"] = str(source)
    arguments = {
        "project_id": project_id,
        "action_ref": _ALIGNMENT_REF,
        "credential_ref": elevenlabs_action_account,
        "input_json": payload,
    }
    validated = _call_tool_raw(mcp_client, "action.validate", arguments)
    assert validated["valid"] is False
    expected_path = "$.audio_path" if case == "caller-path" else "$.audio_artifact_id"
    assert any(issue["path"] == expected_path for issue in validated["issues"])
    if granted:
        started, _ = _start_elevenlabs_write_step(
            mcp_client, project_id, _ALIGNMENT_REF, "utils.elevenlabs.speech.generate"
        )
        denied = mcp_client.call_tool_error(
            "action.execute", {**arguments, "run_token": started["run_token"]}
        )
    else:
        denied = mcp_client.call_tool_error(
            "action.run",
            {
                **arguments,
                "confirm_direct": True,
                "intent_summary": "Reject invalid alignment audio.",
            },
        )
    assert denied["code"] == -32602
    assert any(issue["path"] == expected_path for issue in denied["data"]["issues"])
    assert str(source) not in json.dumps([validated, denied])
    assert httpx_mock.get_requests() == []


def test_elevenlabs_alignment_rechecks_file_after_preflight(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
    elevenlabs_action_account: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stackos.actions import ActionRepository

    project_id = seeded_project["data"]["id"]
    artifact_id, source = _seed_alignment_audio(mcp_settings, project_id)
    resolve = ActionRepository._resolve_credential

    async def replace_after_validation(self, **kwargs):
        credential = await resolve(self, **kwargs)
        source.unlink()
        return credential

    monkeypatch.setattr(ActionRepository, "_resolve_credential", replace_after_validation)
    denied = mcp_client.call_tool_error(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": _ALIGNMENT_REF,
            "credential_ref": elevenlabs_action_account,
            "input_json": {"audio_artifact_id": artifact_id, "text": _ALIGNMENT_TEXT},
            "confirm_direct": True,
            "intent_summary": "Align audio with a changed local file.",
            "idempotency_key": "alignment-race-intent",
        },
    )
    assert not source.exists()
    assert denied["code"] == -32008
    assert denied["data"]["error"] == "project audio artifact is unavailable"
    receipt = _call_tool_raw(
        mcp_client,
        "actionCall.get",
        {"project_id": project_id, "action_call_id": denied["data"]["action_call_id"]},
    )
    assert receipt["status"] == "failed"
    assert str(source) not in json.dumps([denied, receipt])
    assert httpx_mock.get_requests() == []


@pytest.mark.parametrize("operation", ["speech.generate", "voices.design"])
@pytest.mark.parametrize("granted", [False, True], ids=["direct", "granted"])
def test_elevenlabs_audio_outputs_cross_real_mcp_and_artifact_owners(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
    elevenlabs_action_account: str,
    operation: str,
    granted: bool,
) -> None:
    project_id = seeded_project["data"]["id"]
    speech = operation == "speech.generate"
    action_ref = f"utils.elevenlabs.{operation}"
    arguments = {
        "project_id": project_id,
        "action_ref": action_ref,
        "credential_ref": elevenlabs_action_account,
        "input_json": _ELEVENLABS_SPEECH if speech else _ELEVENLABS_DESIGN,
    }
    validated = _call_tool_raw(mcp_client, "action.validate", arguments)
    assert validated["valid"] is True, validated
    assert validated["manifest"]["risk_level"] == "cost"
    invalid = _call_tool_raw(
        mcp_client,
        "action.validate",
        {**arguments, "input_json": {**arguments["input_json"], "output_dir": "/tmp/choose"}},
    )
    assert invalid["valid"] is False
    if granted:
        tool = "action.execute"
        other = "utils.elevenlabs.voices.design" if speech else "utils.elevenlabs.speech.generate"
        started, claimed = _start_elevenlabs_write_step(mcp_client, project_id, action_ref, other)
        arguments["run_token"] = started["run_token"]
        denied = mcp_client.call_tool_error(
            tool,
            {
                **arguments,
                "action_ref": other,
                "input_json": _ELEVENLABS_DESIGN if speech else _ELEVENLABS_SPEECH,
            },
        )
        assert denied["code"] == -32007
    else:
        tool = "action.run"
        denied = mcp_client.call_tool_error(tool, arguments)
        assert denied["data"]["risk_level"] == "cost"
        arguments.update(
            confirm_direct=True,
            intent_summary="Generate synthetic project audio.",
            idempotency_key="elevenlabs-audio-intent",
        )
    assert httpx_mock.get_requests() == []
    assert (
        mcp_client.call_tool_structured("artifact.query", {"project_id": project_id})["items"] == []
    )

    _mock_elevenlabs_audio(httpx_mock, operation)
    first = _call_tool_raw(mcp_client, tool, arguments)["data"]
    replay = _call_tool_raw(mcp_client, tool, arguments)["data"]
    assert len(httpx_mock.get_requests()) == 1
    assert first["action_call"]["id"] == replay["action_call"]["id"]
    assert first["output_json"] == replay["output_json"]
    if granted:
        assert first["action_call"]["run_plan_step_id"] == claimed["id"]
    pointer = first["output_json"]["file"]
    content = json.loads(Path(pointer["path"]).read_text())
    output = content["response"]["output_json"]
    Draft202012Validator(validated["manifest"]["output_schema_json"]).validate(output)
    assert content["response"]["metadata_json"]["provider_request_id"] == "request-123"
    if speech:
        assert output == {
            "output_format": "wav_44100",
            **_ELEVENLABS_TIMINGS,
            "artifact_id": output["artifact_id"],
        }
        files = [(output["artifact_id"], "audio/wav", b"synthetic-wav", None)]
    else:
        assert output["status"] == "partial"
        assert output["text"] == "The provider's script"
        first_preview, middle, last = output["previews"]
        assert middle == {
            "generated_voice_id": "preview-middle",
            "duration_secs": 2.3,
            "media_type": "audio/mpeg",
            "language": "en",
            "reason_code": "invalid_preview",
        }
        assert (first_preview["file_index"], last["file_index"]) == (0, 1)
        assert (first_preview["generated_voice_id"], last["generated_voice_id"]) == (
            "preview-first",
            "preview-last",
        )
        files = [
            (first_preview["artifact_id"], "audio/mpeg", b"first", "preview-first"),
            (last["artifact_id"], "audio/ogg", b"last", "preview-last"),
        ]
    items = mcp_client.call_tool_structured("artifact.query", {"project_id": project_id})["items"]
    assert {item["id"] for item in items} == {file[0] for file in files}
    rendered = json.dumps([first, content, items])
    for private in (
        "synthetic-elevenlabs-action-key",
        _ELEVENLABS_DESIGN["reference_audio_base64"],
        "c3ludGhldGljLXdhdg==",
        "audio_base_64",
        '"audio_base64"',
    ):
        assert private not in rendered
    for artifact_id, mime, expected_bytes, voice_id in files:
        assert type(artifact_id) is int
        artifact = _call_tool_raw(
            mcp_client,
            "artifact.get",
            {"artifact_id": artifact_id, "project_id": project_id},
        )
        assert artifact["mime_type"] == mime
        assert artifact["kind"] == "audio"
        assert artifact["size_bytes"] == len(expected_bytes)
        assert artifact["metadata_json"].get("generated_voice_id") == voice_id
        read = _call_tool_raw(
            mcp_client, "artifact.read", {"artifact_id": artifact_id, "project_id": project_id}
        )
        assert read["artifact"] == artifact
        assert read["content_available"] is False
        assert read["content_type"] == mime
        assert read["bytes"] == len(expected_bytes)
        assert "content" not in read
        assert artifact["uri"].startswith("/generated-assets/")
        path = _stored_elevenlabs_audio(mcp_settings, artifact_id, project_id)
        assert path.read_bytes() == expected_bytes
        assert read["path"] == str(path)
        assert str(path) not in rendered


def test_elevenlabs_workflow_grant_restricts_mutation_before_transport(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    elevenlabs_action_account: str,
) -> None:
    project_id = seeded_project["data"]["id"]
    add_ref = "utils.elevenlabs.library.add"
    create_ref = "utils.elevenlabs.voices.create"
    started, claimed = _start_elevenlabs_write_step(mcp_client, project_id)
    arguments = {
        "project_id": project_id,
        "credential_ref": elevenlabs_action_account,
        "run_token": started["run_token"],
    }
    denied = mcp_client.call_tool_error(
        "action.execute",
        {
            **arguments,
            "action_ref": create_ref,
            "input_json": _ELEVENLABS_VOICE_CREATE,
        },
    )
    assert denied["code"] == -32007
    assert "arguments do not match" in denied["data"]["detail"]
    assert httpx_mock.get_requests() == []

    httpx_mock.add_response(
        method="POST",
        url="https://api.elevenlabs.io/v1/voices/add/owner-id/shared-id",
        match_json={"new_name": "Reference narrator"},
        json={"voice_id": "granted-id"},
    )
    out = _call_tool_raw(
        mcp_client,
        "action.execute",
        {**arguments, "action_ref": add_ref, "input_json": _ELEVENLABS_LIBRARY_ADD},
    )["data"]
    pointer = out["output_json"]["file"]
    assert pointer["schema_ref"] == "stackos.action-output.v1"
    assert pointer["schema_operation"] == "schema.get"
    receipt = json.loads(Path(pointer["path"]).read_text(encoding="utf-8"))
    assert receipt["request"]["input_json"] == _ELEVENLABS_LIBRARY_ADD
    assert receipt["response"]["output_json"] == {"voice_id": "granted-id"}
    assert out["action_call"]["run_id"] == started["run_id"]
    assert out["action_call"]["run_plan_step_id"] == claimed["id"]
    httpx_mock.add_response(
        method="POST",
        url="https://api.elevenlabs.io/v1/voices/add/owner-id/shared-id",
        match_json={"new_name": "Reference narrator"},
        status_code=503,
        json={"detail": "provider failed"},
        is_reusable=True,
    )
    failed = mcp_client.call_tool_error(
        "action.execute",
        {
            **arguments,
            "action_ref": add_ref,
            "input_json": _ELEVENLABS_LIBRARY_ADD,
            "idempotency_key": "another-granted-evidence-intent",
        },
    )
    assert failed["data"]["provider_status_code"] == 503
    assert len(httpx_mock.get_requests()) == 2


def _create_xai_credential(mcp: MCPClient, project_id: int) -> str:
    return _create_account(
        mcp,
        project_id,
        provider_key="xai-imagine",
        auth_method_key="api_key",
        fields={"api_key": "xai-key"},
    )


def _create_google_veo_credential(mcp: MCPClient, project_id: int) -> str:
    return _create_account(
        mcp,
        project_id,
        provider_key="google-veo",
        auth_method_key="api_key",
        fields={"api_key": "gemini-key"},
    )


def _create_reve_credential(mcp: MCPClient, project_id: int) -> str:
    return _create_account(
        mcp,
        project_id,
        provider_key="reve",
        auth_method_key="api_key",
        fields={"api_key": "reve-key"},
    )


def _create_google_gemini_image_credential(mcp: MCPClient, project_id: int) -> str:
    return _create_account(
        mcp,
        project_id,
        provider_key="google-gemini-image",
        auth_method_key="api_key",
        fields={"api_key": "gemini-key"},
    )


def _create_ideogram_credential(mcp: MCPClient, project_id: int) -> str:
    return _create_account(
        mcp,
        project_id,
        provider_key="ideogram",
        auth_method_key="api_key",
        fields={"api_key": "ideo-key"},
    )


def _create_byteplus_ark_credential(mcp: MCPClient, project_id: int) -> str:
    return _create_account(
        mcp,
        project_id,
        provider_key="byteplus-ark",
        auth_method_key="api_key",
        fields={"api_key": "ark-key"},
    )


def _create_alibaba_wan_credential(mcp: MCPClient, project_id: int) -> str:
    return _create_account(
        mcp,
        project_id,
        provider_key="alibaba-wan",
        auth_method_key="api_key",
        fields={"api_key": "dashscope-key"},
    )


def _create_kling_credential(mcp: MCPClient, project_id: int) -> str:
    return _create_account(
        mcp,
        project_id,
        provider_key="kling",
        auth_method_key="access_key_secret",
        fields={"access_key": "ak-test", "secret_key": "sk-test"},
    )


def _create_firecrawl_credential(mcp: MCPClient, project_id: int) -> str:
    return _create_account(
        mcp,
        project_id,
        provider_key="firecrawl",
        auth_method_key="api_key",
        fields={"api_key": "fc-key"},
    )


def _create_dataforseo_credential(mcp: MCPClient, project_id: int) -> str:
    return _create_account(
        mcp,
        project_id,
        provider_key="dataforseo",
        auth_method_key="basic",
        fields={"login": "login@example.com", "password": "password"},
    )


def _create_serper_credential(mcp: MCPClient, project_id: int) -> str:
    return _create_account(
        mcp,
        project_id,
        provider_key="serper",
        auth_method_key="api_key",
        fields={"api_key": "serper-key"},
    )


def _create_wordpress_credential(mcp: MCPClient, project_id: int) -> str:
    return _create_account(
        mcp,
        project_id,
        provider_key="wordpress",
        auth_method_key="application_password",
        fields={
            "username": "editor",
            "application_password": "app pass",
            "wp_url": "https://wp.example",
        },
    )


def _create_ghost_credential(mcp: MCPClient, project_id: int) -> str:
    return _create_account(
        mcp,
        project_id,
        provider_key="ghost",
        auth_method_key="admin_api_key",
        fields={
            "admin_api_key": "keyid:00112233445566778899aabbccddeeff",
            "ghost_url": "https://ghost.example",
            "api_version": "v5.0",
        },
    )


def _create_mock_credential(
    mcp: MCPClient, project_id: int, secret: str = "mock-mcp-secret"
) -> str:
    return _create_account(
        mcp,
        project_id,
        provider_key="mock-provider",
        auth_method_key="api_key",
        display_name="Mock MCP Primary",
        fields={"api_key": secret},
    )


def _image_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.image-action.run",
        "title": "Image action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "generate-image",
                    "tool": "action.execute",
                    "action_refs": ["utils.image.generate"],
                }
            ]
        },
        "steps": [
            {
                "id": "generate-image",
                "title": "Generate image",
                "action_refs": ["utils.image.generate"],
            }
        ],
    }


def _xai_video_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.xai-video-action.run",
        "title": "xAI video action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "generate-xai-video",
                    "tool": "action.execute",
                    "action_refs": ["utils.xai.video.generate"],
                }
            ]
        },
        "steps": [
            {
                "id": "generate-xai-video",
                "title": "Generate xAI video",
                "action_refs": ["utils.xai.video.generate"],
            }
        ],
    }


def _single_video_action_plan_json(action_ref: str, step_id: str) -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": f"{action_ref}.run",
        "title": f"{action_ref} action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": step_id,
                    "tool": "action.execute",
                    "action_refs": [action_ref],
                }
            ]
        },
        "steps": [
            {
                "id": step_id,
                "title": f"Execute {action_ref}",
                "action_refs": [action_ref],
            }
        ],
    }


def _reve_image_action_plan_json() -> dict:
    action_refs = [
        "utils.reve.image.generate",
        "utils.reve.image.edit",
        "utils.reve.image.remix",
    ]
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.reve-image-action.run",
        "title": "Reve image action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "generate-reve-image",
                    "tool": "action.execute",
                    "action_refs": action_refs,
                }
            ]
        },
        "steps": [
            {
                "id": "generate-reve-image",
                "title": "Generate Reve image",
                "action_refs": action_refs,
            }
        ],
    }


def _google_gemini_image_action_plan_json() -> dict:
    action_refs = [
        "utils.google.image.generate",
        "utils.google.image.edit",
    ]
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.google-gemini-image-action.run",
        "title": "Google Gemini image action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "generate-google-gemini-image",
                    "tool": "action.execute",
                    "action_refs": action_refs,
                }
            ]
        },
        "steps": [
            {
                "id": "generate-google-gemini-image",
                "title": "Generate Google Gemini image",
                "action_refs": action_refs,
            }
        ],
    }


def _ideogram_image_action_plan_json() -> dict:
    action_refs = [
        "utils.ideogram.image.generate",
        "utils.ideogram.image.remix",
    ]
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.ideogram-image-action.run",
        "title": "Ideogram image action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "generate-ideogram-image",
                    "tool": "action.execute",
                    "action_refs": action_refs,
                }
            ]
        },
        "steps": [
            {
                "id": "generate-ideogram-image",
                "title": "Generate Ideogram image",
                "action_refs": action_refs,
            }
        ],
    }


def _byteplus_seedream_image_action_plan_json() -> dict:
    action_refs = [
        "utils.byteplus.image.generate",
        "utils.byteplus.image.edit",
    ]
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.byteplus-seedream-image-action.run",
        "title": "BytePlus Seedream image action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "generate-byteplus-seedream-image",
                    "tool": "action.execute",
                    "action_refs": action_refs,
                }
            ]
        },
        "steps": [
            {
                "id": "generate-byteplus-seedream-image",
                "title": "Generate BytePlus Seedream image",
                "action_refs": action_refs,
            }
        ],
    }


def _image_edit_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.image-edit-action.run",
        "title": "Image edit action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "edit-image",
                    "tool": "action.execute",
                    "action_refs": ["utils.image.edit"],
                }
            ]
        },
        "steps": [
            {
                "id": "edit-image",
                "title": "Edit image",
                "action_refs": ["utils.image.edit"],
            }
        ],
    }


def _firecrawl_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.firecrawl-action.run",
        "title": "Firecrawl action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "scrape-page",
                    "tool": "action.execute",
                    "action_refs": ["utils.web.scrape"],
                }
            ]
        },
        "steps": [
            {
                "id": "scrape-page",
                "title": "Scrape page",
                "action_refs": ["utils.web.scrape"],
            }
        ],
    }


def _sitemap_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.sitemap-action.run",
        "title": "Sitemap action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "fetch-sitemap",
                    "tool": "action.execute",
                    "action_refs": ["utils.sitemap.fetch"],
                }
            ]
        },
        "steps": [
            {
                "id": "fetch-sitemap",
                "title": "Fetch sitemap",
                "action_refs": ["utils.sitemap.fetch"],
            }
        ],
    }


def _mock_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.mock-provider.run",
        "title": "Mock provider action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "execute-mock",
                    "tool": "action.execute",
                    "action_refs": ["utils.mock.echo"],
                }
            ]
        },
        "steps": [
            {
                "id": "execute-mock",
                "title": "Execute mock provider",
                "action_refs": ["utils.mock.echo"],
            }
        ],
    }


def _dataforseo_paa_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "seo.paa-action.run",
        "title": "PAA action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "extract-paa",
                    "tool": "action.execute",
                    "action_refs": ["seo.paa.extract"],
                }
            ]
        },
        "steps": [
            {
                "id": "extract-paa",
                "title": "Extract PAA",
                "action_refs": ["seo.paa.extract"],
            }
        ],
    }


def _wordpress_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "publishing.wordpress-action.run",
        "title": "WordPress action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "create-wordpress-post",
                    "tool": "action.execute",
                    "action_refs": ["publishing.wordpress.post.create"],
                }
            ]
        },
        "steps": [
            {
                "id": "create-wordpress-post",
                "title": "Create WordPress post",
                "action_refs": ["publishing.wordpress.post.create"],
            }
        ],
    }


def _ghost_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "publishing.ghost-action.run",
        "title": "Ghost action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "create-ghost-post",
                    "tool": "action.execute",
                    "action_refs": ["publishing.ghost.post.create"],
                }
            ]
        },
        "steps": [
            {
                "id": "create-ghost-post",
                "title": "Create Ghost post",
                "action_refs": ["publishing.ghost.post.create"],
            }
        ],
    }


def test_action_execute_requires_run_plan_grant(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    err = mcp_client.call_tool_error(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.image.generate",
            "input_json": {"prompt": "editorial hero"},
        },
    )

    assert err["code"] == -32007
    assert err["data"]["tool"] == "action.execute"


def test_action_execute_requires_step_action_ref(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    plan = _image_action_plan_json()
    plan["steps"][0]["action_refs"] = ["utils.web.scrape"]
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": plan},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "generate-image",
            "run_token": run_token,
        },
    )

    err = mcp_client.call_tool_error(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.image.generate",
            "input_json": {"prompt": "editorial hero"},
            "run_token": run_token,
        },
    )

    assert err["code"] == -32007
    assert "declared on the active step" in err["data"]["detail"]


def test_action_execute_requires_matching_grant_action_ref(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    plan = _image_action_plan_json()
    plan["grants"]["mcp_tool_grants"][0]["action_refs"] = ["utils.web.scrape"]
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": plan},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "generate-image",
            "run_token": run_token,
        },
    )

    err = mcp_client.call_tool_error(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.image.generate",
            "input_json": {"prompt": "editorial hero"},
            "run_token": run_token,
        },
    )

    assert err["code"] == -32007
    assert "arguments do not match" in err["data"]["detail"]


def test_action_execute_mock_provider_vertical_slice_through_mcp(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_mock_credential(mcp_client, project_id)
    described = _call_tool_raw(
        mcp_client,
        "action.describe",
        {"project_id": project_id, "action_ref": "utils.mock.echo"},
    )
    assert described["availability"]["status"] == "ready"
    assert described["availability"]["credential_state"] == "available"

    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _mock_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "execute-mock",
            "run_token": run_token,
        },
    )

    out = _call_action_execute_raw(
        mcp_client,
        {
            "project_id": project_id,
            "action_ref": "utils.mock.echo",
            "credential_ref": credential_ref,
            "run_token": run_token,
            "input_json": {
                "message": "hello from mcp mock provider",
                "echo": {"campaign": "mock-campaign"},
                "cost_cents": 11,
            },
        },
    )

    data = out["data"]
    rendered = json.dumps(data)
    assert data["action_call"]["run_id"] == started["data"]["run_id"]
    assert data["action_call"]["run_plan_id"] == created["data"]["id"]
    assert data["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
    assert data["action_call"]["provider_key"] == "mock-provider"
    assert data["action_call"]["connector_key"] == "mock-provider"
    assert data["output_json"]["status"] == "success"
    assert data["output_json"]["credential_ref"] == credential_ref
    assert data["output_json"]["leak_check"] == {
        "authorization": "[redacted]",
        "api_key": "[redacted]",
    }
    assert data["metadata_json"]["access_token"] == "[redacted]"
    assert data["cost_cents"] == 11
    assert "mock-mcp-secret" not in rendered

    audit_resp = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={
            "run_id": started["data"]["run_id"],
            "run_plan_id": created["data"]["id"],
            "run_plan_step_id": claimed["data"]["id"],
            "status": "success",
        },
        headers=mcp_client._headers(),
    )
    assert audit_resp.status_code == 200
    audit = audit_resp.json()
    assert audit["total_estimate"] == 1
    assert "mock-mcp-secret" not in json.dumps(audit)


def test_action_execute_manifest_default_transient_read_keeps_step_audit_without_replay(
    mcp_client: MCPClient,
    mcp_settings: Settings,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_mock_credential(mcp_client, project_id)
    engine = make_engine(mcp_settings.db_path)
    try:
        with Session(engine) as session:
            action = session.exec(select(Action).where(Action.key == "mock.echo")).one()
            action.config_json = {
                **action.config_json,
                "default_output_policy_json": {"mode": "transient"},
            }
            session.add(action)
            session.commit()
    finally:
        engine.dispose()
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _mock_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "execute-mock",
            "run_token": run_token,
        },
    )
    arguments = {
        "project_id": project_id,
        "action_ref": "utils.mock.echo",
        "credential_ref": credential_ref,
        "run_token": run_token,
        "input_json": {"message": "private workflow read"},
    }
    compact_error = mcp_client.call_tool_error("action.execute", arguments)
    assert compact_error["code"] == -32602
    key_error = mcp_client.call_tool_error(
        "action.execute",
        {**arguments, "response_mode": "raw", "idempotency_key": "explicit-replay-key"},
    )
    assert key_error["code"] == -32602

    out = _call_tool_raw(mcp_client, "action.execute", arguments)
    data = out["data"]
    assert data["output_json"]["message"] == "private workflow read"
    assert data["action_call"]["run_id"] == started["data"]["run_id"]
    assert data["action_call"]["run_plan_id"] == created["data"]["id"]
    assert data["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
    assert data["action_call"]["response_json"]["result_available"] is False
    engine = make_engine(mcp_settings.db_path)
    try:
        with Session(engine) as session:
            calls = session.exec(
                select(ActionCall).where(ActionCall.project_id == project_id)
            ).all()
            assert len(calls) == 1
            assert calls[0].idempotency_key is None
            assert calls[0].response_json == data["action_call"]["response_json"]
    finally:
        engine.dispose()


def test_action_run_direct_mock_provider_returns_file_pointer_by_default(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_mock_credential(mcp_client, project_id, secret="direct-secret")

    out = mcp_client.call_tool_structured(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "utils.mock.echo",
            "credential_ref": credential_ref,
            "input_json": {
                "message": "hello direct action" + ("." * 600),
                "echo": {"kind": "smoke"},
                "cost_cents": 3,
            },
            "idempotency_key": "direct-mock-provider-1",
        },
    )

    data = out["data"]
    rendered = json.dumps(out)
    assert data["status"] == "success"
    assert data["action_ref"] == "utils.mock.echo"
    assert data["provider_key"] == "mock-provider"
    assert data["operation"] == "echo"
    assert data["output"]["output_mode"] == "file"
    assert data["output"]["schema_ref"] == "stackos.action-output.v1"
    assert data["cost_cents"] == 3
    saved = json.loads(Path(data["output"]["path"]).read_text(encoding="utf-8"))
    assert saved["response"]["output_json"]["message"].startswith("hello direct action")
    assert "direct-secret" not in rendered


def test_action_run_transient_read_is_immediate_only_with_audit_receipt(
    mcp_client: MCPClient,
    mcp_settings: Settings,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_mock_credential(mcp_client, project_id, secret="transient-secret")
    asset_dir = mcp_settings.generated_assets_dir
    files_before = {str(path) for path in asset_dir.rglob("*") if path.is_file()}

    out = mcp_client.call_tool_structured(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "utils.mock.echo",
            "credential_ref": credential_ref,
            "input_json": {"message": "private inbound sample"},
            "output_policy_json": {"mode": "transient"},
            "response_mode": "raw",
        },
    )

    data = out["data"]
    assert data["status"] == "success"
    assert data["output_json"]["message"] == "private inbound sample"
    assert data["action_call"]["response_json"]["output_mode"] == "transient"
    assert data["action_call"]["response_json"]["result_available"] is False
    assert {str(path) for path in asset_dir.rglob("*") if path.is_file()} == files_before
    polled = _call_tool_raw(
        mcp_client,
        "actionCall.get",
        {"project_id": project_id, "action_call_id": data["action_call_id"]},
    )
    assert polled["output_json"] == data["action_call"]["response_json"]
    assert "private inbound sample" not in json.dumps(polled)
    engine = make_engine(mcp_settings.db_path)
    try:
        with Session(engine) as session:
            row = session.get(ActionCall, data["action_call_id"])
            assert row is not None
            assert row.idempotency_key is None
            assert row.response_json == polled["output_json"]
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "extra",
    [
        {"response_mode": "compact"},
        {"response_mode": "raw", "idempotency_key": "cannot-replay"},
        {"response_mode": "raw", "intent_id": "cannot-replay"},
    ],
)
def test_action_run_transient_rejects_non_raw_or_replay_before_dispatch(
    mcp_client: MCPClient,
    mcp_settings: Settings,
    seeded_project: dict,
    extra: dict[str, str],
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_mock_credential(mcp_client, project_id)
    err = mcp_client.call_tool_error(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "utils.mock.echo",
            "credential_ref": credential_ref,
            "input_json": {"message": "never dispatched"},
            "output_policy_json": {"mode": "transient"},
            **extra,
        },
    )
    assert err["code"] == -32602, err
    engine = make_engine(mcp_settings.db_path)
    try:
        with Session(engine) as session:
            calls = session.exec(
                select(ActionCall).where(ActionCall.project_id == project_id)
            ).all()
            assert not calls
    finally:
        engine.dispose()


def test_action_run_verbose_includes_redacted_full_payload(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_mock_credential(mcp_client, project_id, secret="verbose-secret")

    out = mcp_client.call_tool_structured(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "utils.mock.echo",
            "credential_ref": credential_ref,
            "input_json": {"message": "hello verbose"},
            "output_policy_json": {"mode": "inline"},
            "verbose": True,
            "response_mode": "raw",
        },
    )

    data = out["data"]
    rendered = json.dumps(out)
    assert data["action_call"]["provider_key"] == "mock-provider"
    assert data["output_json"]["leak_check"] == {
        "authorization": "[redacted]",
        "api_key": "[redacted]",
    }
    assert "verbose-secret" not in rendered


def test_action_run_rejects_non_read_without_direct_confirmation(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    err = mcp_client.call_tool_error(
        "action.run",
        {
            "project_id": seeded_project["data"]["id"],
            "action_ref": "publishing.wordpress.post.create",
            "input_json": {"post": {"title": "x", "content": "x"}},
        },
    )

    assert err["code"] == -32602
    assert "confirm_direct=true" in err["data"]["detail"]


def test_action_run_derives_idempotency_for_confirmed_non_read(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_wordpress_credential(mcp_client, project_id)
    httpx_mock.add_response(
        method="POST",
        url="https://wp.example/wp-json/wp/v2/posts",
        json={"id": 55, "link": "https://wp.example/direct/"},
    )

    out = mcp_client.call_tool_structured(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "publishing.wordpress.post.create",
            "input_json": {"post": {"title": "x", "content": "x", "status": "draft"}},
            "credential_ref": credential_ref,
            "confirm_direct": True,
            "intent_summary": "User asked to create one post directly.",
            "output_policy_json": {"mode": "inline"},
            "response_mode": "raw",
        },
    )

    data = out["data"]
    assert data["status"] == "success"
    assert data["compact"]["id"] == 55
    assert data["action_call"]["provider_key"] == "wordpress"
    assert data["output_json"]["id"] == 55


def test_action_execute_openai_images_grant_returns_sanitized_artifact_refs(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_openai_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "openai-images", "monthly_budget_usd": 10.0},
        headers={"authorization": f"Bearer {mcp_client.auth_token}"},
    )
    assert budget_resp.status_code == 200
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _image_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "generate-image",
            "run_token": run_token,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/images/generations",
        json={"data": [{"b64_json": base64.b64encode(b"webp").decode("ascii")}]},
    )

    out = _call_action_execute_raw(
        mcp_client,
        {
            "project_id": project_id,
            "action_ref": "utils.image.generate",
            "input_json": {"prompt": "editorial hero", "n": 1},
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )

    data = out["data"]
    item = data["output_json"]["data"][0]
    rendered = json.dumps(data)
    assert data["credential_ref"] == credential_ref
    assert data["action_call"]["credential_ref"] == credential_ref
    assert data["action_call"]["run_id"] == started["data"]["run_id"]
    assert data["action_call"]["run_plan_id"] == created["data"]["id"]
    assert data["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
    assert item["url"].startswith("/generated-assets/openai-images/openai-")
    assert item["artifact_ref"] == item["url"]
    assert isinstance(item["artifact_id"], int)
    assert data["output_json"]["artifact_refs"] == [item["url"]]
    assert "b64_json" not in item
    assert "credential_id" not in rendered
    assert "sk-openai" not in rendered
    assert "Authorization" not in rendered
    path = mcp_settings.generated_assets_dir / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"webp"
    artifacts = mcp_client.call_tool_structured(
        "artifact.query",
        {"project_id": project_id, "kind": "image"},
    )
    artifact_rows = [row for row in artifacts["items"] if row["id"] == item["artifact_id"]]
    assert artifact_rows
    artifact = mcp_client.call_tool_structured(
        "artifact.get",
        {"artifact_id": item["artifact_id"], "response_mode": "raw"},
    )
    assert artifact["uri"] == item["url"]
    assert artifact["plugin_slug"] == "utils"


def test_action_execute_xai_video_grant_returns_sanitized_artifact_refs(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_xai_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "xai-imagine", "monthly_budget_usd": 10.0},
        headers=mcp_client._headers(),
    )
    assert budget_resp.status_code == 200
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _xai_video_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "generate-xai-video",
            "run_token": run_token,
        },
    )
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

    out = _call_action_execute_raw(
        mcp_client,
        {
            "project_id": project_id,
            "action_ref": "utils.xai.video.generate",
            "input_json": {
                "prompt": "editorial product motion",
                "duration": 5,
                "resolution": "480p",
                "poll_interval_seconds": 1,
                "poll_timeout_seconds": 60,
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )

    data = out["data"]
    item = data["output_json"]["data"][0]
    rendered = json.dumps(data)
    assert data["credential_ref"] == credential_ref
    assert data["action_call"]["credential_ref"] == credential_ref
    assert data["action_call"]["provider_key"] == "xai-imagine"
    assert data["action_call"]["connector_key"] == "xai-imagine"
    assert data["action_call"]["run_id"] == started["data"]["run_id"]
    assert data["action_call"]["run_plan_id"] == created["data"]["id"]
    assert data["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
    assert data["cost_cents"] == 31
    assert data["output_json"]["request_id"] == "req_123"
    assert item["url"].startswith("/generated-assets/xai-imagine/xai-video-")
    assert item["artifact_ref"] == item["url"]
    assert isinstance(item["artifact_id"], int)
    assert data["output_json"]["artifact_refs"] == [item["url"]]
    assert "https://cdn.x.ai/video.mp4" not in rendered
    assert "credential_id" not in rendered
    assert "xai-key" not in rendered
    path = mcp_settings.generated_assets_dir / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"video-bytes"
    artifacts = mcp_client.call_tool_structured(
        "artifact.query",
        {"project_id": project_id, "kind": "video"},
    )
    assert any(row["id"] == item["artifact_id"] for row in artifacts["items"])


@pytest.mark.usefixtures("_media_provider_clock")
def test_action_execute_provider_video_grants_return_sanitized_artifact_refs(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
) -> None:
    project_id = seeded_project["data"]["id"]

    cases = [
        {
            "action_ref": "utils.google.video.generate",
            "step_id": "generate-google-video",
            "provider": "google-veo",
            "connector": "google-veo",
            "budget_kind": "google-veo",
            "credential": _create_google_veo_credential,
            "input": {
                "prompt": "cinematic product motion",
                "duration_seconds": 6,
                "resolution": "720p",
                "poll_interval_seconds": 1,
                "poll_timeout_seconds": 60,
            },
            "asset_prefix": "/generated-assets/google-veo/google-veo-video-",
            "provider_url": "https://generativelanguage.googleapis.com/download/mcp-video",
            "bytes": b"google-veo-video",
            "mock": lambda: (
                httpx_mock.add_response(
                    method="POST",
                    url=(
                        "https://generativelanguage.googleapis.com/v1beta/models/"
                        "veo-3.1-generate-preview:predictLongRunning"
                    ),
                    json={"name": "operations/mcp-veo"},
                ),
                httpx_mock.add_response(
                    method="GET",
                    url="https://generativelanguage.googleapis.com/v1beta/operations/mcp-veo",
                    json={
                        "name": "operations/mcp-veo",
                        "done": True,
                        "response": {
                            "generateVideoResponse": {
                                "generatedSamples": [
                                    {
                                        "video": {
                                            "uri": (
                                                "https://generativelanguage.googleapis.com/"
                                                "download/mcp-video"
                                            )
                                        }
                                    }
                                ]
                            }
                        },
                    },
                ),
                httpx_mock.add_response(
                    method="GET",
                    url="https://generativelanguage.googleapis.com/download/mcp-video",
                    content=b"google-veo-video",
                    headers={"content-type": "video/mp4"},
                ),
            ),
            "secret": "gemini-key",
        },
        {
            "action_ref": "utils.byteplus.video.generate",
            "step_id": "generate-byteplus-video",
            "provider": "byteplus-ark",
            "connector": "byteplus-seedance",
            "budget_kind": "byteplus-ark",
            "credential": _create_byteplus_ark_credential,
            "input": {
                "prompt": "cinematic product motion",
                "duration": 5,
                "poll_interval_seconds": 1,
                "poll_timeout_seconds": 60,
            },
            "asset_prefix": "/generated-assets/byteplus-ark/byteplus-seedance-video-",
            "provider_url": "https://ark-output.example/mcp-video.mp4",
            "bytes": b"byteplus-seedance-video",
            "mock": lambda: (
                httpx_mock.add_response(
                    method="POST",
                    url=(
                        "https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks"
                    ),
                    json={"id": "cgt-mcp"},
                ),
                httpx_mock.add_response(
                    method="GET",
                    url=(
                        "https://ark.ap-southeast.bytepluses.com/api/v3/"
                        "contents/generations/tasks/cgt-mcp"
                    ),
                    json={
                        "id": "cgt-mcp",
                        "status": "succeeded",
                        "content": {"video_url": "https://ark-output.example/mcp-video.mp4"},
                    },
                ),
                httpx_mock.add_response(
                    method="GET",
                    url="https://ark-output.example/mcp-video.mp4",
                    content=b"byteplus-seedance-video",
                    headers={"content-type": "video/mp4"},
                ),
            ),
            "secret": "ark-key",
        },
        {
            "action_ref": "utils.alibaba.video.generate",
            "step_id": "generate-alibaba-video",
            "provider": "alibaba-wan",
            "connector": "alibaba-wan",
            "budget_kind": "alibaba-wan",
            "credential": _create_alibaba_wan_credential,
            "input": {
                "prompt": "cinematic product motion",
                "duration": 5,
                "poll_interval_seconds": 1,
                "poll_timeout_seconds": 60,
            },
            "asset_prefix": "/generated-assets/alibaba-wan/alibaba-wan-video-",
            "provider_url": "https://dashscope-result.example/mcp-video.mp4",
            "bytes": b"alibaba-wan-video",
            "mock": lambda: (
                httpx_mock.add_response(
                    method="POST",
                    url=(
                        "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/"
                        "video-generation/video-synthesis"
                    ),
                    json={"output": {"task_id": "task_mcp", "task_status": "PENDING"}},
                ),
                httpx_mock.add_response(
                    method="GET",
                    url="https://dashscope-intl.aliyuncs.com/api/v1/tasks/task_mcp",
                    json={
                        "output": {
                            "task_id": "task_mcp",
                            "task_status": "SUCCEEDED",
                            "video_url": "https://dashscope-result.example/mcp-video.mp4",
                        }
                    },
                ),
                httpx_mock.add_response(
                    method="GET",
                    url="https://dashscope-result.example/mcp-video.mp4",
                    content=b"alibaba-wan-video",
                    headers={"content-type": "video/mp4"},
                ),
            ),
            "secret": "dashscope-key",
        },
        {
            "action_ref": "utils.kling.video.generate",
            "step_id": "generate-kling-video",
            "provider": "kling",
            "connector": "kling-video",
            "budget_kind": "kling",
            "credential": _create_kling_credential,
            "input": {
                "prompt": "cinematic product motion",
                "duration": 5,
                "poll_interval_seconds": 1,
                "poll_timeout_seconds": 60,
            },
            "asset_prefix": "/generated-assets/kling/kling-video-",
            "provider_url": "https://kling.example/mcp-video.mp4",
            "bytes": b"kling-video",
            "mock": lambda: (
                httpx_mock.add_response(
                    method="POST",
                    url="https://api-singapore.klingai.com/v1/videos/text2video",
                    json={
                        "code": 0,
                        "data": {"task_id": "task-mcp", "task_status": "submitted"},
                    },
                ),
                httpx_mock.add_response(
                    method="GET",
                    url="https://api-singapore.klingai.com/v1/videos/text2video/task-mcp",
                    json={
                        "code": 0,
                        "data": {
                            "task_id": "task-mcp",
                            "task_status": "succeed",
                            "task_result": {
                                "videos": [
                                    {
                                        "id": "video-mcp",
                                        "url": "https://kling.example/mcp-video.mp4",
                                    }
                                ]
                            },
                        },
                    },
                ),
                httpx_mock.add_response(
                    method="GET",
                    url="https://kling.example/mcp-video.mp4",
                    content=b"kling-video",
                    headers={"content-type": "video/mp4"},
                ),
            ),
            "secret": "sk-test",
        },
    ]

    for case in cases:
        credential_ref = case["credential"](mcp_client, project_id)
        budget_resp = mcp_client.test_client.post(
            f"/api/v1/projects/{project_id}/budgets",
            json={"kind": case["budget_kind"], "monthly_budget_usd": 10.0},
            headers=mcp_client._headers(),
        )
        assert budget_resp.status_code == 200
        created = mcp_client.call_tool_structured(
            "runPlan.create",
            {
                "project_id": project_id,
                "run_plan_json": _single_video_action_plan_json(
                    case["action_ref"],
                    case["step_id"],
                ),
            },
        )
        started = mcp_client.call_tool_structured(
            "runPlan.start",
            {"project_id": project_id, "run_plan_id": created["data"]["id"]},
        )
        run_token = started["data"]["run_token"]
        claimed = mcp_client.call_tool_structured(
            "runPlan.claimStep",
            {
                "run_plan_id": created["data"]["id"],
                "step_id": case["step_id"],
                "run_token": run_token,
            },
        )
        case["mock"]()

        out = _call_action_execute_raw(
            mcp_client,
            {
                "project_id": project_id,
                "action_ref": case["action_ref"],
                "input_json": case["input"],
                "credential_ref": credential_ref,
                "run_token": run_token,
            },
        )

        data = out["data"]
        assert "output_json" in data, data
        item = data["output_json"]["data"][0]
        rendered = json.dumps(data)
        assert data["credential_ref"] == credential_ref
        assert data["action_call"]["credential_ref"] == credential_ref
        assert data["action_call"]["provider_key"] == case["provider"]
        assert data["action_call"]["connector_key"] == case["connector"]
        assert data["action_call"]["run_id"] == started["data"]["run_id"]
        assert data["action_call"]["run_plan_id"] == created["data"]["id"]
        assert data["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
        assert item["url"].startswith(case["asset_prefix"])
        assert item["artifact_ref"] == item["url"]
        assert isinstance(item["artifact_id"], int)
        assert data["output_json"]["artifact_refs"] == [item["url"]]
        assert case["provider_url"] not in rendered
        assert case["secret"] not in rendered
        assert "credential_id" not in rendered
        path = mcp_settings.generated_assets_dir / item["url"].removeprefix("/generated-assets/")
        assert path.read_bytes() == case["bytes"]
        step_calls = mcp_client.call_tool_structured(
            "run.listStepCalls",
            {"run_step_id": claimed["data"]["id"]},
        )
        rendered_step_calls = json.dumps(step_calls)
        assert case["provider_url"] not in rendered_step_calls
        assert case["secret"] not in rendered_step_calls


def test_action_execute_reve_image_grant_returns_sanitized_artifact_refs(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_reve_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "reve", "monthly_budget_usd": 10.0},
        headers=mcp_client._headers(),
    )
    assert budget_resp.status_code == 200
    input_dir = mcp_settings.generated_assets_dir / "uploads"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "reve-edit.png").write_bytes(b"reve-edit-source")
    (input_dir / "reve-remix-a.png").write_bytes(_png_header(1, 1))
    (input_dir / "reve-remix-b.png").write_bytes(_png_header(2, 1))
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _reve_image_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "generate-reve-image",
            "run_token": run_token,
        },
    )
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

    def assert_reve_artifact(
        result: dict,
        *,
        expected_bytes: bytes,
        expected_credits: int,
        expected_cost_cents: int,
    ) -> None:
        data = result["data"]
        item = data["output_json"]["data"][0]
        rendered = json.dumps(data)
        assert data["credential_ref"] == credential_ref
        assert data["action_call"]["credential_ref"] == credential_ref
        assert data["action_call"]["provider_key"] == "reve"
        assert data["action_call"]["connector_key"] == "reve"
        assert data["action_call"]["run_id"] == started["data"]["run_id"]
        assert data["action_call"]["run_plan_id"] == created["data"]["id"]
        assert data["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
        assert data["cost_cents"] == expected_cost_cents
        assert item["url"].startswith("/generated-assets/reve/reve-image-")
        assert item["artifact_ref"] == item["url"]
        assert isinstance(item["artifact_id"], int)
        assert data["output_json"]["artifact_refs"] == [item["url"]]
        assert data["output_json"]["usage"]["credits_used"] == expected_credits
        assert "image" not in data["output_json"]
        assert "credential_id" not in rendered
        assert "reve-key" not in rendered
        path = mcp_settings.generated_assets_dir / item["url"].removeprefix("/generated-assets/")
        assert path.read_bytes() == expected_bytes

    out = _call_action_execute_raw(
        mcp_client,
        {
            "project_id": project_id,
            "action_ref": "utils.reve.image.generate",
            "input_json": {"prompt": "editorial hero", "aspect_ratio": "3:2"},
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )

    assert_reve_artifact(
        out,
        expected_bytes=b"reve-image",
        expected_credits=18,
        expected_cost_cents=2,
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.reve.com/v1/image/edit",
        json={
            "image": base64.b64encode(b"reve-edit-image").decode("ascii"),
            "version": "reve-edit@20250915",
            "content_violation": False,
            "request_id": "rsid-edit",
            "credits_used": 30,
            "credits_remaining": 952,
        },
    )
    edit_out = _call_action_execute_raw(
        mcp_client,
        {
            "project_id": project_id,
            "action_ref": "utils.reve.image.edit",
            "input_json": {
                "edit_instruction": "make the product brighter",
                "input_image_ref": "/generated-assets/uploads/reve-edit.png",
                "aspect_ratio": "1:1",
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )
    assert_reve_artifact(
        edit_out,
        expected_bytes=b"reve-edit-image",
        expected_credits=30,
        expected_cost_cents=4,
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.reve.com/v1/image/remix",
        json={
            "image": base64.b64encode(b"reve-remix-image").decode("ascii"),
            "version": "reve-remix-fast@20251030",
            "content_violation": False,
            "request_id": "rsid-remix",
            "credits_used": 5,
            "credits_remaining": 947,
        },
    )
    remix_out = _call_action_execute_raw(
        mcp_client,
        {
            "project_id": project_id,
            "action_ref": "utils.reve.image.remix",
            "input_json": {
                "prompt": "combine the references into a new campaign image",
                "input_image_refs": [
                    "/generated-assets/uploads/reve-remix-a.png",
                    "/generated-assets/uploads/reve-remix-b.png",
                ],
                "version": "latest-fast",
                "aspect_ratio": "auto",
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )
    assert_reve_artifact(
        remix_out,
        expected_bytes=b"reve-remix-image",
        expected_credits=5,
        expected_cost_cents=1,
    )
    artifacts = mcp_client.call_tool_structured(
        "artifact.query",
        {"project_id": project_id, "kind": "image"},
    )
    artifact_ids = {
        out["data"]["output_json"]["data"][0]["artifact_id"],
        edit_out["data"]["output_json"]["data"][0]["artifact_id"],
        remix_out["data"]["output_json"]["data"][0]["artifact_id"],
    }
    assert artifact_ids.issubset({row["id"] for row in artifacts["items"]})


def test_action_execute_google_gemini_image_grant_returns_sanitized_artifact_refs(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_google_gemini_image_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "google-gemini-image", "monthly_budget_usd": 10.0},
        headers=mcp_client._headers(),
    )
    assert budget_resp.status_code == 200
    input_dir = mcp_settings.generated_assets_dir / "uploads"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "google-edit.png").write_bytes(b"google-edit-source")
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _google_gemini_image_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "generate-google-gemini-image",
            "run_token": run_token,
        },
    )
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
                                    "data": base64.b64encode(b"google-image").decode("ascii"),
                                }
                            }
                        ]
                    }
                }
            ],
            "usageMetadata": {"totalTokenCount": 1680},
        },
    )

    def assert_google_artifact(
        result: dict,
        *,
        expected_bytes: bytes,
        expected_cost_cents: int,
    ) -> None:
        data = result["data"]
        item = data["output_json"]["data"][0]
        rendered = json.dumps(data)
        assert data["credential_ref"] == credential_ref
        assert data["action_call"]["credential_ref"] == credential_ref
        assert data["action_call"]["provider_key"] == "google-gemini-image"
        assert data["action_call"]["connector_key"] == "google-gemini-image"
        assert data["action_call"]["run_id"] == started["data"]["run_id"]
        assert data["action_call"]["run_plan_id"] == created["data"]["id"]
        assert data["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
        assert data["cost_cents"] == expected_cost_cents
        assert item["url"].startswith("/generated-assets/google-gemini-image/google-gemini-image-")
        assert item["artifact_ref"] == item["url"]
        assert isinstance(item["artifact_id"], int)
        assert data["output_json"]["artifact_refs"] == [item["url"]]
        assert "candidates" not in data["output_json"]
        assert "credential_id" not in rendered
        assert "gemini-key" not in rendered
        path = mcp_settings.generated_assets_dir / item["url"].removeprefix("/generated-assets/")
        assert path.read_bytes() == expected_bytes

    out = _call_action_execute_raw(
        mcp_client,
        {
            "project_id": project_id,
            "action_ref": "utils.google.image.generate",
            "input_json": {
                "prompt": "editorial hero",
                "aspect_ratio": "16:9",
                "image_size": "2K",
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )

    assert_google_artifact(out, expected_bytes=b"google-image", expected_cost_cents=10)
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
                                    "data": base64.b64encode(b"google-edit-image").decode("ascii"),
                                }
                            }
                        ]
                    }
                }
            ]
        },
    )
    edit_out = _call_action_execute_raw(
        mcp_client,
        {
            "project_id": project_id,
            "action_ref": "utils.google.image.edit",
            "input_json": {
                "prompt": "make the product brighter",
                "input_image_refs": ["/generated-assets/uploads/google-edit.png"],
                "model": "gemini-3-pro-image",
                "aspect_ratio": "1:1",
                "image_size": "4K",
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )
    assert_google_artifact(
        edit_out,
        expected_bytes=b"google-edit-image",
        expected_cost_cents=24,
    )
    artifacts = mcp_client.call_tool_structured(
        "artifact.query",
        {"project_id": project_id, "kind": "image"},
    )
    artifact_ids = {
        out["data"]["output_json"]["data"][0]["artifact_id"],
        edit_out["data"]["output_json"]["data"][0]["artifact_id"],
    }
    assert artifact_ids.issubset({row["id"] for row in artifacts["items"]})


@pytest.mark.usefixtures("_media_provider_clock")
def test_action_execute_ideogram_image_grant_returns_sanitized_artifact_refs(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_ideogram_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "ideogram", "monthly_budget_usd": 10.0},
        headers=mcp_client._headers(),
    )
    assert budget_resp.status_code == 200
    input_dir = mcp_settings.generated_assets_dir / "uploads"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "ideogram-source.png").write_bytes(_png_header(32, 32))
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _ideogram_image_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "generate-ideogram-image",
            "run_token": run_token,
        },
    )

    def assert_ideogram_artifact(
        result: dict,
        *,
        expected_bytes: bytes,
        expected_cost_cents: int,
    ) -> None:
        data = result["data"]
        item = data["output_json"]["data"][0]
        rendered = json.dumps(data)
        assert data["credential_ref"] == credential_ref
        assert data["action_call"]["credential_ref"] == credential_ref
        assert data["action_call"]["provider_key"] == "ideogram"
        assert data["action_call"]["connector_key"] == "ideogram"
        assert data["action_call"]["run_id"] == started["data"]["run_id"]
        assert data["action_call"]["run_plan_id"] == created["data"]["id"]
        assert data["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
        assert data["cost_cents"] == expected_cost_cents
        assert item["url"].startswith("/generated-assets/ideogram/ideogram-")
        assert item["artifact_ref"] == item["url"]
        assert isinstance(item["artifact_id"], int)
        assert data["output_json"]["artifact_refs"] == [item["url"]]
        assert "ephemeral" not in rendered
        assert "sig=secret" not in rendered
        assert "credential_id" not in rendered
        assert "ideo-key" not in rendered
        path = mcp_settings.generated_assets_dir / item["url"].removeprefix("/generated-assets/")
        assert path.read_bytes() == expected_bytes

    generate_provider_url = "https://ideogram.ai/api/images/ephemeral/generated.png?sig=secret"
    httpx_mock.add_response(
        method="POST",
        url="https://api.ideogram.ai/v1/ideogram-v4/generate",
        json={
            "created": "2026-06-10 00:00:00+00:00",
            "data": [
                {
                    "prompt": "editorial hero",
                    "resolution": "2048x2048",
                    "is_image_safe": True,
                    "seed": 12345,
                    "url": generate_provider_url,
                }
            ],
            "response_type": "url",
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=generate_provider_url,
        content=b"ideogram-image",
        headers={"content-type": "image/png"},
    )
    out = _call_action_execute_raw(
        mcp_client,
        {
            "project_id": project_id,
            "action_ref": "utils.ideogram.image.generate",
            "input_json": {
                "text_prompt": "editorial hero",
                "resolution": "2048x2048",
                "rendering_speed": "TURBO",
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )
    assert_ideogram_artifact(out, expected_bytes=b"ideogram-image", expected_cost_cents=3)

    remix_provider_url = "https://ideogram.ai/api/images/ephemeral/remix.webp?sig=secret"
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
                    "seed": 67890,
                    "url": remix_provider_url,
                }
            ],
            "response_type": "url",
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=remix_provider_url,
        content=b"ideogram-remix",
        headers={"content-type": "image/webp"},
    )
    remix_out = _call_action_execute_raw(
        mcp_client,
        {
            "project_id": project_id,
            "action_ref": "utils.ideogram.image.remix",
            "input_json": {
                "text_prompt": "remix",
                "input_image_ref": "/generated-assets/uploads/ideogram-source.png",
                "image_weight": 75,
                "resolution": "3072x1024",
                "rendering_speed": "QUALITY",
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )
    assert_ideogram_artifact(
        remix_out,
        expected_bytes=b"ideogram-remix",
        expected_cost_cents=10,
    )
    artifacts = mcp_client.call_tool_structured(
        "artifact.query",
        {"project_id": project_id, "kind": "image"},
    )
    artifact_ids = {
        out["data"]["output_json"]["data"][0]["artifact_id"],
        remix_out["data"]["output_json"]["data"][0]["artifact_id"],
    }
    assert artifact_ids.issubset({row["id"] for row in artifacts["items"]})


@pytest.mark.usefixtures("_media_provider_clock")
def test_action_execute_byteplus_seedream_image_grant_returns_sanitized_artifact_refs(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_byteplus_ark_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "byteplus-ark", "monthly_budget_usd": 10.0},
        headers=mcp_client._headers(),
    )
    assert budget_resp.status_code == 200
    input_dir = mcp_settings.generated_assets_dir / "uploads"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "byteplus-source.png").write_bytes(_png_header(32, 32))
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _byteplus_seedream_image_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "generate-byteplus-seedream-image",
            "run_token": run_token,
        },
    )

    def assert_byteplus_artifact(
        result: dict,
        *,
        expected_bytes: bytes,
        expected_cost_cents: int,
    ) -> None:
        data = result["data"]
        item = data["output_json"]["data"][0]
        rendered = json.dumps(data)
        assert data["credential_ref"] == credential_ref
        assert data["action_call"]["credential_ref"] == credential_ref
        assert data["action_call"]["provider_key"] == "byteplus-ark"
        assert data["action_call"]["connector_key"] == "byteplus-seedream"
        assert data["action_call"]["run_id"] == started["data"]["run_id"]
        assert data["action_call"]["run_plan_id"] == created["data"]["id"]
        assert data["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
        assert data["cost_cents"] == expected_cost_cents
        assert item["url"].startswith("/generated-assets/byteplus-ark/byteplus-ark-")
        assert item["artifact_ref"] == item["url"]
        assert isinstance(item["artifact_id"], int)
        assert data["output_json"]["artifact_refs"] == [item["url"]]
        assert "ark-output.byteplus.test" not in rendered
        assert "token=secret" not in rendered
        assert "b64_json" not in rendered
        assert "credential_id" not in rendered
        assert "ark-key" not in rendered
        path = mcp_settings.generated_assets_dir / item["url"].removeprefix("/generated-assets/")
        assert path.read_bytes() == expected_bytes

    provider_url = "https://ark-output.byteplus.test/generated.jpeg?token=secret"
    httpx_mock.add_response(
        method="POST",
        url="https://ark.ap-southeast.bytepluses.com/api/v3/images/generations",
        json={
            "model": "seedream-5-0-lite-260128",
            "data": [{"url": provider_url, "size": "2048x2048"}],
            "usage": {"generated_images": 1},
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=provider_url,
        content=b"byteplus-image",
        headers={"content-type": "image/jpeg"},
    )
    out = _call_action_execute_raw(
        mcp_client,
        {
            "project_id": project_id,
            "action_ref": "utils.byteplus.image.generate",
            "input_json": {
                "prompt": "editorial hero",
                "size": "2K",
                "watermark": False,
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )
    assert_byteplus_artifact(out, expected_bytes=b"byteplus-image", expected_cost_cents=4)

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
    edit_out = _call_action_execute_raw(
        mcp_client,
        {
            "project_id": project_id,
            "action_ref": "utils.byteplus.image.edit",
            "input_json": {
                "prompt": "remix the product scene",
                "input_image_refs": ["/generated-assets/uploads/byteplus-source.png"],
                "model": "seedream-4-0-250828",
                "size": "2048x2048",
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )
    assert_byteplus_artifact(
        edit_out,
        expected_bytes=b"byteplus-edit",
        expected_cost_cents=3,
    )
    artifacts = mcp_client.call_tool_structured(
        "artifact.query",
        {"project_id": project_id, "kind": "image"},
    )
    artifact_ids = {
        out["data"]["output_json"]["data"][0]["artifact_id"],
        edit_out["data"]["output_json"]["data"][0]["artifact_id"],
    }
    assert artifact_ids.issubset({row["id"] for row in artifacts["items"]})


def test_action_execute_openai_image_edit_uses_input_reference_images(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_openai_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "openai-images", "monthly_budget_usd": 10.0},
        headers={"authorization": f"Bearer {mcp_client.auth_token}"},
    )
    assert budget_resp.status_code == 200
    input_dir = mcp_settings.generated_assets_dir / "uploads"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "product.png").write_bytes(b"product-photo")
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _image_edit_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "edit-image",
            "run_token": run_token,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/images/edits",
        json={"data": [{"b64_json": base64.b64encode(b"edited-webp").decode("ascii")}]},
    )

    out = _call_action_execute_raw(
        mcp_client,
        {
            "project_id": project_id,
            "action_ref": "utils.image.edit",
            "input_json": {
                "prompt": "same product on a marble table",
                "input_image_refs": ["/generated-assets/uploads/product.png"],
                "n": 1,
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )

    data = out["data"]
    item = data["output_json"]["data"][0]
    rendered = json.dumps(data)
    vendor_request = httpx_mock.get_requests()[-1]
    assert vendor_request.headers["content-type"].startswith("multipart/form-data; boundary=")
    vendor_body = vendor_request.content
    assert b'name="prompt"' in vendor_body
    assert b"same product on a marble table" in vendor_body
    assert b'name="image"; filename="product.png"' in vendor_body
    assert b"Content-Type: image/png" in vendor_body
    assert b"product-photo" in vendor_body
    assert b"data:image/png;base64" not in vendor_body
    assert item["url"].startswith("/generated-assets/openai-images/openai-")
    assert item["artifact_ref"] == item["url"]
    assert isinstance(item["artifact_id"], int)
    assert "b64_json" not in item
    assert "sk-openai" not in rendered
    path = mcp_settings.generated_assets_dir / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"edited-webp"


def test_action_execute_firecrawl_grant_uses_generic_connector(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stackos_connectors.connectors.firecrawl.actions import FirecrawlActionConnector

    native_calls = []
    native_execute = FirecrawlActionConnector.execute

    async def native_spy(self, request):
        native_calls.append(request)
        return await native_execute(self, request)

    monkeypatch.setattr(FirecrawlActionConnector, "execute", native_spy)
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_firecrawl_credential(mcp_client, project_id)
    denied = mcp_client.call_tool_error(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.web.scrape",
            "input_json": {"url": "https://example.com"},
            "credential_ref": credential_ref,
        },
    )
    assert denied["data"]["tool"] == "action.execute"
    assert native_calls == []
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "firecrawl", "monthly_budget_usd": 10.0},
        headers={"authorization": f"Bearer {mcp_client.auth_token}"},
    )
    assert budget_resp.status_code == 200
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _firecrawl_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "scrape-page",
            "run_token": run_token,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.firecrawl.dev/v2/scrape",
        json={"data": {"markdown": "# Hello"}},
    )

    out = _call_action_execute_raw(
        mcp_client,
        {
            "project_id": project_id,
            "action_ref": "utils.web.scrape",
            "input_json": {"url": "https://example.com"},
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )

    data = out["data"]
    rendered = json.dumps(data)
    assert data["credential_ref"] == credential_ref
    assert data["output_json"] == {"data": {"markdown": "# Hello"}}
    assert data["action_call"]["provider_key"] == "firecrawl"
    assert data["action_call"]["connector_key"] == "firecrawl"
    assert "credential_id" not in rendered
    assert "fc-key" not in rendered
    assert len(native_calls) == 1
    assert native_calls[0].auth.fields == {"api_key": "fc-key"}
    assert native_calls[0].auth.config == {}
    assert not hasattr(native_calls[0], "project_id")
    assert data["action_call"]["run_plan_id"] == created["data"]["id"]
    assert data["action_call"]["run_id"] == started["data"]["run_id"]


def test_action_execute_sitemap_grant_uses_noauth_utility_connector(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    described = _call_tool_raw(
        mcp_client,
        "action.describe",
        {"project_id": project_id, "action_ref": "utils.sitemap.fetch"},
    )
    validation = _call_tool_raw(
        mcp_client,
        "action.validate",
        {
            "project_id": project_id,
            "action_ref": "utils.sitemap.fetch",
            "input_json": {"urls": ["https://example.com/sitemap.xml"], "max_entries": 5},
        },
    )

    assert described["availability"]["status"] == "ready"
    assert described["manifest"]["requires_credential"] is False
    assert validation["valid"] is True
    assert validation["credential_ref"] is None

    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _sitemap_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "fetch-sitemap",
            "run_token": run_token,
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://example.com/sitemap.xml",
        text=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://example.com/a</loc></url>"
            "</urlset>"
        ),
    )

    out = _call_action_execute_raw(
        mcp_client,
        {
            "project_id": project_id,
            "action_ref": "utils.sitemap.fetch",
            "input_json": {"urls": ["https://example.com/sitemap.xml"], "max_entries": 5},
            "run_token": run_token,
        },
    )

    data = out["data"]
    assert data["credential_ref"] is None
    assert data["action_call"]["provider_key"] is None
    assert data["action_call"]["connector_key"] == "sitemap"
    assert data["output_json"]["entries"][0]["url"] == "https://example.com/a"
    assert data["output_json"]["errors"] == []

    audit_resp = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={
            "run_id": started["data"]["run_id"],
            "run_plan_id": created["data"]["id"],
            "run_plan_step_id": claimed["data"]["id"],
            "plugin_slug": "utils",
            "action_key": "sitemap.fetch",
            "status": "success",
        },
        headers=mcp_client._headers(),
    )
    assert audit_resp.status_code == 200
    audit_body = audit_resp.json()
    audit_rendered = json.dumps(audit_body)
    assert audit_body["total_estimate"] == 1
    audit_row = audit_body["items"][0]
    assert audit_row["id"] == data["action_call"]["id"]
    assert audit_row["run_id"] == started["data"]["run_id"]
    assert audit_row["run_plan_id"] == created["data"]["id"]
    assert audit_row["run_plan_step_id"] == claimed["data"]["id"]
    assert audit_row["plugin_slug"] == "utils"
    assert audit_row["action_key"] == "sitemap.fetch"
    assert audit_row["credential_ref"] is None
    assert audit_row["response_json"]["entries"][0]["url"] == "https://example.com/a"
    assert "credential_id" not in audit_rendered
    assert "idempotency_key" not in audit_rendered


def test_action_execute_dataforseo_paa_grant_uses_generic_connector(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_dataforseo_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "dataforseo", "monthly_budget_usd": 10.0},
        headers={"authorization": f"Bearer {mcp_client.auth_token}"},
    )
    assert budget_resp.status_code == 200
    described = _call_tool_raw(
        mcp_client,
        "action.describe",
        {"project_id": project_id, "action_ref": "seo.paa.extract"},
    )
    validation = _call_tool_raw(
        mcp_client,
        "action.validate",
        {
            "project_id": project_id,
            "action_ref": "seo.paa.extract",
            "input_json": {"keyword": "seo tools"},
            "credential_ref": credential_ref,
        },
    )

    assert described["availability"]["status"] == "ready"
    assert described["manifest"]["connector_key"] == "dataforseo"
    assert validation["valid"] is True
    assert validation["credential_ref"] == credential_ref

    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _dataforseo_paa_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "extract-paa",
            "run_token": run_token,
        },
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

    out = _call_action_execute_raw(
        mcp_client,
        {
            "project_id": project_id,
            "action_ref": "seo.paa.extract",
            "input_json": {"keyword": "seo tools"},
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )

    request_body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    data = out["data"]
    rendered = json.dumps(data)
    assert request_body[0]["keyword"] == "seo tools"
    assert request_body[0]["people_also_ask_click_depth"] == 1
    assert data["credential_ref"] == credential_ref
    assert data["action_call"]["provider_key"] == "dataforseo"
    assert data["action_call"]["connector_key"] == "dataforseo"
    assert data["output_json"]["tasks"][0]["result"][0]["items"][0]["title"] == "What is SEO?"
    assert "password" not in rendered
    assert "login@example.com" not in rendered


def test_action_execute_wordpress_post_create_grant_uses_generic_connector(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_wordpress_credential(mcp_client, project_id)
    described = _call_tool_raw(
        mcp_client,
        "action.describe",
        {"project_id": project_id, "action_ref": "publishing.wordpress.post.create"},
    )
    validation = _call_tool_raw(
        mcp_client,
        "action.validate",
        {
            "project_id": project_id,
            "action_ref": "publishing.wordpress.post.create",
            "input_json": {
                "post": {
                    "title": "Hello world",
                    "content": "<p>Body</p>",
                    "status": "draft",
                }
            },
            "credential_ref": credential_ref,
        },
    )

    assert described["availability"]["status"] == "ready"
    assert described["manifest"]["connector_key"] == "wordpress"
    assert validation["valid"] is True

    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _wordpress_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "create-wordpress-post",
            "run_token": run_token,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://wp.example/wp-json/wp/v2/posts",
        json={"id": 42, "link": "https://wp.example/hello-world/"},
    )

    out = _call_action_execute_raw(
        mcp_client,
        {
            "project_id": project_id,
            "action_ref": "publishing.wordpress.post.create",
            "input_json": {
                "post": {
                    "title": "Hello world",
                    "content": "<p>Body</p>",
                    "status": "draft",
                }
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )

    request_body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    data = out["data"]
    rendered = json.dumps(data)
    assert request_body == {
        "title": "Hello world",
        "content": "<p>Body</p>",
        "status": "draft",
    }
    assert data["credential_ref"] == credential_ref
    assert data["action_call"]["provider_key"] == "wordpress"
    assert data["action_call"]["connector_key"] == "wordpress"
    assert data["output_json"]["id"] == 42
    assert "app pass" not in rendered
    assert "editor" not in rendered


def test_action_execute_ghost_post_create_grant_uses_generic_connector(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_ghost_credential(mcp_client, project_id)
    described = _call_tool_raw(
        mcp_client,
        "action.describe",
        {"project_id": project_id, "action_ref": "publishing.ghost.post.create"},
    )
    validation = _call_tool_raw(
        mcp_client,
        "action.validate",
        {
            "project_id": project_id,
            "action_ref": "publishing.ghost.post.create",
            "input_json": {
                "post": {
                    "title": "Hello world",
                    "html": "<p>Body</p>",
                    "status": "draft",
                }
            },
            "credential_ref": credential_ref,
        },
    )

    assert described["availability"]["status"] == "ready"
    assert described["manifest"]["connector_key"] == "ghost"
    assert validation["valid"] is True

    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _ghost_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "create-ghost-post",
            "run_token": run_token,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://ghost.example/ghost/api/admin/posts/?source=html",
        json={"posts": [{"id": "post-1", "url": "https://ghost.example/hello/"}]},
    )

    out = _call_action_execute_raw(
        mcp_client,
        {
            "project_id": project_id,
            "action_ref": "publishing.ghost.post.create",
            "input_json": {
                "post": {
                    "title": "Hello world",
                    "html": "<p>Body</p>",
                    "status": "draft",
                }
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )

    request = httpx_mock.get_requests()[0]
    request_body = json.loads(request.content.decode("utf-8"))
    data = out["data"]
    rendered = json.dumps(data)
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
    assert data["credential_ref"] == credential_ref
    assert data["action_call"]["provider_key"] == "ghost"
    assert data["action_call"]["connector_key"] == "ghost"
    assert data["output_json"]["posts"][0]["id"] == "post-1"
    assert "00112233445566778899aabbccddeeff" not in rendered
    assert "keyid" not in rendered
