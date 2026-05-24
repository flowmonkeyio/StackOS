from __future__ import annotations

from stackos.operations.registry import build_operation_registry


def test_operation_registry_documents_core_operations() -> None:
    registry = build_operation_registry()

    names = [item.name for item in registry.all()]
    assert names == [
        "action.describe",
        "action.execute",
        "action.run",
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
        "communicationContact.list",
        "communicationContact.upsert",
        "communicationContext.query",
        "communicationMembership.list",
        "communicationMembership.upsert",
        "communicationProfile.get",
        "communicationProfile.list",
        "communicationProfile.upsert",
        "communicationRoute.list",
        "communicationRoute.upsert",
        "communicationSurface.list",
        "communicationSurface.upsert",
        "communicationTarget.list",
        "communicationTarget.resolve",
        "communicationTarget.upsert",
        "ingressEndpoint.configure",
        "ingressEndpoint.refresh",
        "ingressEndpoint.routes",
        "ingressEndpoint.status",
        "ingressEndpoint.sync",
        "localAgentChat.createMessage",
        "runPlan.claimStep",
        "runPlan.create",
        "runPlan.get",
        "runPlan.list",
        "runPlan.recordStep",
        "runPlan.start",
        "runPlan.update",
        "runPlan.validate",
        "toolProfile.resolve",
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

    direct_action = registry.get("action.run").describe_out()
    assert direct_action.surfaces["mcp"].enabled is True
    assert direct_action.surfaces["rest"].enabled is True
    assert direct_action.surfaces["cli"].command == "actions run"
    assert direct_action.grant_policy == "direct-action-policy"
    assert any("confirm_direct=true" in item for item in direct_action.prerequisites)

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

    target_resolver = registry.get("communicationTarget.resolve").describe_out()
    assert target_resolver.surfaces["mcp"].enabled is True
    assert target_resolver.surfaces["rest"].enabled is True
    assert target_resolver.surfaces["cli"].command == "ops call communicationTarget.resolve"
    assert target_resolver.grant_policy == "direct-read"
    assert any("before sending" in item for item in [target_resolver.purpose])

    context_query = registry.get("communicationContext.query").describe_out()
    assert context_query.surfaces["mcp"].enabled is True
    assert context_query.grant_policy == "direct-read"
    assert any("never fetches live provider history" in item for item in [context_query.purpose])

    local_chat = registry.get("localAgentChat.createMessage").describe_out()
    assert local_chat.surfaces["mcp"].enabled is True
    assert local_chat.surfaces["rest"].enabled is True
    assert local_chat.surfaces["cli"].command == "ops call localAgentChat.createMessage"
    assert local_chat.grant_policy == "direct-work-queue-write"
    assert any("never invokes a model" in item for item in [local_chat.purpose])

    ingress = registry.get("ingressEndpoint.configure").describe_out()
    assert ingress.surfaces["mcp"].enabled is True
    assert ingress.surfaces["rest"].enabled is True
    assert ingress.surfaces["cli"].command == "ops call ingressEndpoint.configure"
    assert ingress.grant_policy == "direct-setup-write"
    assert any("driver_config" in item for item in ingress.prerequisites)

    resolver = registry.get("toolProfile.resolve").describe_out()
    assert resolver.surfaces["mcp"].enabled is True
    assert resolver.surfaces["rest"].enabled is True
    assert resolver.surfaces["cli"].command == "ops call toolProfile.resolve"
    assert resolver.grant_policy == "direct-read"
    assert any("credential_ref" in item for item in resolver.returns)

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
        "action.run",
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
        "communicationContact.list",
        "communicationContact.upsert",
        "communicationContext.query",
        "communicationMembership.list",
        "communicationMembership.upsert",
        "communicationProfile.get",
        "communicationProfile.list",
        "communicationProfile.upsert",
        "communicationRoute.list",
        "communicationRoute.upsert",
        "communicationSurface.list",
        "communicationSurface.upsert",
        "communicationTarget.list",
        "communicationTarget.resolve",
        "communicationTarget.upsert",
        "ingressEndpoint.configure",
        "ingressEndpoint.refresh",
        "ingressEndpoint.routes",
        "ingressEndpoint.status",
        "ingressEndpoint.sync",
        "localAgentChat.createMessage",
        "runPlan.claimStep",
        "runPlan.create",
        "runPlan.get",
        "runPlan.list",
        "runPlan.recordStep",
        "runPlan.start",
        "runPlan.validate",
        "toolProfile.resolve",
    ]
    assert registry.get("runPlan.update").surfaces.cli.enabled is False
    assert registry.list_out(surface="rest").items[0].surfaces["rest"].enabled is True
