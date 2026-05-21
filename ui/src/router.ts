import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import HomeView from './views/HomeView.vue'
import AuthErrorView from './views/AuthErrorView.vue'
import ProjectsView from './views/ProjectsView.vue'
import ProjectDetailView from './views/ProjectDetailView.vue'
import OverviewTab from './views/project-detail/OverviewTab.vue'
import SchedulesTab from './views/project-detail/SchedulesTab.vue'
import CostBudgetTab from './views/project-detail/CostBudgetTab.vue'
import RunsView from './views/RunsView.vue'
import PluginsView from './views/PluginsView.vue'
import CapabilitiesView from './views/CapabilitiesView.vue'
import ConnectionsView from './views/ConnectionsView.vue'
import WorkflowTemplatesView from './views/WorkflowTemplatesView.vue'
import ProjectDataView from './views/ProjectDataView.vue'
import ResourceExplorerView from './views/ResourceExplorerView.vue'

const routes: RouteRecordRaw[] = [
  { path: '/', name: 'home', component: HomeView },
  { path: '/auth-error', name: 'auth-error', component: AuthErrorView },
  { path: '/projects', name: 'projects', component: ProjectsView },
  {
    path: '/projects/:id',
    component: ProjectDetailView,
    redirect: (to) => ({ path: `/projects/${to.params.id}/overview` }),
    children: [
      { path: 'overview', name: 'project-detail-overview', component: OverviewTab },
      { path: 'schedules', name: 'project-detail-schedules', component: SchedulesTab },
      { path: 'cost-budget', name: 'project-detail-cost-budget', component: CostBudgetTab },
    ],
  },
  { path: '/projects/:id/plugins', name: 'project-plugins', component: PluginsView },
  { path: '/projects/:id/capabilities', name: 'project-capabilities', component: CapabilitiesView },
  { path: '/projects/:id/connections', name: 'project-connections', component: ConnectionsView },
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
