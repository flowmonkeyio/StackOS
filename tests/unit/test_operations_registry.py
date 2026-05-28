from __future__ import annotations

from stackos.operations.registry import build_operation_registry


def test_operation_registry_documents_core_operations() -> None:
    registry = build_operation_registry()

    names = [item.name for item in registry.all()]
    assert names == [
        "action.describe",
        "action.execute",
        "action.list",
        "action.run",
        "action.validate",
        "agentPreset.describe",
        "agentPreset.list",
        "agentPreset.resolveForWorkflow",
        "agentRequest.claim",
        "agentRequest.complete",
        "agentRequest.create",
        "agentRequest.get",
        "agentRequest.ignore",
        "agentRequest.linkRunPlan",
        "agentRequest.list",
        "agentRequest.prepareRunPlan",
        "agentRequest.release",
        "auth.status",
        "auth.test",
        "communication.reply",
        "communication.send",
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
        "operation.describe",
        "operation.list",
        "project.create",
        "project.get",
        "project.list",
        "runPlan.claimStep",
        "runPlan.create",
        "runPlan.get",
        "runPlan.list",
        "runPlan.recordStep",
        "runPlan.start",
        "runPlan.update",
        "runPlan.validate",
        "toolProfile.resolve",
        "tracker.blockers",
        "tracker.brief",
        "tracker.changed",
        "tracker.createTask",
        "tracker.createTicket",
        "tracker.execute",
        "tracker.get",
        "tracker.history",
        "tracker.linkRunPlan",
        "tracker.next",
        "tracker.patch",
        "tracker.pick",
        "tracker.release",
        "tracker.search",
        "tracker.status",
        "tracker.updateTask",
        "tracker.updateTicket",
        "tracker.verify",
        "tracker.why",
        "workspace.bootstrap",
        "workspace.connect",
        "workspace.listBindings",
        "workspace.resolve",
        "workspace.startSession",
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
    assert any("intent_id or idempotency_key" in item for item in direct_action.prerequisites)

    action_list = registry.get("action.list").describe_out()
    assert action_list.category == "actions"
    assert action_list.surfaces["mcp"].enabled is True
    assert action_list.surfaces["rest"].enabled is True
    assert action_list.surfaces["cli"].command == "actions list"
    assert action_list.grant_policy == "direct-read"
    assert any("discover executable action refs" in item for item in [action_list.purpose])

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

    agent_preset_resolution = registry.get("agentPreset.resolveForWorkflow").describe_out()
    assert agent_preset_resolution.surfaces["mcp"].enabled is True
    assert agent_preset_resolution.surfaces["rest"].enabled is True
    assert agent_preset_resolution.surfaces["cli"].command == (
        "ops call agentPreset.resolveForWorkflow"
    )
    assert agent_preset_resolution.grant_policy == "direct-read"
    assert any("StackOS skill" in item for item in agent_preset_resolution.prerequisites)

    communication_profile = registry.get("communicationProfile.upsert").describe_out()
    assert communication_profile.surfaces["mcp"].enabled is True
    assert communication_profile.surfaces["rest"].enabled is True
    assert communication_profile.surfaces["cli"].command == "ops call communicationProfile.upsert"
    assert communication_profile.grant_policy == "direct-setup-write"
    assert any("provider_facets" in item for item in communication_profile.prerequisites)

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

    communication_send = registry.get("communication.send").describe_out()
    assert communication_send.surfaces["mcp"].enabled is True
    assert communication_send.surfaces["rest"].enabled is True
    assert communication_send.surfaces["cli"].command == "ops call communication.send"
    assert communication_send.grant_policy == "direct-communication-send"
    assert any("StackOS resolves profile" in item for item in [communication_send.purpose])

    communication_reply = registry.get("communication.reply").describe_out()
    assert communication_reply.surfaces["mcp"].enabled is True
    assert communication_reply.surfaces["rest"].enabled is True
    assert communication_reply.grant_policy == "direct-communication-send"
    assert any("origin surface/thread" in item for item in [communication_reply.purpose])

    local_chat = registry.get("localAgentChat.createMessage").describe_out()
    assert local_chat.surfaces["mcp"].enabled is True
    assert local_chat.surfaces["rest"].enabled is True
    assert local_chat.surfaces["cli"].command == "ops call localAgentChat.createMessage"
    assert local_chat.grant_policy == "direct-work-queue-write"
    assert any("never invokes a model" in item for item in [local_chat.purpose])

    operation_list = registry.get("operation.list").describe_out()
    assert operation_list.surfaces["mcp"].enabled is True
    assert operation_list.surfaces["rest"].enabled is True
    assert operation_list.surfaces["cli"].command == "ops list"
    assert operation_list.grant_policy == "direct-read"
    assert any(
        "available StackOS operation inventory" in item for item in operation_list.when_to_use
    )

    operation_describe = registry.get("operation.describe").describe_out()
    assert operation_describe.surfaces["mcp"].enabled is True
    assert operation_describe.surfaces["rest"].enabled is True
    assert operation_describe.surfaces["cli"].command == "ops describe"
    assert operation_describe.grant_policy == "direct-read"
    assert operation_describe.examples[1].arguments["name"] == "operation.describe"

    auth_status = registry.get("auth.status").describe_out()
    assert auth_status.surfaces["mcp"].enabled is True
    assert auth_status.surfaces["rest"].enabled is True
    assert auth_status.surfaces["cli"].command == "ops call auth.status"
    assert auth_status.grant_policy == "direct-read"
    assert any("credential_ref" in item for item in auth_status.returns)

    auth_test = registry.get("auth.test").describe_out()
    assert auth_test.surfaces["mcp"].enabled is True
    assert auth_test.surfaces["rest"].enabled is True
    assert auth_test.surfaces["cli"].command == "ops call auth.test"
    assert auth_test.grant_policy == "direct-setup-write"
    assert any("auth.status" in item for item in auth_test.prerequisites)

    project_list = registry.get("project.list").describe_out()
    assert project_list.surfaces["mcp"].enabled is True
    assert project_list.grant_policy == "direct-setup-read"
    assert any("workspace.resolve" in item for item in project_list.when_to_use)

    workspace_bootstrap = registry.get("workspace.bootstrap").describe_out()
    assert workspace_bootstrap.surfaces["mcp"].enabled is True
    assert workspace_bootstrap.mutating is True
    assert workspace_bootstrap.grant_policy == "direct-setup-write"
    assert any("idempotent" in item for item in [workspace_bootstrap.purpose])

    workspace_session = registry.get("workspace.startSession").describe_out()
    assert workspace_session.surfaces["mcp"].enabled is True
    assert workspace_session.mutating is True
    assert workspace_session.grant_policy == "direct-setup-write"
    assert any("auto_bootstrap=false" in item for item in workspace_session.prerequisites)

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

    tracker_next = registry.get("tracker.next").describe_out()
    assert tracker_next.surfaces["mcp"].enabled is True
    assert tracker_next.surfaces["rest"].enabled is True
    assert tracker_next.surfaces["cli"].command == "tracker next"
    assert tracker_next.grant_policy == "direct-read"
    assert any("bounded project work context" in item for item in tracker_next.when_to_use)

    tracker_patch = registry.get("tracker.patch").describe_out()
    assert tracker_patch.surfaces["mcp"].enabled is True
    assert tracker_patch.surfaces["rest"].enabled is True
    assert tracker_patch.surfaces["cli"].command == "tracker patch"
    assert tracker_patch.grant_policy == "direct-tracker-write"
    assert "WriteEnvelope" in tracker_patch.output_schema["title"]


def test_operation_registry_surface_filter() -> None:
    registry = build_operation_registry()

    assert [item.name for item in registry.by_surface("cli")] == [
        "action.describe",
        "action.execute",
        "action.list",
        "action.run",
        "action.validate",
        "agentPreset.describe",
        "agentPreset.list",
        "agentPreset.resolveForWorkflow",
        "agentRequest.claim",
        "agentRequest.complete",
        "agentRequest.create",
        "agentRequest.get",
        "agentRequest.ignore",
        "agentRequest.linkRunPlan",
        "agentRequest.list",
        "agentRequest.prepareRunPlan",
        "agentRequest.release",
        "auth.status",
        "auth.test",
        "communication.reply",
        "communication.send",
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
        "operation.describe",
        "operation.list",
        "project.create",
        "project.get",
        "project.list",
        "runPlan.claimStep",
        "runPlan.create",
        "runPlan.get",
        "runPlan.list",
        "runPlan.recordStep",
        "runPlan.start",
        "runPlan.update",
        "runPlan.validate",
        "toolProfile.resolve",
        "tracker.blockers",
        "tracker.brief",
        "tracker.changed",
        "tracker.createTask",
        "tracker.createTicket",
        "tracker.execute",
        "tracker.get",
        "tracker.history",
        "tracker.linkRunPlan",
        "tracker.next",
        "tracker.patch",
        "tracker.pick",
        "tracker.release",
        "tracker.search",
        "tracker.status",
        "tracker.updateTask",
        "tracker.updateTicket",
        "tracker.verify",
        "tracker.why",
        "workspace.bootstrap",
        "workspace.connect",
        "workspace.listBindings",
        "workspace.resolve",
        "workspace.startSession",
    ]
    assert registry.get("runPlan.update").surfaces.cli.enabled is True
    assert registry.get("runPlan.update").surfaces.cli.command == "run-plans approve"
    assert registry.list_out(surface="rest").items[0].surfaces["rest"].enabled is True


def test_operation_registry_filters_and_groups_operation_list() -> None:
    registry = build_operation_registry()

    actions = registry.list_out(category="action")
    assert [item.category for item in actions.items] == ["actions"] * len(actions.items)
    assert "action.list" in [item.name for item in actions.items]
    assert any(group.category == "actions" for group in actions.groups)

    queried = registry.list_out(query="workspace", mode="grouped")
    assert queried.items == []
    assert any("workspace.startSession" in group.operation_names for group in queried.groups)
    assert all(group.count == len(group.operation_names) for group in queried.groups)


def test_tracker_operations_include_agent_examples() -> None:
    registry = build_operation_registry()

    missing = [
        operation.name
        for operation in registry.all()
        if operation.name.startswith("tracker.") and not operation.examples
    ]

    assert missing == []
    assert registry.get("tracker.brief").examples[0].arguments["response_mode"] == "compact"
    assert "dry_run" in registry.get("tracker.createTicket").examples[0].arguments


def test_agent_communication_operations_include_when_to_use_guidance() -> None:
    registry = build_operation_registry()
    prefixes = ("agentRequest.", "communication", "ingressEndpoint.", "localAgentChat.")

    missing = [
        operation.name
        for operation in registry.all()
        if operation.name.startswith(prefixes) and not operation.when_to_use
    ]

    assert missing == []
    assert any("dry_run=true" in item for item in registry.get("communication.send").when_to_use)
    assert any("claim token" in item for item in registry.get("agentRequest.claim").when_to_use)
