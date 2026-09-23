"""MCP coverage for physical communication-surface bindings."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from stackos.communication_surface_bindings import communication_surface_binding_external_id
from stackos.repositories.resources import ResourceRepository

from .conftest import MCPClient


def _profile(mcp_client: MCPClient, project_id: int, key: str) -> None:
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": key,
            "identity": {"display_name": key.title()},
        },
    )


def test_surface_binding_keeps_local_agent_chat_profile_explicit_and_ambiguous_profiles_fail(
    mcp_client: MCPClient,
    seeded_project: dict[str, Any],
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _profile(mcp_client, project_id, "support")

    created = mcp_client.call_tool_structured(
        "communicationSurface.upsert",
        {
            "project_id": project_id,
            "surface_ref": "local-agent-chat:thread:support",
            "provider_key": "local-agent-chat",
            "profile_ref": "communication-profile:support",
            "kind": "local-agent-thread",
        },
    )

    assert created["data"]["profile_ref"] == "communication-profile:support"

    _profile(mcp_client, project_id, "review")
    failed = mcp_client.call_tool_error(
        "communicationSurface.upsert",
        {
            "project_id": project_id,
            "surface_ref": "local-agent-chat:thread:review",
            "provider_key": "local-agent-chat",
            "kind": "local-agent-thread",
        },
    )

    assert failed["data"]["candidate_profile_refs"] == [
        "communication-profile:support",
        "communication-profile:review",
    ]


def test_surface_list_hides_retained_superseded_bindings_and_marks_repair_required(
    mcp_client: MCPClient,
    seeded_project: dict[str, Any],
) -> None:
    project_id = int(seeded_project["data"]["id"])
    profile_ref = "communication-profile:support"
    surface_ref = "slack-channel:C123"
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        resources = ResourceRepository(session)
        canonical_ref = communication_surface_binding_external_id(
            provider_key="slack-bot",
            profile_ref=profile_ref,
            surface_ref=surface_ref,
        )
        resources.upsert_record(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-channel",
            external_id=canonical_ref,
            title="Support",
            data_json={
                "surface_ref": surface_ref,
                "channel_ref": surface_ref,
                "provider_key": "slack-bot",
                "profile_ref": profile_ref,
                "kind": "slack-channel",
            },
        )
        resources.upsert_record(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-channel",
            external_id="communication-surface:legacy-support",
            title="Legacy support",
            data_json={
                "surface_ref": surface_ref,
                "channel_ref": surface_ref,
                "provider_key": "slack-bot",
                "profile_ref": profile_ref,
                "kind": "slack-channel",
                "surface_binding_state": "superseded",
                "surface_binding_issue": "canonical_binding_exists",
                "surface_binding_ref": canonical_ref,
            },
        )
        repair_surface_ref = "slack-channel:CREPAIR"
        resources.upsert_record(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-channel",
            external_id=communication_surface_binding_external_id(
                provider_key="slack-bot",
                profile_ref=profile_ref,
                surface_ref=repair_surface_ref,
            ),
            title="Repair required",
            data_json={
                "surface_ref": repair_surface_ref,
                "channel_ref": repair_surface_ref,
                "provider_key": "slack-bot",
                "profile_ref": profile_ref,
                "kind": "slack-channel",
                "surface_binding_state": "repair-required",
                "surface_binding_issue": "conflicting_duplicate_binding_metadata",
            },
        )

    listed = mcp_client.call_tool_structured(
        "communicationSurface.list",
        {"project_id": project_id, "response_mode": "raw"},
    )

    assert [item["surface_ref"] for item in listed["items"]] == [
        surface_ref,
        repair_surface_ref,
    ]
    assert listed["items"][0]["binding_state"] == "ready"
    assert listed["items"][0]["binding_issues"] == []
    assert listed["items"][1]["binding_state"] == "repair-required"
    assert listed["items"][1]["binding_issues"] == [
        {"code": "conflicting_duplicate_binding_metadata"}
    ]
