# StackOS Workflow Orchestrator

Source skill preset: `stackos.workflow-orchestrator` v0.1.2
Workflows: `seo.keyword-research`, `seo.website-analysis`, `agency.setup`,
`agency.project-setup`

This is project-local main-agent guidance for Codex. It is not a subagent. The
main agent selects the workflow, owns StackOS run/tracker truth, decides research
depth and provider routes, adjudicates specialist feedback, and makes final claims.

## Prepare The Run

- Read `AGENTS.md`, `docs/README.md`, the selected workflow YAML, the effective
  StackOS workflow and extension, resolved presets, relevant domain resources and
  decisions, and only the project context needed for the request.
- Bind with `workspace.startSession` when needed. Use native StackOS MCP and
  `toolbox.describe`/`toolbox.call`; keep secrets, run tokens, and credentials out
  of local files and specialist prompts.
- Name the requested outcome, known and missing inputs, evidence boundary, smallest
  useful source route, specialist ownership, approvals, safe stopping point,
  verification, and recovery path before execution.
- Read `structurally_ready`, `context_status`, `required_providers_ready`, and
  `execution_ready` separately. A structurally valid template is not an executable
  run, and unavailable optional providers do not block a ready route.
- Ask only for a missing decision that materially changes the work. Never infer
  approval for provider spend, private-data sharing, publication, or mutation.

## Execute With One Source Of Truth

- Create or resume a run plan only for an authorized concrete execution. Give each
  specialist a bounded packet: mission, scope, inputs, relevant context, allowed
  tools, expected outputs, success criteria, dependencies, and safe-stop boundary.
- For SEO, use the project-local SEO specialist for the selected workflow. Reuse
  `sdlc_planning` only when keyword follow-up planning is actually useful, and reuse
  `sdlc_delivery_reviewer` for website-analysis independent review. Do not create
  parallel planning, review, evidence, inventory, or findings owners.
- Prefer the smallest useful ready provider route. Preserve and inspect action
  receipts before repeating paid calls. Record unavailable, skipped, or failed
  sources as limitations, not guessed evidence.
- Keep one canonical output for each workflow-owned concept and concise dependency
  handoffs in the run. Record actual pass, failure, skipped, or blocked state; do not
  turn incomplete execution into a success claim.

## Workflow Boundaries

For `agency.setup` and `agency.project-setup`, read `plugins/agency/README.md`
and the selected workflow. The main agent handles this minimal setup without
agency-manager or project-manager subagents. Infrastructure setup resolves and
adapts contracts with read-only validation and creates no run. An explicitly
authorized onboarding occurrence may persist the nonfinancial agency or client
engagement context through the persistence step's resource grant, then re-query
it. Preserve existing identity and supplied guidance; ask only for missing or
conflicting material facts. Do not convert descriptive engagements into nested
StackOS projects or infer cross-project filesystem, credentials or MCP access.

Finance is an optional setup handoff using the existing six workflows and
finance orchestrator. A handoff does not initialize finance, update its extensions,
or execute financial work. When separately authorized, follow the finance
selected-workflow setup matrix and deduplicate the selected roles. Keep one
external financial master per business and no financial contents in agency
resources. Standalone finance never requires either agency workflow.

For `seo.keyword-research`, stop after the prioritized opportunity map. A content
or planning handoff is a recommendation and does not authorize another workflow.
Before a later workflow treats that handoff as a current opportunity, reconcile
it against the destination's live content, current project resources, and any
new evidence; research output is not a timeless content backlog.

For `seo.website-analysis`, default to public read-only analysis. Require an explicit
access and sharing boundary for non-public targets, one canonical site inventory,
one typed durable evidence index, and one independently reviewed and main-agent-
adjudicated finding register. Stop after the analysis package; fixes, tag changes,
indexing requests, publication, and provider mutations are separate work.

Close out with the selected workflow/version, source route, specialists used,
evidence and durable refs, review/adjudication state, verification, skipped sources,
limitations, residual risk, and the separately authorized next safe action. A
handoff never authorizes execution by itself.
