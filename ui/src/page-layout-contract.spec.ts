import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = dirname(fileURLToPath(import.meta.url))

function source(relativePath: string): string {
  return readFileSync(join(ROOT, relativePath), 'utf8')
}

const OWN_SHELL = [
  'HomeView',
  'AuthErrorView',
  'HomeConsoleView',
  'InboxView',
  'ActivityView',
  'PluginsView',
  'CapabilitiesView',
  'ConnectionsView',
  'OperationsView',
  'ActionCallsView',
  'AgentRequestsView',
  'AgentPresetsView',
  'TaskTrackerView',
  'WorkflowTemplatesView',
  'ProjectDataView',
  'ResourceExplorerView',
  'RunsView',
] as const

describe('desktop page layout contract', () => {
  it('keeps the shared shell full width without a global content cap', () => {
    const shell = source('components/ui/UiPageShell.vue')

    expect(shell).toContain("'ui-page-shell w-full min-w-0'")
    expect(shell).not.toContain('max-w-content')
    expect(shell).not.toContain('mx-auto')
  })

  it('gives every independent routed view one page-shell owner', () => {
    const router = source('router.ts')

    for (const view of OWN_SHELL) {
      expect(router).toContain(`component: ${view}`)
      expect(source(`views/${view}.vue`)).toContain('<UiPageShell')
    }
  })

  it('delegates setup-family child routes to the project-detail shell', () => {
    const router = source('router.ts')
    const wrapper = source('views/ProjectDetailView.vue')

    for (const child of ['SetupStatusTab', 'SchedulesTab', 'CostBudgetTab']) {
      expect(router).toContain(`component: ${child}`)
      expect(source(`views/project-detail/${child}.vue`)).not.toContain('<UiPageShell')
    }
    expect(wrapper).toContain('<UiPageShell v-else>')
    expect(wrapper).toContain('<RouterView />')
  })

  it('keeps one shared Work command surface above all three modes', () => {
    const work = source('views/TaskTrackerView.vue')
    const stories = source('views/task-tracker/TrackerStoriesPanel.vue')

    expect(work).toContain("const viewMode = ref<ViewMode>('graph')")
    expect(work).toContain('<TaskTrackerCommandPanel')
    expect(work).not.toContain('v-if="viewMode !== \'stories\'"\n      :active-task-key')
    expect(stories).not.toContain('viewOptions')
    expect(stories).not.toContain('update:view-mode')
  })
})
