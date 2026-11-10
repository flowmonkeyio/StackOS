import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import HomeView from './views/HomeView.vue'
import LocalSetupView from './views/LocalSetupView.vue'
import AuthErrorView from './views/AuthErrorView.vue'
import ProjectDetailView from './views/ProjectDetailView.vue'
import HomeConsoleView from './views/HomeConsoleView.vue'
import SchedulesTab from './views/project-detail/SchedulesTab.vue'
import CostBudgetTab from './views/project-detail/CostBudgetTab.vue'
import PluginsView from './views/PluginsView.vue'
import ConnectionsView from './views/ConnectionsView.vue'
import AccountsView from './views/AccountsView.vue'
import LibraryView from './views/LibraryView.vue'
import AgentsView from './views/AgentsView.vue'
import PluginDomainView from './views/PluginDomainView.vue'
import WorkflowDetailView from './views/WorkflowDetailView.vue'
import TaskTrackerView from './views/TaskTrackerView.vue'
import FinanceView from './views/FinanceView.vue'

const routes: RouteRecordRaw[] = [
  { path: '/', name: 'home', component: HomeView },
  { path: '/setup', name: 'local-setup', component: LocalSetupView },
  { path: '/auth-error', name: 'auth-error', component: AuthErrorView },
  { path: '/accounts', name: 'accounts', component: AccountsView },
  { path: '/projects', redirect: '/' },
  {
    path: '/projects/:id',
    component: ProjectDetailView,
    children: [
      // Home is the operations console; it owns its own page chrome, so
      // ProjectDetailView renders it without the configuration header wrapper.
      { path: '', name: 'project-home', component: HomeConsoleView },
      { path: 'schedules', name: 'project-detail-schedules', component: SchedulesTab },
      { path: 'cost-budget', name: 'project-detail-cost-budget', component: CostBudgetTab },
    ],
  },
  { path: '/projects/:id/plugins', name: 'project-plugins', component: PluginsView },
  { path: '/projects/:id/plugins/:pluginSlug', name: 'project-plugin', component: PluginDomainView },
  { path: '/projects/:id/plugins/:pluginSlug/workflows/:templateKey', name: 'project-workflow', component: WorkflowDetailView },
  { path: '/projects/:id/workflows/:templateKey', name: 'project-configured-workflow', component: WorkflowDetailView },
  { path: '/projects/:id/library', name: 'project-library', component: LibraryView },
  { path: '/projects/:id/agents', name: 'project-agents', component: AgentsView },
  { path: '/projects/:id/connections', name: 'project-connections', component: ConnectionsView },
  { path: '/projects/:id/tasks', name: 'project-tasks', component: TaskTrackerView },
  { path: '/projects/:id/finance', name: 'project-finance', component: FinanceView },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
