// @vitest-environment node

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

  it('delegates configuration child routes to the project-detail shell', () => {
    const router = source('router.ts')
    const wrapper = source('views/ProjectDetailView.vue')

    for (const child of ['SchedulesTab', 'CostBudgetTab']) {
      expect(router).toContain(`component: ${child}`)
      expect(source(`views/project-detail/${child}.vue`)).not.toContain('<UiPageShell')
    }
    expect(wrapper).toContain('<UiPageShell v-else>')
    expect(wrapper).toContain('<RouterView />')
  })

  it('keeps Board and Graph controls inline in one shared Work toolbar', () => {
    const work = source('views/TaskTrackerView.vue')
    const commands = source('views/task-tracker/TaskTrackerCommandPanel.vue')
    const board = source('views/task-tracker/TrackerBoardPanel.vue')
    const graph = source('views/task-tracker/TrackerGraphPanel.vue')

    expect(work.match(/<TaskTrackerCommandPanel\b/g)).toHaveLength(1)
    expect(work.indexOf('<TaskTrackerCommandPanel')).toBeLessThan(work.indexOf('<TrackerBoardPanel'))
    expect(work.indexOf('<TaskTrackerCommandPanel')).toBeLessThan(work.indexOf('<TrackerGraphPanel'))
    expect(commands.match(/<UiToolbar\b/g)).toHaveLength(1)
    expect(commands).toMatch(/<UiToolbar[\s\S]*<UiSegmentedControl[\s\S]*<template v-if="viewMode === 'board'">[\s\S]*<template v-else>[\s\S]*<\/UiToolbar>/)
    expect(commands).toMatch(/\.tracker-command-panel\s*\{[^}]*flex-wrap:\s*nowrap/)
    expect(commands).toContain("{ key: 'board', label: 'Board' }")
    expect(commands).toContain("{ key: 'graph', label: 'Graph' }")
    expect(board).not.toContain('UiSegmentedControl')
    expect(graph).not.toContain('UiSegmentedControl')
    expect(board).not.toContain('update:view-mode')
    expect(graph).not.toContain('update:view-mode')
  })
})
