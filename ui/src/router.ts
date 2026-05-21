import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import HomeView from './views/HomeView.vue'
import AuthErrorView from './views/AuthErrorView.vue'
import ProjectsView from './views/ProjectsView.vue'
import ProjectDetailView from './views/ProjectDetailView.vue'
import OverviewTab from './views/project-detail/OverviewTab.vue'
import VoiceTab from './views/project-detail/VoiceTab.vue'
import ComplianceTab from './views/project-detail/ComplianceTab.vue'
import EeatTab from './views/project-detail/EeatTab.vue'
import TargetsTab from './views/project-detail/TargetsTab.vue'
import IntegrationsTab from './views/project-detail/IntegrationsTab.vue'
import SchedulesTab from './views/project-detail/SchedulesTab.vue'
import CostBudgetTab from './views/project-detail/CostBudgetTab.vue'
import ClustersView from './views/ClustersView.vue'
import TopicsView from './views/TopicsView.vue'
import ArticlesView from './views/ArticlesView.vue'
import ArticleDetailView from './views/ArticleDetailView.vue'
import BriefTab from './views/article-detail/BriefTab.vue'
import OutlineTab from './views/article-detail/OutlineTab.vue'
import DraftTab from './views/article-detail/DraftTab.vue'
import EditedTab from './views/article-detail/EditedTab.vue'
import AssetsTab from './views/article-detail/AssetsTab.vue'
import SourcesTab from './views/article-detail/SourcesTab.vue'
import SchemaTab from './views/article-detail/SchemaTab.vue'
import PublishesTab from './views/article-detail/PublishesTab.vue'
import ArticleEeatTab from './views/article-detail/EeatTab.vue'
import VersionsTab from './views/article-detail/VersionsTab.vue'
import ArticleInterlinksTab from './views/article-detail/InterlinksTab.vue'
import DriftTab from './views/article-detail/DriftTab.vue'
import InterlinksView from './views/InterlinksView.vue'
import GscView from './views/GscView.vue'
import DriftView from './views/DriftView.vue'
import RunsView from './views/RunsView.vue'
import ProceduresView from './views/ProceduresView.vue'
import PluginsView from './views/PluginsView.vue'
import CapabilitiesView from './views/CapabilitiesView.vue'
import ConnectionsView from './views/ConnectionsView.vue'
import WorkflowTemplatesView from './views/WorkflowTemplatesView.vue'
import ProjectDataView from './views/ProjectDataView.vue'
import ResourceExplorerView from './views/ResourceExplorerView.vue'

// Routes per PLAN.md L185-L194 + the M5.B article-detail tabs.
// Each article-detail tab is its own child route so the URL preserves the
// active tab on refresh + back/forward (audit-trail-friendly URLs).
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
      { path: 'voice', name: 'project-detail-voice', component: VoiceTab },
      { path: 'compliance', name: 'project-detail-compliance', component: ComplianceTab },
      { path: 'eeat', name: 'project-detail-eeat', component: EeatTab },
      { path: 'targets', name: 'project-detail-targets', component: TargetsTab },
      { path: 'integrations', name: 'project-detail-integrations', component: IntegrationsTab },
      { path: 'schedules', name: 'project-detail-schedules', component: SchedulesTab },
      { path: 'cost-budget', name: 'project-detail-cost-budget', component: CostBudgetTab },
    ],
  },
  { path: '/projects/:id/clusters', name: 'project-clusters', component: ClustersView },
  { path: '/projects/:id/topics', name: 'project-topics', component: TopicsView },
  { path: '/projects/:id/articles', name: 'project-articles', component: ArticlesView },
  {
    path: '/projects/:id/articles/:aid',
    component: ArticleDetailView,
    props: true,
    children: [
      // The empty path matches the bare `/projects/:id/articles/:aid`. The
      // outer view's onMounted redirects this to `/brief` so the URL is
      // bookmark-friendly; the named route still resolves so vue-router
      // doesn't 404 the bare URL.
      { path: '', name: 'project-article-detail', component: BriefTab, props: true },
      {
        path: 'brief',
        name: 'project-article-detail-brief',
        component: BriefTab,
        props: true,
      },
      {
        path: 'outline',
        name: 'project-article-detail-outline',
        component: OutlineTab,
        props: true,
      },
      {
        path: 'draft',
        name: 'project-article-detail-draft',
        component: DraftTab,
        props: true,
      },
      {
        path: 'edited',
        name: 'project-article-detail-edited',
        component: EditedTab,
        props: true,
      },
      {
        path: 'assets',
        name: 'project-article-detail-assets',
        component: AssetsTab,
        props: true,
      },
      {
        path: 'sources',
        name: 'project-article-detail-sources',
        component: SourcesTab,
        props: true,
      },
      {
        path: 'schema',
        name: 'project-article-detail-schema',
        component: SchemaTab,
        props: true,
      },
      {
        path: 'publishes',
        name: 'project-article-detail-publishes',
        component: PublishesTab,
        props: true,
      },
      {
        path: 'eeat',
        name: 'project-article-detail-eeat',
        component: ArticleEeatTab,
        props: true,
      },
      {
        path: 'versions',
        name: 'project-article-detail-versions',
        component: VersionsTab,
        props: true,
      },
      {
        path: 'interlinks',
        name: 'project-article-detail-interlinks',
        component: ArticleInterlinksTab,
        props: true,
      },
      {
        path: 'drift',
        name: 'project-article-detail-drift',
        component: DriftTab,
        props: true,
      },
    ],
  },
  { path: '/projects/:id/interlinks', name: 'project-interlinks', component: InterlinksView },
  { path: '/projects/:id/gsc', name: 'project-gsc', component: GscView },
  { path: '/projects/:id/drift', name: 'project-drift', component: DriftView },
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
  { path: '/projects/:id/procedures', name: 'project-procedures', component: ProceduresView },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
