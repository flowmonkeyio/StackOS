# StackOS Desktop Page Layout Map

Status: layout contract for every routed desktop page.

## Shared contract

`App.vue` owns the persistent navigation, project switcher, and scrollable main
viewport. `UiPageShell` owns page rhythm only: it is always `width: 100%` and
`min-width: 0`, with no centered maximum-width container. Each route has
exactly one page-shell owner. Inner cards, tables, maps, and readable text may
set their own useful widths, but a page may not silently reintroduce a global
content cap.

The same rule applies to empty, loading, error, and detail states. A page must
not become narrower when its data or selected mode changes.

## Route and wrapper ownership

| Route | Human surface | Page-shell owner | Inner layout |
| --- | --- | --- | --- |
| `/` | Portfolio | `HomeView` | portfolio overview, workload visualization, project directory |
| `/auth-error` | Authentication recovery | `AuthErrorView` | centered recovery content within the full shell |
| `/projects/:id` | Project Home | `HomeConsoleView` | priority banner, supervision metrics, active work, outcomes |
| `/projects/:id/setup` | Setup | `ProjectDetailView` | shared project-detail header and setup panel |
| `/projects/:id/schedules` | Automation | `ProjectDetailView` | shared project-detail header and schedules panel |
| `/projects/:id/cost-budget` | Spend | `ProjectDetailView` | shared project-detail header and budget panel |
| `/projects/:id/inbox` | Attention | `InboxView` | filters and human-action master/detail |
| `/projects/:id/activity` | Activity | `ActivityView` | grouped outcomes master/detail with audit disclosure |
| `/projects/:id/tasks` | Work | `TaskTrackerView` | one shared command surface; map, stories, and tickets swap below it |
| `/projects/:id/runs` | Runs | `RunsView` | run inventory |
| `/projects/:id/runs/:run_id` | Run detail | `RunsView` | selected run journey and evidence |
| `/projects/:id/plugins` | Plugins | `PluginsView` | plugin catalog/cards |
| `/projects/:id/capabilities` | Capabilities | `CapabilitiesView` | capability inventory |
| `/projects/:id/connections` | Connections | `ConnectionsView` | persistent section navigation and connection workspace |
| `/projects/:id/operations` | Operations | `OperationsView` | operation catalog and detail |
| `/projects/:id/action-calls` | Action history | `ActionCallsView` | action inventory and result detail |
| `/projects/:id/agent-requests` | Agent requests | `AgentRequestsView` | request intake/handoff inventory and detail |
| `/projects/:id/agent-presets` | Agent roles | `AgentPresetsView` | preset catalog and detail drawer |
| `/projects/:id/workflow-templates` | Workflow library | `WorkflowTemplatesView` | template inventory and wide visual workflow guide |
| `/projects/:id/data` | Project data | `ProjectDataView` | generic object sections and detail |
| `/projects/:id/resources` | Resources | `ResourceExplorerView` | resource explorer and selected resource detail |

`/projects` and `/projects/:id/overview` are redirects and do not own shells.
The three setup-family child components deliberately do not create a second
shell; `ProjectDetailView` supplies it. Project Home is the exception inside
that nested router because `HomeConsoleView` owns its complete page chrome.

## Mode continuity

Work uses one wrapper for all three views:

```text
Project page header
  -> task selector + Dependency map / Stories / Tickets
  -> shared filters and task totals
  -> mode content (only this region changes)
```

Changing project preserves the current operating route. Changing Work mode
does not move the selector, view control, filters, or page boundaries. The
Dependency map is the default because it exposes sequence, parallel work, and
blockers fastest; Stories translates the same state into goals/outcomes, and
Tickets provides exact inventory.
