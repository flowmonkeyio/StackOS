from __future__ import annotations

from stackos.operations.registry import build_operation_registry


def test_operation_registry_documents_core_operations() -> None:
    registry = build_operation_registry()

    names = {item.name for item in registry.all()}
    assert len(names) == 167
    assert {
        "action.execute",
        "auth.status",
        "workflowTemplate.describe",
        "resource.query",
        "resource.upsert",
        "artifact.create",
        "context.query",
        "learning.query",
        "decision.record",
        "budget.set",
        "run.start",
        "workspace.updateProfile",
        "integration.list",
        "readiness.check",
        "workflowExtension.upsert",
        "workflowExtension.delete",
        "tracker.rejectTask",
        "tracker.reopen",
        "runPlan.recover",
        "runPlan.reopen",
        "executionContext.create",
        "executionContext.get",
        "executionContext.list",
        "executionContext.discover",
        "executionContext.resolve",
        "executionContext.update",
        "executionContext.link",
        "executionContext.unlink",
        "executionContext.artifact.list",
        "executionContext.artifact.read",
        "skillPreset.list",
        "skillPreset.describe",
        "skillPreset.resolveForWorkflow",
    } <= names

    described = registry.get("action.execute").describe_out()

    assert described.name == "action.execute"
    assert described.mutating is True
    assert described.surfaces["mcp"].enabled is True
    assert described.surfaces["rest"].enabled is True
    assert described.surfaces["cli"].enabled is True
    assert described.grant_policy == "run-plan-step-action-ref"
    assert described.response_policy.default_mode == "raw"
    assert described.response_policy.allowed_modes == ["raw"]
    assert described.response_policy.raw_only_reason is not None
    assert "properties" in described.input_schema
    assert "project_id" in described.input_schema["properties"]
    assert described.input_schema["properties"]["response_mode"]["enum"] == [
        "raw",
        "standard",
        "verbose",
    ]
    assert "WriteEnvelope" in described.output_schema["title"]
    assert any("run_token" in item for item in described.prerequisites)
    assert described.examples[0].arguments["action_ref"] == "utils.sitemap.fetch"

    direct_action = registry.get("action.run").describe_out()
    assert direct_action.surfaces["mcp"].enabled is True
    assert direct_action.surfaces["rest"].enabled is True
    assert direct_action.surfaces["cli"].command == "actions run"
    assert direct_action.grant_policy == "direct-action-policy"
    assert direct_action.response_policy.default_mode == "raw"
    assert direct_action.response_policy.allowed_modes == ["raw"]
    assert any("confirm_direct=true" in item for item in direct_action.prerequisites)
    assert any("intent_id or idempotency_key" in item for item in direct_action.prerequisites)

    action_list = registry.get("action.list").describe_out()
    assert action_list.category == "actions"
    assert action_list.surfaces["mcp"].enabled is True
    assert action_list.surfaces["rest"].enabled is True
    assert action_list.surfaces["cli"].command == "actions list"
    assert action_list.grant_policy == "direct-read"
    assert any("currently usable action refs" in item for item in [action_list.purpose])

    integration_list = registry.get("integration.list").describe_out()
    assert integration_list.category == "setup"
    assert integration_list.surfaces["mcp"].enabled is True
    assert integration_list.surfaces["rest"].enabled is True
    assert integration_list.surfaces["cli"].command == "ops call integration.list"
    assert integration_list.grant_policy == "direct-read"
    assert any(
        "compact project integration inventory" in item for item in [integration_list.purpose]
    )

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

    skill_preset_resolution = registry.get("skillPreset.resolveForWorkflow").describe_out()
    assert skill_preset_resolution.category == "agents"
    assert skill_preset_resolution.surfaces["mcp"].enabled is True
    assert skill_preset_resolution.surfaces["rest"].enabled is True
    assert skill_preset_resolution.surfaces["cli"].command == (
        "ops call skillPreset.resolveForWorkflow"
    )
    assert skill_preset_resolution.grant_policy == "direct-read"
    assert any("workflow template key" in item for item in skill_preset_resolution.prerequisites)

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
    assert operation_list.input_schema["properties"]["response_mode"]["enum"] == [
        "compact",
        "raw",
        "standard",
        "verbose",
    ]
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

    readiness = registry.get("readiness.check").describe_out()
    assert readiness.category == "setup"
    assert readiness.surfaces["mcp"].enabled is True
    assert readiness.surfaces["rest"].enabled is True
    assert readiness.surfaces["cli"].command == "ops call readiness.check"
    assert readiness.grant_policy == "direct-read"
    assert any("global auth.status gaps" in item for item in readiness.prerequisites)

    workflow_extension = registry.get("workflowExtension.upsert").describe_out()
    assert workflow_extension.category == "workflow"
    assert workflow_extension.surfaces["mcp"].enabled is True
    assert workflow_extension.surfaces["rest"].enabled is True
    assert workflow_extension.surfaces["cli"].command == "ops call workflowExtension.upsert"
    assert workflow_extension.grant_policy == "direct-setup-write"
    assert any("base workflow should stay generic" in item for item in [workflow_extension.purpose])

    run_plan = registry.get("runPlan.claimStep").describe_out()
    assert run_plan.surfaces["mcp"].enabled is True
    assert run_plan.surfaces["rest"].enabled is True
    assert run_plan.surfaces["cli"].command == "run-plans claim-step"
    assert run_plan.grant_policy == "run-plan-controller"
    assert any("run_token" in item for item in run_plan.prerequisites)

    run_plan_recover = registry.get("runPlan.recover").describe_out()
    assert run_plan_recover.surfaces["mcp"].enabled is True
    assert run_plan_recover.surfaces["rest"].enabled is True
    assert run_plan_recover.surfaces["cli"].command == "run-plans recover"
    assert run_plan_recover.grant_policy == "direct-run-audit-write"
    assert any("system-recoverable" in item for item in run_plan_recover.prerequisites)

    run_plan_reopen = registry.get("runPlan.reopen").describe_out()
    assert run_plan_reopen.surfaces["mcp"].enabled is True
    assert run_plan_reopen.surfaces["rest"].enabled is True
    assert run_plan_reopen.surfaces["cli"].command == "run-plans reopen"
    assert run_plan_reopen.grant_policy == "direct-run-audit-write"
    assert any("human-readable reason" in item for item in run_plan_reopen.prerequisites)

    tracker_next = registry.get("tracker.next").describe_out()
    assert tracker_next.surfaces["mcp"].enabled is True
    assert tracker_next.surfaces["rest"].enabled is True
    assert tracker_next.surfaces["cli"].command == "tracker next"
    assert tracker_next.grant_policy == "direct-read"
    assert tracker_next.input_schema["properties"]["response_mode"]["enum"] == [
        "compact",
        "raw",
        "standard",
        "verbose",
    ]
    assert any("bounded project work context" in item for item in tracker_next.when_to_use)

    tracker_patch = registry.get("tracker.patch").describe_out()
    assert tracker_patch.surfaces["mcp"].enabled is True
    assert tracker_patch.surfaces["rest"].enabled is True
    assert tracker_patch.surfaces["cli"].command == "tracker patch"
    assert tracker_patch.grant_policy == "direct-tracker-write"
    assert "WriteEnvelope" in tracker_patch.output_schema["title"]

    tracker_reject = registry.get("tracker.rejectTask").describe_out()
    assert tracker_reject.surfaces["mcp"].enabled is True
    assert tracker_reject.surfaces["rest"].enabled is True
    assert tracker_reject.surfaces["cli"].command == "tracker reject-task"
    assert tracker_reject.grant_policy == "direct-tracker-write"
    assert "one clear terminal state" in tracker_reject.purpose

    tracker_reopen = registry.get("tracker.reopen").describe_out()
    assert tracker_reopen.surfaces["mcp"].enabled is True
    assert tracker_reopen.surfaces["rest"].enabled is True
    assert tracker_reopen.surfaces["cli"].command == "tracker reopen"
    assert tracker_reopen.grant_policy == "direct-tracker-write"
    assert "plain tracker task or a mirrored workflow run plan" in tracker_reopen.purpose


def test_operation_registry_surface_filter() -> None:
    registry = build_operation_registry()

    cli_names = {item.name for item in registry.by_surface("cli")}
    assert cli_names == {item.name for item in registry.all()}
    assert {
        "workflowTemplate.describe",
        "resource.query",
        "context.query",
        "budget.set",
        "run.start",
        "workspace.updateProfile",
    } <= cli_names
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
