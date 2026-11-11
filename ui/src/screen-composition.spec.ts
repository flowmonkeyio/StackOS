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
  it('composes project Home from active work, retained Usage and connected tools', () => {
    const text = source('views/HomeConsoleView.vue')

    expect(text).toContain('<ActiveTaskList')
    expect(text).not.toContain('Latest action calls')
    expect(text).toContain('<HomeConnectionsSection')
    expect(text).toContain('<AgentActivitySection')
    expect(text).not.toContain('Configured workflows')
    expect(text).not.toContain('Agents are working')
    expect(text).not.toContain('status=running')
    expect(text).not.toContain('AttentionItemRow')
  })

  it('preserves Local service before the shared active list and Usage', () => {
    const home = source('views/HomeView.vue')
    expect(home.indexOf('<HomeSystemStatusCard')).toBeLessThan(home.indexOf('<ActiveTaskList'))
    expect(home.indexOf('<ActiveTaskList')).toBeLessThan(home.indexOf('<AgentActivitySection'))
    expect(home.indexOf('<AgentActivitySection')).toBeLessThan(home.indexOf('<HomeConnectionsSection'))
    expect(home).toContain('Project visibility')
    expect(home).not.toContain('<HomePortfolioOverview')
    expect(home).not.toContain('<HomeProjectsSection')
  })

  it('opens Work on a task Board with bounded card edits, focused map and centered detail inspection', () => {
    const view = source('views/TaskTrackerView.vue')
    const board = source('views/task-tracker/TrackerBoardPanel.vue')
    const reorder = source('components/ui/UiReorderList.vue')
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
    expect(board).toContain('<UiReorderList')
    expect(board).toContain('<UiReorderHandle')
    expect(reorder).toContain('Move up')
    expect(reorder).toContain('Move down')
    expect(board).toContain('<TaskPriorityBadge')
    expect(view).toContain('@move="boardEdits.move"')
    expect(commands).toContain('<TrackerBoardFilterBar')
    expect(commands).not.toContain('aria-label="Search tasks"')
    expect(commands).toMatch(/<div\s+v-else\s+class="tracker-command-panel__context"\s*>[\s\S]*aria-label="Selected task"/)
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

})
