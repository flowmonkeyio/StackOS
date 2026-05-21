# StackOS Plan

StackOS is the clean product boundary for this repository.

## Mission

Provide a local project runtime where agents can install plugins, connect
providers once, create reusable workflow templates, instantiate run plans, call
tools without receiving secrets, and build durable project memory.

## Product Contract

- Core is domain-agnostic.
- Plugins own domain resources, providers, actions, and templates.
- Agents decide strategy and next actions.
- Tools validate configuration, execute explicit calls, and persist output.
- Credentials remain daemon-owned.
- UI renders generic project, plugin, template, run-plan, resource, artifact,
  auth, context, learning, experiment, decision, and action-call state.

## Primary Objects

- Project
- Plugin
- Capability
- Provider
- Auth provider and credential ref
- Resource and resource record
- Artifact
- Workflow template
- Run plan and run-plan step
- Run, run step, and action call
- Context snapshot
- Learning
- Experiment and observation
- Decision
- Approval request

## Runtime Flow

1. User creates or opens a project.
2. User enables relevant plugins.
3. User connects provider credentials through local auth flows.
4. Agent selects or creates a workflow template.
5. Agent creates a run plan for the concrete goal.
6. Agent queries bounded context.
7. Agent claims steps and executes validated actions.
8. Daemon resolves credentials inside the tool process.
9. Agent records outputs, artifacts, learnings, observations, and decisions.
10. Run plan completes with an auditable summary.

## Immediate Delivery Focus

- Keep the repository aligned with StackOS naming and architecture.
- Keep removed flows out of routes, MCP tools, UI, tests, install assets, and
  documentation.
- Verify no-secret action execution.
- Expand plugins beyond SEO using the same core primitives.
- Keep docs and `AGENTS.md` current with the actual system.
