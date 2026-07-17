# StackOS Workflow Orchestrator

Source skill preset: `stackos.workflow-orchestrator` v0.1.1
Workflows: `seo.keyword-research`, `seo.website-analysis`

This is project-local main-agent guidance for Codex. It is not a subagent. The
main agent selects the workflow, owns StackOS run/tracker truth, decides research
depth and provider routes, adjudicates specialist feedback, and makes final claims.

## Prepare The Run

- Read `AGENTS.md`, `docs/README.md`, the selected workflow YAML, the effective
  StackOS workflow and extension, resolved presets, relevant SEO resources and
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
- Use the project-local SEO specialist for the selected workflow. Reuse
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

For `seo.keyword-research`, stop after the prioritized opportunity map. A content
or planning handoff is a recommendation and does not authorize another workflow.

For `seo.website-analysis`, default to public read-only analysis. Require an explicit
access and sharing boundary for non-public targets, one canonical site inventory,
one typed durable evidence index, and one independently reviewed and main-agent-
adjudicated finding register. Stop after the analysis package; fixes, tag changes,
indexing requests, publication, and provider mutations are separate work.

Close out with the selected workflow/version, source route, specialists used,
evidence and durable refs, review/adjudication state, verification, skipped sources,
limitations, residual risk, and the separately authorized next safe action. A
handoff never authorizes execution by itself.
