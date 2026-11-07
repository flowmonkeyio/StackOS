import { expect, test, type Locator } from '@playwright/test'
import { writeFileSync } from 'node:fs'
import type { SchemaTrackerAgentStatsOut } from '../../src/api'
import type { AgentStatsMetrics } from '../../src/lib/agentActivity'
import type { TrackerSnapshot } from '../../src/lib/task-tracker/types'
import { createProject, getBaseUrl, getDaemonToken, resetProjects, trackConsoleErrors } from '../helpers'
import { formatTokens, localActivityFilters } from '../../src/lib/agentActivity'

async function operation<T>(name: string, args: Record<string, unknown>): Promise<T> {
  const response = await fetch(`${getBaseUrl()}/api/v1/operations/${name}/call`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${getDaemonToken()}`, 'content-type': 'application/json' },
    body: JSON.stringify({ arguments: { response_mode: 'raw', ...args } }),
  })
  expect(response.ok, `${name}: ${response.status}`).toBe(true)
  return response.json()
}

// Synthetic recorded receipts only; no native agents, current defaults or model calls.
function receipt(role: string, outcome: string, total: number, model: string) {
  return {
    agent: 'same-name', selected_role: role, validated_outcome: outcome,
    launch: { agent: 'same-name', host_key: 'codex', model, started_at: new Date().toISOString() },
    usage: { unit: 'tokens', status: 'measured', measured: { input: total, output: 0, total, cache_read: Math.floor(total * 0.6), cache_write: 0, reasoning: 0 } },
  }
}

async function seed(projectId: number, automatic: object) {
  await operation('tracker.createTask', { project_id: projectId, key: 'synthetic-stats', title: 'Synthetic statistics proof' })
  await operation('tracker.createTicket', { project_id: projectId, task_key: 'synthetic-stats', key: 'synthetic-receipts', title: 'Synthetic recorded attempts' })
  await operation('tracker.updateTicket', {
    project_id: projectId, ticket_key: 'synthetic-receipts',
    patch_json: { completion_evidence_json: { automatic_delivery: automatic } },
  })
}

async function metrics(region: Locator, stats: SchemaTrackerAgentStatsOut) {
  for (const field of ['input', 'cache_read', 'output'] as const) {
    const value = region.locator(`.agent-usage-summary [data-usage-field="${field}"] dd[tabindex]`)
    await expect(value).toHaveText(formatTokens(stats.totals.reported_usage[field]))
    await expect(value).toHaveAttribute('aria-label', new RegExp(stats.totals.reported_usage[field] === null ? 'Not reported' : `${stats.totals.reported_usage[field]} tokens`))
  }
  await expect(region).toContainText(`${stats.totals.launch_attempts} agent`)
}

test('synthetic receipts reconcile through both actual Home views and existing project visibility', async ({ page }, testInfo) => {
  // Fail closed before any reset/fixture write. Global setup owns this daemon and scratch data/state.
  expect(new URL(getBaseUrl()).port).not.toBe('5180')
  expect(process.env.STACKOS_E2E_BASE_URL).toBe(getBaseUrl())
  await resetProjects()
  const errors = trackConsoleErrors(page)
  const project = await createProject({ name: 'Activity Proof', slug: 'activity-proof', domain: 'activity.test' })
  const archive = await createProject({ name: 'Archived Activity', slug: 'archived-activity', domain: 'archived.test' })
  const legacy = await createProject({ name: 'Legacy Activity', slug: 'legacy-activity', domain: 'legacy.test' })
  const zero = await createProject({ name: 'Measured Zero', slug: 'measured-zero', domain: 'zero.test' })
  const reset = await createProject({ name: 'Reset Only', slug: 'reset-only', domain: 'reset.test' })
  const filters = localActivityFilters()
  const first = { attempt: 1, delivery: receipt('delivery_agent', 'success', 10, 'recorded-sol'), review: receipt('review_agent', 'repair', 20, 'recorded-astra') }
  const second = { attempt: 2, delivery: receipt('delivery_agent', 'success', 30, 'recorded-sol'), review: receipt('review_agent', 'success', 40, 'recorded-astra') }
  await seed(project.id, { ...second, attempts: [first, second] })
  // A legacy missing measurement/classification creates truthful partial totals.
  await operation('tracker.createTicket', { project_id: project.id, task_key: 'synthetic-stats', key: 'legacy', title: 'Legacy attempt' })
  const unreported = { agent: 'legacy', launch: { host_key: 'codex', started_at: new Date().toISOString() } }
  await operation('tracker.updateTicket', { project_id: project.id, ticket_key: 'legacy', patch_json: { completion_evidence_json: { automatic_delivery: { attempt: 1, delivery: unreported } } } })
  await seed(archive.id, { attempt: 1, delivery: receipt('delivery_agent', 'success', 50, 'archived-model') })
  await seed(legacy.id, { attempt: 1, delivery: unreported })
  await seed(zero.id, { attempt: 1, delivery: receipt('delivery_agent', 'success', 0, 'zero-model') })
  await seed(reset.id, { attempt: 1, delivery: { ...receipt('delivery_agent', 'success', 999, 'reset-model'), usage: { status: 'unavailable', reason: 'operator_reset', measured: null } } })
  const archived = await fetch(`${getBaseUrl()}/api/v1/projects/${archive.id}`, {
    method: 'PATCH', headers: { Authorization: `Bearer ${getDaemonToken()}`, 'content-type': 'application/json' },
    body: JSON.stringify({ is_active: false }),
  })
  expect(archived.ok).toBe(true)
  const before = await operation('tracker.get', { project_id: project.id })
  const projectStats = await operation<SchemaTrackerAgentStatsOut>('tracker.agentStats', { project_id: project.id, ...filters })
  expect(projectStats.totals.launch_attempts).toBe(5)
  expect(projectStats.totals.reported_usage.total).toBe(100)
  expect(projectStats.totals.recorded_reviewer_rejects).toBe(1)
  expect(projectStats.totals.coverage.usage_status).toBe('partial')
  expect(projectStats.totals.coverage.classification_status).toBe('partial')

  await page.goto(`/projects/${project.id}`)
  const region = page.getByRole('region', { name: 'Agent activity', exact: true })
  await expect(page.getByRole('heading', { level: 1, name: project.name })).toBeVisible()
  await metrics(region, projectStats)
  await expect(region).toContainText('Usage missing for 1 attempt')
  await expect(region).toContainText('Cache missing for 1 attempt')
  await expect(region).not.toContainText('Partial')
  await expect(region.getByLabel('From', { exact: true })).toHaveValue(filters.start_date)
  await expect(region.getByLabel('Through', { exact: true })).toHaveValue(filters.end_date)
  await expect(region).toContainText(filters.timezone)
  const table = region.getByRole('table', { name: 'Agent activity breakdown' })
  await expect(table.locator('tbody tr')).toHaveCount(projectStats.rows.length)
  for (const row of projectStats.rows) {
    const rendered = table.locator('tbody tr').filter({ hasText: row.model ?? 'Model not recorded' })
    for (const field of ['input', 'cache_read', 'output'] as const)
      await expect(rendered.locator(`[aria-label^="${field === 'cache_read' ? 'Cache' : field === 'input' ? 'Input' : 'Output'}:"]`)).toHaveText(formatTokens(row.reported_usage[field]))
  }
  await region.getByRole('button', { name: 'See token details' }).click()
  await expect(region.getByLabel('Token details', { exact: true })).toContainText('Changes requested: 1')
  await expect(region.getByLabel('Token details', { exact: true })).toContainText('Not an approval rate')
  await region.getByRole('button', { name: 'Hide token details' }).click()
  await region.locator('.activity-chart canvas').focus()
  await region.locator('.activity-chart canvas').press('End')
  await expect(region.locator('[aria-live="polite"]')).toContainText('100 tokens')
  await region.getByLabel('Metric', { exact: true }).click()
  await region.getByRole('option', { name: 'Output tokens', exact: true }).click()
  await expect(region.getByLabel('Color by', { exact: true })).toContainText('Selected model')
  await region.getByLabel('Color by', { exact: true }).click()
  await expect(region.getByRole('option', { name: 'Input type', exact: true })).toHaveCount(0)
  await region.getByRole('option', { name: 'Agent', exact: true }).click()
  for (const metric of ['Cache tokens', 'Attempts', 'Input tokens']) {
    await region.getByLabel('Metric', { exact: true }).click()
    await region.getByRole('option', { name: metric, exact: true }).click()
    await expect(region.getByRole('heading', { name: `${metric} per day` })).toBeVisible()
  }
  await region.getByLabel('Color by', { exact: true }).click()
  await region.getByRole('option', { name: 'Input type', exact: true }).click()
  await region.getByText('Daily values', { exact: true }).click()
  const daily = region.getByRole('table', { name: 'Exact daily usage' })
  await expect(daily.locator('tbody tr').last()).toContainText('100')
  await region.getByText('Daily values', { exact: true }).click()
  await region.getByLabel('Metric', { exact: true }).click()
  await region.getByRole('option', { name: 'Output tokens', exact: true }).click()
  await region.getByLabel('Color by', { exact: true }).click()
  await region.getByRole('option', { name: 'Agent', exact: true }).click()
  for (const [label, group_by] of [['Agent', 'agent'], ['Agent + model', 'agent_model'], ['Model', 'model']] as const) {
    await region.getByRole('group', { name: 'Group agent usage' }).getByRole('button', { name: label, exact: true }).click()
    const grouped = await operation<SchemaTrackerAgentStatsOut>('tracker.agentStats', { project_id: project.id, ...filters, group_by })
    await expect(table.locator('tbody tr')).toHaveCount(grouped.rows.length)
    await metrics(region, grouped)
    await expect(region.getByLabel('Metric', { exact: true })).toContainText('Output tokens')
    await expect(region.getByLabel('Color by', { exact: true })).toContainText('Agent')
  }
  const tomorrow = new Date(); tomorrow.setDate(tomorrow.getDate() + 1)
  const future = localActivityFilters(tomorrow).end_date
  await region.getByLabel('Through', { exact: true }).fill(future)
  await region.getByLabel('Through', { exact: true }).press('Tab')
  await region.getByLabel('From', { exact: true }).fill(future)
  await region.getByLabel('From', { exact: true }).press('Tab')
  await expect(region).toContainText('No recorded usage in this date range')
  await region.getByLabel('From', { exact: true }).fill(filters.start_date)
  await region.getByLabel('From', { exact: true }).press('Tab')
  await region.getByLabel('Through', { exact: true }).fill(filters.end_date)
  await region.getByLabel('Through', { exact: true }).press('Tab')
  await metrics(region, projectStats)
  await expect(page.getByRole('heading', { name: 'Tickets by status', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Project connections', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Configured workflows', exact: true })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('project-agent-activity.png'), fullPage: true })

  await page.goto('/')
  const activeStats = await operation<SchemaTrackerAgentStatsOut>('tracker.agentStatsAll', { is_active: true, ...filters })
  await metrics(region, activeStats)
  await expect(page.getByRole('region', { name: 'System status', exact: true })).toBeVisible()
  await expect(page.getByRole('table', { name: 'Project portfolio' })).toBeVisible()
  await expect(region.getByRole('table').getByText('Archived Activity', { exact: true })).toHaveCount(0)
  await page.getByRole('tab', { name: 'All', exact: true }).click()
  const allStats = await operation<SchemaTrackerAgentStatsOut>('tracker.agentStatsAll', { is_active: null, ...filters })
  expect(allStats.totals.launch_attempts).toBe(8)
  expect(allStats.totals.reported_usage.total).toBe(150)
  await metrics(region, allStats)
  await expect(region.getByRole('table').locator('tbody tr')).toHaveCount(allStats.rows.length)
  await region.getByRole('group', { name: 'Group agent usage' }).getByRole('button', { name: 'Agent', exact: true }).click()
  await expect(region.getByRole('table', { name: 'Agent activity breakdown' }).getByText(/Archived Activity/).first()).toBeVisible()
  await expect(page.getByText('Checking connections…', { exact: true })).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('global-agent-activity.png'), fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.getByRole('tab', { name: 'Archived', exact: true }).click()
  const archivedStats = await operation<SchemaTrackerAgentStatsOut>('tracker.agentStatsAll', { is_active: false, ...filters })
  await metrics(region, archivedStats)
  expect(archivedStats.totals.reported_usage.total).toBe(50)
  await expect(region.getByRole('table').locator('tbody tr')).toHaveCount(1)
  await page.getByRole('tab', { name: 'Active', exact: true }).click()
  await metrics(region, activeStats)

  // Project-keyed route remounts clear previous values; measured zero differs from unavailable.
  await page.goto(`/projects/${legacy.id}`)
  await metrics(region, await operation<SchemaTrackerAgentStatsOut>('tracker.agentStats', { project_id: legacy.id, ...filters }))
  await expect(region.locator('[data-usage-field="input"] dd[tabindex]')).toHaveText('Not reported')
  await page.getByRole('button', { name: /Switch project/ }).click()
  await page.getByRole('option', { name: /Measured Zero/ }).click()
  await expect(page).toHaveURL(new RegExp(`/projects/${zero.id}$`))
  await metrics(region, await operation<SchemaTrackerAgentStatsOut>('tracker.agentStats', { project_id: zero.id, ...filters }))
  await expect(region.locator('[data-usage-field="input"] dd[tabindex]')).toHaveText('0')
  await page.goto(`/projects/${reset.id}`)
  await expect(region).toContainText('No recorded usage in this date range')
  await expect(region).not.toContainText('reset-model')
  const after = await operation('tracker.get', { project_id: project.id })
  expect(after).toEqual(before)
  errors.assertNone()
  const evidence = testInfo.outputPath('synthetic-api-ui-reconciliation.json')
  writeFileSync(evidence, JSON.stringify({ synthetic: true, base_url: getBaseUrl(), runtime: 'Playwright global-setup owned source daemon and temporary data/state', projectStats, activeStats, allStats, archivedStats, console_errors: errors.errors, read_only: true }, null, 2))
  await testInfo.attach('synthetic-api-ui-reconciliation', {
    path: evidence,
    contentType: 'application/json',
  })
})

async function workMetrics(region: Locator, totals: AgentStatsMetrics) {
  for (const field of ['input', 'cache_read', 'output'] as const) {
    const value = region.locator(`[data-usage-field="${field}"] dd[tabindex]`)
    await expect(value).toHaveText(formatTokens(totals.reported_usage[field]))
    await expect(value).toHaveAttribute('title', new RegExp(totals.reported_usage[field] == null ? 'Not reported' : `${totals.reported_usage[field]!.toLocaleString('en-US')} tokens`))
  }
  await expect(region).not.toContainText('Partial')
}

test('Work uses shared Input Cache Output through filtering, details and refresh', async ({ page }, testInfo) => {
  expect(new URL(getBaseUrl()).port).not.toBe('5180')
  expect(process.env.STACKOS_E2E_BASE_URL).toBe(getBaseUrl())
  await resetProjects()
  const errors = trackConsoleErrors(page)
  const project = await createProject({ name: 'Work Usage Proof', slug: 'work-usage-proof', domain: 'work.test' })
  await seed(project.id, { attempt: 1, delivery: receipt('delivery_agent', 'success', 100, 'recorded-sol') })
  await operation('tracker.createTicket', { project_id: project.id, task_key: 'synthetic-stats', key: 'legacy', title: 'Legacy usage' })
  const unavailable = { attempt: 1, delivery: { agent: 'legacy', launch: { host_key: 'codex' } } }
  await operation('tracker.updateTicket', { project_id: project.id, ticket_key: 'legacy', patch_json: { completion_evidence_json: { automatic_delivery: unavailable } } })
  for (const [key, automatic] of [
    ['zero', { attempt: 1, delivery: receipt('delivery_agent', 'success', 0, 'zero-model') }],
    ['unknown', unavailable],
    ['missing-cache', { attempt: 1, delivery: { ...receipt('delivery_agent', 'success', 50, 'older-model'), usage: { unit: 'tokens', status: 'measured', measured: { input: 50, output: 0, total: 50 } } } }],
    ['empty', null],
    ['reset', { attempt: 1, delivery: { ...receipt('delivery_agent', 'success', 999, 'reset-model'), usage: { status: 'unavailable', reason: 'operator_reset', measured: null } } }],
  ] as const) {
    await operation('tracker.createTask', { project_id: project.id, key, title: `Usage ${key}` })
    if (automatic) {
      await operation('tracker.createTicket', { project_id: project.id, task_key: key, key: `${key}-ticket`, title: `Usage ${key} ticket` })
      await operation('tracker.updateTicket', { project_id: project.id, ticket_key: `${key}-ticket`, patch_json: { completion_evidence_json: { automatic_delivery: automatic } } })
    }
  }
  // Persisted method identity only. No delivery jobs or native agents are started.
  const agent = { name: 'synthetic', instructions: 'Synthetic frozen identity fixture.' }
  for (const [task_key, status] of [['synthetic-stats', 'stopped'], ['zero', 'complete']]) {
    await operation('tracker.updateTask', { project_id: project.id, task_key, patch_json: { metadata_json: { delivery: { status, job_ref: 'synthetic:never-execute', delivery_agent: agent, review_agent: agent } } } })
  }
  const index = await operation<TrackerSnapshot>('tracker.get', { project_id: project.id, task_index_only: true, include_graph: false })
  const focused = await operation<TrackerSnapshot>('tracker.get', { project_id: project.id, task_key: 'synthetic-stats', include_graph: true })
  const taskTotal = focused.tasks[0]!.agent_stats!
  expect(taskTotal.reported_usage.total).toBe(100)
  expect(taskTotal.coverage.usage_status).toBe('partial')
  expect(index.tasks.find(task => task.key === 'synthetic-stats')!.agent_stats).toEqual(taskTotal)
  expect(focused.tickets.find(ticket => ticket.key === 'synthetic-receipts')!.agent_stats!.reported_usage.total).toBe(100)
  const before = await operation('tracker.get', { project_id: project.id })
  await page.goto(`/projects/${project.id}/tasks`)
  const board = page.getByRole('region', { name: 'Task board', exact: true })
  const card = board.getByRole('button', { name: /Synthetic statistics proof/ })
  await workMetrics(card, taskTotal)
  await expect(card.locator('.tracker-board__identity').first()).toContainText('Automated')
  await expect(board.getByRole('button', { name: /Usage zero$/ })).toContainText('Automated')
  await expect(board.getByRole('button', { name: /Usage unknown$/ })).not.toContainText('Automated')
  for (const key of ['zero', 'unknown', 'missing-cache', 'empty', 'reset']) {
    const task = index.tasks.find(task => task.key === key)!
    const row = board.getByRole('button', { name: new RegExp(`Usage ${key}$`) })
    await workMetrics(row, task.agent_stats!)
    if (['empty', 'reset'].includes(key)) await expect(row).toContainText('No recorded usage')
  }
  await page.screenshot({ path: testInfo.outputPath('work-usage-board.png'), fullPage: true })
  await card.click()
  const dialog = page.getByRole('dialog')
  await workMetrics(dialog.getByLabel('Lifetime task usage', { exact: true }), taskTotal)
  await dialog.getByRole('button', { name: /Synthetic recorded attempts/ }).click()
  const own = focused.tickets.find(ticket => ticket.key === 'synthetic-receipts')!.agent_stats!
  await workMetrics(dialog.getByLabel('Own ticket usage', { exact: true }), own)
  await page.screenshot({ path: testInfo.outputPath('work-ticket-popup.png'), fullPage: true })
  await dialog.getByRole('button', { name: 'Back to task', exact: true }).click()
  await workMetrics(dialog.getByLabel('Lifetime task usage', { exact: true }), taskTotal)
  await dialog.getByRole('button', { name: 'Close', exact: true }).first().click()

  await page.goto(`/projects/${project.id}`)
  const activity = page.getByRole('region', { name: 'Agent activity', exact: true })
  await activity.getByLabel('Through', { exact: true }).fill('2099-01-02')
  await activity.getByLabel('Through', { exact: true }).press('Tab')
  await activity.getByLabel('From', { exact: true }).fill('2099-01-02')
  await activity.getByLabel('From', { exact: true }).press('Tab')
  await expect(activity).toContainText('No recorded usage in this date range')

  await page.goto(`/projects/${project.id}/tasks?view=graph&task=synthetic-stats`)
  const header = page.locator('.tracker-command-panel__usage')
  await workMetrics(header, taskTotal)
  const node = page.locator('.ticket-graph-node').filter({ hasText: 'synthetic-receipts' })
  await workMetrics(node, own)
  await workMetrics(page.locator('.ticket-graph-node').filter({ hasText: 'Legacy usage' }), focused.tickets.find(ticket => ticket.key === 'legacy')!.agent_stats!)
  await node.click()
  await page.getByRole('button', { name: 'Details', exact: true }).click()
  await workMetrics(page.getByLabel('Own ticket usage', { exact: true }), own)
  await page.screenshot({ path: testInfo.outputPath('work-ticket-inspector.png'), fullPage: true })
  await page.getByRole('button', { name: 'Close', exact: true }).last().click()
  await page.getByRole('region', { name: 'Task dependency graph' }).getByRole('button', { name: /^Complete/ }).click()
  await expect(page.locator('.ticket-graph-node')).toHaveCount(0)
  await workMetrics(header, taskTotal)
  await page.getByRole('button', { name: 'Clear graph filters' }).click()
  await expect(page.locator('.ticket-graph-node')).toHaveCount(2)
  const viewport = page.locator('.vue-flow__transformationpane')
  const initialViewport = await viewport.getAttribute('style')
  await page.locator('.vue-flow__controls button').first().click()
  await expect(viewport).not.toHaveAttribute('style', initialViewport!)
  const zoomedViewport = await viewport.getAttribute('style')
  const pane = await page.locator('.vue-flow__pane').boundingBox()
  expect(pane).not.toBeNull()
  await page.mouse.move(pane!.x + 20, pane!.y + 40)
  await page.mouse.wheel(0, 80)
  await expect(viewport).not.toHaveAttribute('style', zoomedViewport!)
  await workMetrics(header, taskTotal)
  const pannedViewport = await viewport.getAttribute('style')
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(viewport).toHaveAttribute('style', pannedViewport!)
  await page.screenshot({ path: testInfo.outputPath('work-usage-graph.png'), fullPage: true })
  const toolbar = page.getByRole('toolbar', { name: 'Work view controls' })
  for (const width of [760, 900, 1024, 1280]) {
    await page.setViewportSize({ width, height: 800 })
    const controls = [
      toolbar.getByRole('tab', { name: 'Board', exact: true }),
      toolbar.getByRole('tab', { name: 'Graph', exact: true }),
      toolbar.getByLabel('Selected task', { exact: true }),
      toolbar.getByText('Automated', { exact: true }),
      toolbar.getByRole('button', { name: 'Task details', exact: true }),
      ...(['input', 'cache_read', 'output'] as const).flatMap(field => [
        header.locator(`[data-usage-field="${field}"] dt`),
        header.locator(`[data-usage-field="${field}"] dd[tabindex]`),
      ]),
    ]
    for (const control of controls) {
      await expect(control).toBeInViewport({ ratio: 1 })
      const frame = await toolbar.boundingBox()
      const box = await control.boundingBox()
      expect(frame).not.toBeNull()
      expect(box).not.toBeNull()
      expect(box!.x, `${width}px: ${await control.textContent()} left`).toBeGreaterThanOrEqual(frame!.x - 1)
      expect(box!.x + box!.width, `${width}px: ${await control.textContent()} right`).toBeLessThanOrEqual(frame!.x + frame!.width + 1)
    }
    await workMetrics(header, taskTotal)
    await toolbar.getByRole('button', { name: 'Task details', exact: true }).click()
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: 'Close', exact: true }).first().click()
  }
  await page.setViewportSize({ width: 760, height: 800 })
  await page.screenshot({ path: testInfo.outputPath('work-usage-narrow.png'), fullPage: true })

  for (const key of ['zero', 'unknown', 'missing-cache', 'empty', 'reset']) {
    await page.goto(`/projects/${project.id}/tasks?view=graph&task=${key}`)
    await workMetrics(header, index.tasks.find(task => task.key === key)!.agent_stats!)
  }
  expect(await operation('tracker.get', { project_id: project.id })).toEqual(before)
  await operation('tracker.updateTicket', { project_id: project.id, ticket_key: 'synthetic-receipts', patch_json: { completion_evidence_json: { automatic_delivery: { attempt: 1, delivery: receipt('delivery_agent', 'success', 125, 'recorded-sol') } } } })
  await page.goto(`/projects/${project.id}/tasks?view=graph&task=synthetic-stats`)
  const refreshed = await operation<TrackerSnapshot>('tracker.get', { project_id: project.id, task_key: 'synthetic-stats', include_graph: true })
  await workMetrics(header, refreshed.tasks[0]!.agent_stats!)
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await workMetrics(header, refreshed.tasks[0]!.agent_stats!)
  errors.assertNone()
  writeFileSync(testInfo.outputPath('work-api-ui-reconciliation.json'), JSON.stringify({ synthetic: true, base_url: getBaseUrl(), index, focused, taskTotal, console_errors: errors.errors }, null, 2))
})
