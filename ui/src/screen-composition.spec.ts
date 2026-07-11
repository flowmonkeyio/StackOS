import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = dirname(fileURLToPath(import.meta.url))

function source(relativePath: string): string {
  return readFileSync(join(ROOT, relativePath), 'utf8')
}

describe('desktop screen composition contract', () => {
  it('makes Home a prioritized control room instead of stacked inventory feeds', () => {
    const text = source('views/HomeConsoleView.vue')

    expect(text).toContain("eyebrow: 'Your next decision'")
    expect(text).toContain('Why this state')
    expect(text).toContain('Supervision')
    expect(text).toContain('Active work')
    expect(text).toContain('Recent outcomes')
    expect(text).not.toContain('AttentionItemRow')
  })

  it('makes Portfolio a cross-project operating overview before the directory', () => {
    const home = source('views/HomeView.vue')
    const overview = source('views/home/HomePortfolioOverview.vue')
    const directory = source('views/home/HomeProjectsSection.vue')

    expect(home).toContain('<HomePortfolioOverview')
    expect(overview).toContain('Portfolio operations')
    expect(overview).toContain('Work in progress')
    expect(overview).toContain('Workload by project')
    expect(overview).toContain('Portfolio completion')
    expect(directory).toContain("const filter = ref<'active' | 'archived' | 'all'>('active')")
    expect(directory).toContain('Current workspace')
    expect(directory).toContain('No active work')
  })

  it('opens Work on the dependency map while keeping one shared control surface', () => {
    const view = source('views/TaskTrackerView.vue')
    const stories = source('views/task-tracker/TrackerStoriesPanel.vue')

    expect(view).toContain("const viewMode = ref<ViewMode>('graph')")
    expect(view).toContain("{ key: 'graph', label: 'Dependency map'")
    expect(view).toContain('<TaskTrackerCommandPanel')
    expect(view).toContain('<TrackerStoriesPanel')
    expect(view).toContain("statuses: ['in-progress']")
    expect(view).toContain("snapshotScope.value = needsFullSnapshot ? 'full' : 'active'")
    expect(view).toContain('function setViewMode')
    expect(stories).toContain('What is blocking progress')
    expect(stories).toContain('Latest recorded outcome')
    expect(stories).toContain('Resolve in Attention')
    expect(stories).toContain('View outcome timeline')
    expect(stories).not.toContain("update:view-mode")
  })

  it('defaults Activity to grouped outcomes with raw audit on demand', () => {
    const text = source('views/ActivityView.vue')

    expect(text).toContain("const filter = ref<Category>(routeCategory())")
    expect(text).toContain(": 'outcomes'")
    expect(text).toContain('Related adjacent changes are grouped into one episode.')
    expect(text).toContain('Show raw audit')
    expect(text).toContain('Technical event details')
  })

  it('explains Setup without a synthetic readiness percentage', () => {
    const text = source('views/project-detail/SetupStatusTab.vue')

    expect(text).toContain('Setup path')
    expect(text).toContain('Required runtime state first')
    expect(text).toContain('Optional setup items')
    expect(text).toContain('Configure what this project actually needs')
    expect(text).not.toContain('UiScoreMeter')
  })

  it('opens Connections on services and groups messaging and diagnostics', () => {
    const text = source('views/ConnectionsView.vue')
    const addPanel = source('views/connections/AddConnectionPanel.vue')

    expect(text).toContain("const activeSection = ref<ConnectionSection>('services')")
    expect(text).toContain("label: 'Messaging setup'")
    expect(text).toContain("label: 'Advanced'")
    expect(text).toContain('webhook needs manual update')
    expect(addPanel).toContain('Credentials stay in the local daemon')
    expect(addPanel).toContain('Save and verify')
  })

  it('explains the request-to-work-to-outcome handoff', () => {
    const text = source('views/AgentRequestsView.vue')

    expect(text).toContain('From request to outcome')
    expect(text).toContain('Agent claims it')
    expect(text).toContain('Work becomes visible')
    expect(text).toContain('Outcome is recorded')
    expect(text).toContain('Where this request is now')
  })

  it('gives every Attention item impact, ownership, and an expected result', () => {
    const store = source('stores/attention.ts')
    const view = source('views/InboxView.vue')

    expect(store).toContain('impact: string')
    expect(store).toContain('ownership: string')
    expect(store).toContain('after: string')
    expect(view).toContain('Why it matters')
    expect(view).toContain('Who owns what')
    expect(view).toContain('After you act')
  })
})
