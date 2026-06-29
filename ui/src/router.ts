import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import HomeView from './views/HomeView.vue'
import AuthErrorView from './views/AuthErrorView.vue'
import ProjectDetailView from './views/ProjectDetailView.vue'
import HomeConsoleView from './views/HomeConsoleView.vue'
import InboxView from './views/InboxView.vue'
import ActivityView from './views/ActivityView.vue'
import SetupStatusTab from './views/project-detail/SetupStatusTab.vue'
import SchedulesTab from './views/project-detail/SchedulesTab.vue'
import CostBudgetTab from './views/project-detail/CostBudgetTab.vue'
import RunsView from './views/RunsView.vue'
import PluginsView from './views/PluginsView.vue'
import CapabilitiesView from './views/CapabilitiesView.vue'
import ConnectionsView from './views/ConnectionsView.vue'
import OperationsView from './views/OperationsView.vue'
import WorkflowTemplatesView from './views/WorkflowTemplatesView.vue'
import AgentPresetsView from './views/AgentPresetsView.vue'
import ProjectDataView from './views/ProjectDataView.vue'
import ResourceExplorerView from './views/ResourceExplorerView.vue'
import ActionCallsView from './views/ActionCallsView.vue'
import AgentRequestsView from './views/AgentRequestsView.vue'
import TaskTrackerView from './views/TaskTrackerView.vue'

const routes: RouteRecordRaw[] = [
  { path: '/', name: 'home', component: HomeView },
  { path: '/auth-error', name: 'auth-error', component: AuthErrorView },
  { path: '/projects', redirect: '/' },
  {
    path: '/projects/:id',
    component: ProjectDetailView,
    children: [
      // Home is the operations console; it owns its own page chrome, so
      // ProjectDetailView renders it without the setup-tab header wrapper.
      { path: '', name: 'project-home', component: HomeConsoleView },
      { path: 'overview', redirect: (to) => ({ path: `/projects/${to.params.id}` }) },
      { path: 'setup', name: 'project-detail-setup', component: SetupStatusTab },
      { path: 'schedules', name: 'project-detail-schedules', component: SchedulesTab },
      { path: 'cost-budget', name: 'project-detail-cost-budget', component: CostBudgetTab },
    ],
  },
  { path: '/projects/:id/inbox', name: 'project-inbox', component: InboxView },
  { path: '/projects/:id/activity', name: 'project-activity', component: ActivityView },
  { path: '/projects/:id/plugins', name: 'project-plugins', component: PluginsView },
  { path: '/projects/:id/capabilities', name: 'project-capabilities', component: CapabilitiesView },
  { path: '/projects/:id/connections', name: 'project-connections', component: ConnectionsView },
  { path: '/projects/:id/operations', name: 'project-operations', component: OperationsView },
  { path: '/projects/:id/action-calls', name: 'project-action-calls', component: ActionCallsView },
  { path: '/projects/:id/agent-requests', name: 'project-agent-requests', component: AgentRequestsView },
  { path: '/projects/:id/agent-presets', name: 'project-agent-presets', component: AgentPresetsView },
  { path: '/projects/:id/tasks', name: 'project-tasks', component: TaskTrackerView },
  {
    path: '/projects/:id/workflow-templates',
    name: 'project-workflow-templates',
    component: WorkflowTemplatesView,
  },
  { path: '/projects/:id/data', name: 'project-data', component: ProjectDataView },
  { path: '/projects/:id/resources', name: 'project-resources', component: ResourceExplorerView },
  { path: '/projects/:id/runs', name: 'project-runs', component: RunsView },
  { path: '/projects/:id/runs/:run_id', name: 'project-run-detail', component: RunsView },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
