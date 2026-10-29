// @vitest-environment node

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = dirname(fileURLToPath(import.meta.url))

function source(relativePath: string): string {
  return readFileSync(join(ROOT, relativePath), 'utf8')
}

describe('desktop screen composition contract', () => {
  it('makes project Home a current ticket overview rather than live-agent claims', () => {
    const text = source('views/HomeConsoleView.vue')

    expect(text).toContain('Tickets by status')
    expect(text).not.toContain('Latest action calls')
    expect(text).toContain('Project connections')
    expect(text).toContain('Configured workflows')
    expect(text).toContain('Tracked work')
    expect(text).not.toContain('Agents are working')
    expect(text).not.toContain('status=running')
    expect(text).not.toContain('AttentionItemRow')
  })

  it('makes Portfolio a cross-project operating overview before the directory', () => {
    const home = source('views/HomeView.vue')
    const overview = source('views/home/HomePortfolioOverview.vue')
    const directory = source('views/home/HomeProjectsSection.vue')

    expect(home).toContain('<HomePortfolioOverview')
    expect(overview).toContain('Tickets by status')
    expect(overview).not.toContain('summary-aside')
    expect(overview).toContain('Current snapshot')
    expect(overview).not.toContain('Portfolio completion')
    expect(overview).toContain('Project visibility')
    expect(directory).toContain('Latest tracked work')
    expect(directory).toContain('Ticket status')
    expect(directory).toContain('Load more projects')
  })

  it('opens Work on a complete read-only task Board with focused map and centered detail inspection', () => {
    const view = source('views/TaskTrackerView.vue')
    const board = source('views/task-tracker/TrackerBoardPanel.vue')
    const commands = source('views/task-tracker/TaskTrackerCommandPanel.vue')
    const taskDialog = source('views/task-tracker/TrackerTaskDetailDialog.vue')
    const ticketDialog = source('views/task-tracker/TrackerTicketDetailPanel.vue')
    const ticketContent = source('views/task-tracker/TrackerTicketDetailContent.vue')

    expect(view).toContain("route.query.view === 'graph' ? 'graph' : 'board'")
    expect(view).toContain('<TaskTrackerCommandPanel')
    expect(view).toContain('<TrackerBoardPanel')
    expect(view).toContain('<TrackerGraphPanel')
    expect(view).not.toContain('TrackerStoriesPanel')
    expect(view).not.toContain('TrackerTicketTable')
    expect(view).toContain('task_index_only: true')
    expect(view).not.toContain("statuses: ['in-progress']")
    expect(view).toContain('const taskKey = mapTaskKey.value')
    expect(view).toContain('graph.loadFocusedGraph(taskKey,')
    expect(board).toContain('Object.keys(trackerStatus)')
    expect(board).toMatch(/grid-template-columns:\s*repeat\(7,/)
    expect(board).toContain('row.task.status === status')
    expect(board).toContain("@click=\"$emit('openTask', row.key)\"")
    expect(board).not.toMatch(/draggable|@drop|@dragstart/)
    const boardControls = commands.split('<template v-if="viewMode === \'board\'">')[1]!.split('<template v-else>')[0]!
    expect(boardControls).toContain('aria-label="Search tasks"')
    expect(boardControls).not.toContain('aria-label="Selected task"')
    expect(commands).toMatch(/<template v-else>[\s\S]*aria-label="Selected task"/)
    expect(taskDialog).toContain('<UiDialog')
    expect(taskDialog.match(/<UiDialog\b/g)).toHaveLength(1)
    expect(taskDialog).toContain('layout="detail"')
    expect(taskDialog).toContain('<TrackerTicketDetailContent')
    expect(ticketDialog).toContain('<InspectableDetailDrawer')
    expect(ticketDialog).toContain('<TrackerTicketDetailContent')
    expect(ticketDialog).not.toContain('<UiDialog')
    expect(ticketContent.match(/aria-label="Ticket core metadata"/g)).toHaveLength(1)
    expect(taskDialog).toContain('View graph')
    expect(taskDialog).toContain('Back to task')
  })

  it('defaults Activity to grouped outcomes with raw audit on demand', () => {
    const text = source('views/ActivityView.vue')

    expect(text).toContain('const filter = ref<Category>(routeCategory())')
    expect(text).toContain(": 'outcomes'")
    expect(text).toContain('Related adjacent changes are grouped into one episode.')
    expect(text).toContain('Show raw audit')
    expect(text).toContain('Technical event details')
  })

  it('opens Connections on services and groups messaging and diagnostics', () => {
    const text = source('views/ConnectionsView.vue')
    const connectivity = source('views/connections/ConnectivityPanel.vue')
    const addPanel = source('views/accounts/AddAccountPanel.vue')
    const accounts = source('views/AccountsView.vue')

    expect(text).toContain("const activeSection = ref<ConnectionSection>('services')")
    expect(text).toContain("label: 'Messaging setup'")
    expect(text).toContain("label: 'Advanced'")
    expect(text).not.toContain('webhook needs manual update')
    expect(connectivity).toContain('I’ve updated Slack')
    expect(addPanel).toContain('Credentials stay in the local daemon')
    expect(addPanel).toContain('Save and verify')
    expect(accounts).toContain('Set up provider Accounts once')
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
