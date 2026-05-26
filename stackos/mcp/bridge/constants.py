"""Agent-facing MCP bridge tool surface constants."""

from __future__ import annotations

from stackos.workflows.run_plan_grants import (
    RUN_PLAN_ADMIN_ONLY_TOOL_NAMES,
    RUN_PLAN_CONTROLLER_TOOL_NAMES,
    RUN_PLAN_GRANTABLE_TOOL_NAMES,
)

_AGENT_VISIBLE_TOOL_ORDER: tuple[str, ...] = (
    # Repo/project setup.
    "workspace.startSession",
    "workspace.resolve",
    "workspace.connect",
    "workspace.listBindings",
    "workspace.updateProfile",
    "auth.status",
    "auth.test",
    "operation.list",
    "operation.describe",
    "toolProfile.resolve",
    "ingressEndpoint.configure",
    "ingressEndpoint.refresh",
    "ingressEndpoint.routes",
    "ingressEndpoint.sync",
    "ingressEndpoint.status",
    "localAgentChat.createMessage",
    "communication.send",
    "communication.reply",
    "communicationProfile.list",
    "communicationProfile.get",
    "communicationProfile.upsert",
    "communicationSurface.list",
    "communicationSurface.upsert",
    "communicationContact.list",
    "communicationContact.upsert",
    "communicationMembership.list",
    "communicationMembership.upsert",
    "communicationTarget.list",
    "communicationTarget.resolve",
    "communicationTarget.upsert",
    "communicationRoute.list",
    "communicationRoute.upsert",
    "communicationContext.query",
    "action.describe",
    "action.validate",
    "action.run",
    "agentRequest.list",
    "agentRequest.get",
    "agentRequest.claim",
    "agentRequest.release",
    "agentRequest.linkRunPlan",
    "agentRequest.prepareRunPlan",
    "agentRequest.complete",
    "agentRequest.ignore",
    "plugin.list",
    "catalog.list",
    "catalog.describe",
    "capability.list",
    "capability.describe",
    "provider.list",
    "provider.describe",
    "resource.get",
    "resource.query",
    "artifact.get",
    "artifact.query",
    "context.query",
    "context.timeline",
    "learning.query",
    "experiment.query",
    "decision.query",
    "workflowTemplate.list",
    "workflowTemplate.describe",
    "workflowTemplate.validate",
    "runPlan.create",
    "runPlan.validate",
    "runPlan.start",
    "runPlan.get",
    "runPlan.list",
    "tracker.status",
    "tracker.get",
    "tracker.next",
    "tracker.blockers",
    "tracker.brief",
    "tracker.why",
    "tracker.execute",
    "tracker.verify",
    "tracker.history",
    "tracker.changed",
    "tracker.search",
    "tracker.createTask",
    "tracker.createTicket",
    "tracker.updateTask",
    "tracker.updateTicket",
    "tracker.patch",
    "tracker.pick",
    "tracker.release",
    "tracker.linkRunPlan",
    "meta.enums",
    "run.get",
    "run.list",
    "run.heartbeat",
    "run.abort",
)
_AGENT_VISIBLE_TOOL_NAMES = frozenset(_AGENT_VISIBLE_TOOL_ORDER)
_AGENT_COMPACT_DEFAULT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "workspace.startSession",
        "workspace.resolve",
        "workspace.connect",
        "auth.status",
        "toolProfile.resolve",
        "ingressEndpoint.routes",
        "ingressEndpoint.status",
        "communication.send",
        "communication.reply",
        "communicationProfile.list",
        "communicationProfile.get",
        "communicationTarget.list",
        "communicationTarget.resolve",
        "tracker.status",
        "tracker.next",
        "tracker.brief",
        "tracker.verify",
        "action.describe",
        "catalog.describe",
    }
)
_AGENT_RESPONSE_MODE_FIELD = "response_mode"
_AGENT_GLOBAL_DISCOVERY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "workspace.startSession",
        "workspace.resolve",
        "workspace.connect",
        "plugin.list",
        "catalog.list",
        "catalog.describe",
        "capability.list",
        "capability.describe",
        "provider.list",
        "provider.describe",
        "operation.list",
        "operation.describe",
        "action.describe",
        "action.validate",
        "meta.enums",
    }
)
_TOOLBOX_DESCRIBE_TOOL = "toolbox.describe"
_TOOLBOX_CALL_TOOL = "toolbox.call"
_TOOLBOX_TOOL_NAMES = frozenset({_TOOLBOX_DESCRIBE_TOOL, _TOOLBOX_CALL_TOOL})
_AGENT_ADMIN_GATED_TOOL_NAMES: frozenset[str] = frozenset(RUN_PLAN_ADMIN_ONLY_TOOL_NAMES)
_AGENT_RUN_PLAN_GATED_TOOL_NAMES: frozenset[str] = frozenset(RUN_PLAN_GRANTABLE_TOOL_NAMES)
_AGENT_STEP_GATED_TOOL_NAMES: frozenset[str] = frozenset(RUN_PLAN_CONTROLLER_TOOL_NAMES)
_AGENT_GATED_TOOL_NAMES: frozenset[str] = (
    _AGENT_ADMIN_GATED_TOOL_NAMES | _AGENT_RUN_PLAN_GATED_TOOL_NAMES
)

# Tools that stay out of the advertised MCP list but are still useful during
# setup when an agent explicitly asks the bridge to describe/call them.
_AGENT_SETUP_TOOLBOX_NAMES: frozenset[str] = frozenset(
    {
        "budget.list",
        "budget.queryProject",
        "budget.set",
        "budget.update",
        "cost.queryAll",
        "cost.queryProject",
        "run.children",
        "run.cost",
        "run.finish",
        "run.insertStep",
        "run.listStepCalls",
        "run.listSteps",
        "run.recordStepCall",
        "run.start",
        "schedule.list",
        "schedule.remove",
        "schedule.set",
        "schedule.toggle",
        "sitemap.fetch",
    }
)
_AGENT_BASE_TOOLBOX_NAMES = _AGENT_VISIBLE_TOOL_NAMES | _AGENT_SETUP_TOOLBOX_NAMES
