# UI Usability Audit

Date: 2026-05-24

This audit covers the StackOS operator UI after the project pivot to an
agent-first, plugin-oriented runtime. The UI is not the workflow engine. It is
the human inspection and local-admin setup surface for projects, connections,
catalog state, resources, run plans, agent requests, action calls, and
communications setup.

## Product Principles

1. **Read-first, not dashboard-first.** The main object of a page should appear
   in the first viewport. Metrics are useful, but they should be compact strips
   or table badges, not large sections that push the list or detail down.

2. **One page answers one inspection question.** Examples:
   `Connections` answers "what can agents safely use and how is it wired?",
   `Operations` answers "what operation can be called through MCP/REST/CLI?",
   `Action Calls` answers "what external call happened and what did it return?"

3. **Use master/detail for inspectable objects.** Ledgers, queues, templates,
   resources, and operation registries should keep the selected detail beside
   the list on wide screens. Avoid forcing operators to select a row, scroll
   below the table, then scroll back.

4. **Separate setup exceptions from observation.** The UI may store credentials
   and static setup because secrets must stay daemon-side. Most other product
   writes should remain agent/MCP driven and shown as state, audit, or
   configuration.

5. **Generic StackOS nouns first.** Prefer project, connection, operation,
   action call, agent request, template, run, resource, artifact, surface, and
   target. Avoid domain-specific workflow screens unless a plugin explicitly
   contributes a generic renderer payload.

6. **Dense does not mean cramped.** Use 32px rows, compact metric cards,
   sticky detail panels, truncation, and internal scroll for long lists. Do not
   solve density by removing labels or hiding status.

## Detailed Findings

### Navigation

- The sidebar had too many peer destinations: overview, plugins, capabilities,
  connections, operations, action calls, agent requests, workflow templates,
  runs, data, resources, setup, schedules, cost, plus plugin sections.
- This is technically accurate but cognitively flat. Future consolidation
  should group routes around human tasks: setup, connections, catalog,
  execution, data, resources.
- Concrete example: `Operations`, `Action Calls`, `Agent Requests`, and `Runs`
  all belong to execution inspection. They can remain routes, but the visual
  hierarchy should make their relationship obvious.

### Connections

- The page mixed connected services, communication profiles, surfaces, targets,
  ingress routes, Telegram profile setup, diagnostics, and drawers in one long
  vertical flow.
- The high-value read path is now sectioned: service credentials, communication
  setup, Telegram profiles, and diagnostics. This keeps setup controls visible
  without making every operator read every subsystem at once.
- Long ingress URLs and surface refs must wrap or stay inside internal scroll;
  horizontal overflow in setup pages is a usability bug.

### Catalog Pages

- `Plugins` and `Capabilities` duplicated hundreds of action-contract summaries
  that are better inspected in `Operations`.
- These pages should stay catalog-oriented: installed plugins, provider count,
  capability count, and a link into operation details.
- Concrete example: `Capabilities` should show providers and capability rows,
  then summarize action availability. Full input/output schema belongs in
  `Operations`.

### Operations, Templates, Action Calls, Agent Requests, Resources

- These pages are all inspection surfaces and should use list/detail layout.
- Selected rows need a visible selected state, not just an implicit detail
  panel.
- JSON-heavy detail panes should be bounded with internal scroll, especially
  when used beside a table.

### Project Data

- Eight data categories are useful, but eight large counters consume too much
  space. Compact metric cards keep the scan value without dominating the
  screen.
- Future work should make the active tab and selected detail query-backed so
  agents and operators can share exact UI state.

### Empty And Loading States

- A good empty state answers: what is missing, where would it come from, and
  whether the next step is UI setup or agent execution.
- Avoid generic "No rows" when the object has operational meaning. Example:
  "No agent requests match these filters" is better than "No rows"; "No
  communication profiles configured" should also mention that profiles are
  static setup and agents still decide what to run.

## Screen-By-Screen Audit

| Route | Primary Job | Detailed Finding | Direction |
| --- | --- | --- | --- |
| `/` | Entry redirect and daemon readiness | The root should not behave like a marketing page. It should move operators to projects or show daemon readiness when auth/bootstrap fails. | Keep lightweight; no separate landing surface. |
| `/auth-error` | Recover daemon UI auth | This route is a repair state, not a product page. It should stay direct and avoid secondary navigation that implies project context exists. | Keep as a narrow recovery screen. |
| `/projects` | Choose or inspect project bindings | The project table is sparse but acceptable for now. It should eventually include last activity and setup readiness so operators can choose a project without opening each one. | Add readiness/activity in a later pass. |
| `/projects/:id/overview` | Project control-room snapshot | Overview currently has useful counts and recent rows. The risk is dashboard-first composition; recent runs/resources should stay visible in the first viewport. | Keep counts compact and make row/action labels specific. |
| `/projects/:id/setup` | Readiness checklist | Setup is an inspection page. It should surface missing prerequisites and links to the exact setup surface, not workflow-specific actions. | Keep read-first; only refresh is needed. |
| `/projects/:id/schedules` | Scheduled daemon jobs | Schedules are read-only operational state. Empty states should explain whether no scheduler is configured or just no jobs exist. | No new workflow UI. |
| `/projects/:id/cost-budget` | Budget/cost inspection | The dashboard style is acceptable because cost is inherently summarized, but tables/history should remain visible without deep scrolling. | Keep metrics compact; avoid mutation controls unless generic budget setup is added. |
| `/projects/:id/plugins` | Installed plugin catalog | Inline action contracts made the page thousands of pixels tall and duplicated `Operations`. | Summarize plugins and link to Operations for action detail. |
| `/projects/:id/capabilities` | Capabilities and providers | This should explain what the project can use, not render every provider action schema inline. | Keep capabilities/providers as bounded tables; summarize actions. |
| `/projects/:id/connections` | Credential and communication setup | This page mixed services, profiles, surfaces, ingress, Telegram setup, and diagnostics in one long flow. | Section services, comms, Telegram, and diagnostics; keep secrets daemon-side. |
| `/projects/:id/operations` | MCP/REST/CLI operation registry | Operation details below the table slowed scanning. The operation list can be large. | Use master/detail and bounded table scroll. |
| `/projects/:id/action-calls` | Audited external call ledger | Ledger inspection needs row + redacted request/response together. A full-page table hides the selected call. | Use master/detail and bounded table scroll. |
| `/projects/:id/agent-requests` | Trigger/request queue | Queue state needs detail context beside the selected request. The "select newest" control is useful but should remain secondary. | Use master/detail and visible active row. |
| `/projects/:id/workflow-templates` | Reusable template contracts | Template detail below the list made comparison slow. Long template lists should not grow the entire page. | Use master/detail and bounded list scroll. |
| `/projects/:id/data` | Project memory and history | Eight data categories are valid, but counters and tabs must stay compact so the active data table remains primary. | Use compact metric cards and bounded tables. |
| `/projects/:id/resources` | Resource schemas, records, artifacts | Schemas, records, details, and artifacts were sequential full-width regions. This contradicted the list/detail principle. | Keep filters first; place schemas/records left and selected record/artifacts right. |
| `/projects/:id/runs` | Run list | Runs already has a strong table-first shape. The filters should stay compact and query-backed in future. | Keep route focused on run inspection. |
| `/projects/:id/runs/:run_id` | Run detail | Run detail is naturally vertical because it contains steps, context, decisions, and linked artifacts. The risk is too many nested panels. | Keep generic renderers and avoid workflow-specific UI. |

## Implementation Guidelines

- Prefer `UiMetricCard` for counters instead of hand-built `UiPanel` metric
  cards.
- Prefer `DataTable` with `interactive` and `selected-id` for master/detail
  pages.
- Keep details beside lists at `xl` and below the list on narrower screens.
- Limit the amount of repeated closed-summary content rendered on catalog
  pages. Closed details still consume vertical space when there are hundreds of
  rows.
- Use `break-all` or bounded scroll for refs, URLs, and opaque IDs.
- Keep `Connections` as the only broad setup surface for credentials unless a
  future generic setup route is introduced.

## Implemented In This Pass

- `Connections` now has compact section navigation for services,
  communication setup, Telegram profiles, and diagnostics.
- Communication setup lists use internal scroll and long ingress URLs wrap.
- `Plugins` and `Capabilities` no longer render every action contract inline;
  they summarize and link to `Operations`.
- `Operations`, `Workflow Templates`, `Action Calls`, `Agent Requests`, and
  `Resources` use a more direct list/detail inspection pattern.
- `DataTable` supports `selected-id` so active rows are visible.
- `DataTable` supports a `max-height` prop so long ledgers and catalog tables
  scroll inside the table region instead of making the whole page several
  screens tall.
- Repeated metric strips were migrated to `UiMetricCard` where touched.

## Next Refinements

- Collapse the sidebar into fewer human task groups while keeping routes
  addressable.
- Extract a reusable master/detail layout after one more pass confirms the
  pattern is stable across views.
- Query-back selected rows and active tabs for operations, resources,
  templates, and project data.
- Split `ConnectionsView.vue` into service, communication setup, Telegram
  profile, and diagnostics components once the UI behavior is verified.
