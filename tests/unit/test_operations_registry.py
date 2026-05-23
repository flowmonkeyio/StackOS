from __future__ import annotations

from content_stack.operations.registry import build_operation_registry


def test_operation_registry_documents_core_operations() -> None:
    registry = build_operation_registry()

    names = [item.name for item in registry.all()]
    assert names == [
        "action.describe",
        "action.execute",
        "action.validate",
        "agentRequest.claim",
        "agentRequest.complete",
        "agentRequest.create",
        "agentRequest.get",
        "agentRequest.ignore",
        "agentRequest.linkRunPlan",
        "agentRequest.list",
        "agentRequest.prepareRunPlan",
        "agentRequest.release",
        "communicationBotProfile.get",
        "communicationBotProfile.list",
        "communicationBotProfile.upsert",
        "localAgentChat.createMessage",
        "runPlan.claimStep",
        "runPlan.create",
        "runPlan.get",
        "runPlan.list",
        "runPlan.recordStep",
        "runPlan.start",
        "runPlan.update",
        "runPlan.validate",
    ]

    described = registry.get("action.execute").describe_out()

    assert described.name == "action.execute"
    assert described.mutating is True
    assert described.surfaces["mcp"].enabled is True
    assert described.surfaces["rest"].enabled is True
    assert described.surfaces["cli"].enabled is True
    assert described.grant_policy == "run-plan-step-action-ref"
    assert "properties" in described.input_schema
    assert "project_id" in described.input_schema["properties"]
    assert "WriteEnvelope" in described.output_schema["title"]
    assert any("run_token" in item for item in described.prerequisites)
    assert described.examples[0].arguments["action_ref"] == "utils.sitemap.fetch"

    agent_request = registry.get("agentRequest.claim").describe_out()
    assert agent_request.surfaces["mcp"].enabled is True
    assert agent_request.surfaces["rest"].enabled is True
    assert agent_request.surfaces["cli"].command == "agent-requests claim"
    assert agent_request.grant_policy == "direct-work-queue-write"
    assert any("idempotency_key" in item for item in agent_request.prerequisites)

    create_request = registry.get("agentRequest.create").describe_out()
    assert create_request.grant_policy == "run-plan-step-grant"
    assert any("run_token" in item for item in create_request.prerequisites)

    prepare_request = registry.get("agentRequest.prepareRunPlan").describe_out()
    assert prepare_request.surfaces["cli"].command == "agent-requests prepare-run-plan"
    assert prepare_request.grant_policy == "direct-work-queue-write"
    assert any("does not infer intent" in item for item in prepare_request.prerequisites)

    bot_profile = registry.get("communicationBotProfile.upsert").describe_out()
    assert bot_profile.surfaces["mcp"].enabled is True
    assert bot_profile.surfaces["rest"].enabled is True
    assert bot_profile.surfaces["cli"].command == "ops call communicationBotProfile.upsert"
    assert bot_profile.grant_policy == "direct-setup-write"
    assert any("telegram-bot credential" in item for item in bot_profile.prerequisites)

    local_chat = registry.get("localAgentChat.createMessage").describe_out()
    assert local_chat.surfaces["mcp"].enabled is True
    assert local_chat.surfaces["rest"].enabled is True
    assert local_chat.surfaces["cli"].command == "ops call localAgentChat.createMessage"
    assert local_chat.grant_policy == "direct-work-queue-write"
    assert any("never invokes a model" in item for item in [local_chat.purpose])

    run_plan = registry.get("runPlan.claimStep").describe_out()
    assert run_plan.surfaces["mcp"].enabled is True
    assert run_plan.surfaces["rest"].enabled is True
    assert run_plan.surfaces["cli"].command == "run-plans claim-step"
    assert run_plan.grant_policy == "run-plan-controller"
    assert any("run_token" in item for item in run_plan.prerequisites)


def test_operation_registry_surface_filter() -> None:
    registry = build_operation_registry()

    assert [item.name for item in registry.by_surface("cli")] == [
        "action.describe",
        "action.execute",
        "action.validate",
        "agentRequest.claim",
        "agentRequest.complete",
        "agentRequest.create",
        "agentRequest.get",
        "agentRequest.ignore",
        "agentRequest.linkRunPlan",
        "agentRequest.list",
        "agentRequest.prepareRunPlan",
        "agentRequest.release",
        "communicationBotProfile.get",
        "communicationBotProfile.list",
        "communicationBotProfile.upsert",
        "localAgentChat.createMessage",
        "runPlan.claimStep",
        "runPlan.create",
        "runPlan.get",
        "runPlan.list",
        "runPlan.recordStep",
        "runPlan.start",
        "runPlan.validate",
    ]
    assert registry.get("runPlan.update").surfaces.cli.enabled is False
    assert registry.list_out(surface="rest").items[0].surfaces["rest"].enabled is True
