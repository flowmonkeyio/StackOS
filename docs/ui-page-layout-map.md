# StackOS Desktop Page Layout Map

Status: layout contract for every routed desktop page.

## Shared contract

`App.vue` owns the persistent navigation, project switcher, and scrollable main
viewport. The project picker directly precedes the scrollable project links.
Overview, Accounts, and Backups sit in a separate App footer above the theme
control and remain available without a project. `UiPageShell` owns page rhythm
only: it is always `width: 100%` and
`min-width: 0`, with no centered maximum-width container. Each route has
exactly one page-shell owner. Inner cards, tables, maps, and readable text may
set their own useful widths, but a page may not silently reintroduce a global
content cap.

The same rule applies to empty, loading, error, and detail states. A page must
not become narrower when its data or selected mode changes.

## Route and wrapper ownership

| Route | Human surface | Page-shell owner | Inner layout |
| --- | --- | --- | --- |
| `/` | Overview | `HomeView` | entire local service and host status, continuous active-task list across accessible projects, existing Agent usage with date/group and Active/Archived/All controls, Connected tools |
| `/auth-error` | Authentication recovery | `AuthErrorView` | centered recovery content within the full shell |
| `/projects/:id` | Project Home | `HomeConsoleView` | scoped active-task list, existing scoped Agent usage and coverage, Connected tools; task rows open the selected graph directly |
| `/setup` | Backups | `LocalSetupView` | installation-wide backup settings and coverage |
| `/projects/:id/schedules` | Automations | `ProjectDetailView` | shared project-detail header and schedules panel |
| `/projects/:id/cost-budget` | Spend | `ProjectDetailView` | shared project-detail header and budget panel |
| `/projects/:id/tasks` | Work | `TaskTrackerView` | inline Board / Graph command surface; default seven-column Board; map-only task picker and explicit side inspector; one persistent task/ticket detail dialog |
| `/projects/:id/plugins` | Plugins | `PluginsView` | plugin catalog/cards |
| `/projects/:id/plugins/:pluginSlug` | Domain | `PluginDomainView` | workflows and canonical saved information/files |
| `/projects/:id/plugins/:pluginSlug/workflows/:templateKey` | Workflow details | `WorkflowDetailView` | effective workflow contract and the same scoped records/files |
| `/projects/:id/workflows/:templateKey` | Configured workflow details | `WorkflowDetailView` | exact project/repository/user workflow contract selected from Home; return to that project's Home |
| `/projects/:id/library` | Library | `LibraryView` | Saved information / Files with typed source identity, filters and shared reader |
| `/projects/:id/agents` | Agents | `AgentsView` | native project sources, Add/Edit and explicit saved-default adoption |
| `/projects/:id/finance` | Finance | `FinanceView` | Overview / Expenses & income / Reports; contextual request inspection without inferred task links |
| `/accounts` | Accounts | `AccountsView` | global named Account inventory and reusable Account setup |
| `/projects/:id/connections` | Connections | `ConnectionsView` | Account attachment plus project-bound communication setup |

`/projects` redirects to the portfolio and does not own a shell. Replaced human
routes have no forwarding shims. Their shared backend APIs and records remain.
The configuration child components deliberately do not create a second
shell; `ProjectDetailView` supplies it. Project Home is the exception inside
that nested router because `HomeConsoleView` owns its complete page chrome.

## Mode continuity

Work uses one wrapper for Board and Graph:

```text
Project page header
  -> Board / Graph switch + view-specific controls
  -> Board filters, or graph task picker + whole-task token total
  -> mode content (only this region changes)
```

Changing project preserves the current operating route. Board is the default
and shows whole-task token totals on its cards. Graph retains its selected
task independently of Board filters; the command panel shows that task's same
API-projected total, while ticket nodes show their own usage. Graph filters
change visible tickets without changing the task total. Token coverage stays
visible at narrow widths, wrapping below the controls when needed. Both Home
surfaces pair each activity bar with a wrapping group identity and exact count.

Library URLs preserve `tab=saved|files`, `plugin`, `type` and
`item=<source-kind>:<id>`. Switching projects preserves reusable filters and
domain/workflow identity, clearing selected record/file/task/ticket/request IDs.
Contextual configured-workflow links carry the exact template key. Plugin workflows
retain their plugin slug and domain route; project/repository/user workflows
without a plugin use the contextual `/projects/:id/workflows/:templateKey`
detail route. This route has no catalog/list owner. The plugin catalog remains
distinct from domain and workflow pages.
