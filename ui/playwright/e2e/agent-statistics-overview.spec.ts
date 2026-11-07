import { expect, test, type Locator } from '@playwright/test'
import { writeFileSync } from 'node:fs'
import type { SchemaTrackerAgentStatsOut } from '../../src/api'
import type { TrackerSnapshot } from '../../src/lib/task-tracker/types'
import { createProject, getBaseUrl, getDaemonToken, resetProjects, trackConsoleErrors } from '../helpers'

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
    launch: { agent: 'same-name', host_key: 'codex', model },
    usage: { unit: 'tokens', status: 'measured', measured: { input: total, output: 0, total } },
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
  const cards = region.locator('.ui-metric-card')
  await expect(cards).toHaveCount(3)
  await expect(cards.nth(0).locator('span.tabular-nums')).toHaveText(String(stats.totals.launch_attempts))
  await expect(cards.nth(1).locator('span.tabular-nums')).toHaveText(stats.totals.reported_usage.total === null ? 'Unavailable' : String(stats.totals.reported_usage.total))
  await expect(cards.nth(2).locator('span.tabular-nums')).toHaveText(stats.totals.coverage.classification_status === 'unavailable' ? 'Unavailable' : String(stats.totals.recorded_reviewer_rejects))
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
  const first = { attempt: 1, delivery: receipt('delivery_agent', 'success', 10, 'recorded-sol'), review: receipt('review_agent', 'repair', 20, 'recorded-astra') }
  const second = { attempt: 2, delivery: receipt('delivery_agent', 'success', 30, 'recorded-sol'), review: receipt('review_agent', 'success', 40, 'recorded-astra') }
  await seed(project.id, { ...second, attempts: [first, second] })
  // A legacy missing measurement/classification creates truthful partial totals.
  await operation('tracker.createTicket', { project_id: project.id, task_key: 'synthetic-stats', key: 'legacy', title: 'Legacy attempt' })
  await operation('tracker.updateTicket', { project_id: project.id, ticket_key: 'legacy', patch_json: { completion_evidence_json: { automatic_delivery: { attempt: 1, delivery: { agent: 'legacy', launch: { host_key: 'codex' } } } } } })
  await seed(archive.id, { attempt: 1, delivery: receipt('delivery_agent', 'success', 50, 'archived-model') })
  await seed(legacy.id, { attempt: 1, delivery: { agent: 'legacy', launch: { host_key: 'codex' } } })
  await seed(zero.id, { attempt: 1, delivery: receipt('delivery_agent', 'success', 0, 'zero-model') })
  const archived = await fetch(`${getBaseUrl()}/api/v1/projects/${archive.id}`, {
    method: 'PATCH', headers: { Authorization: `Bearer ${getDaemonToken()}`, 'content-type': 'application/json' },
    body: JSON.stringify({ is_active: false }),
  })
  expect(archived.ok).toBe(true)
  const before = await operation('tracker.get', { project_id: project.id })
  const projectStats = await operation<SchemaTrackerAgentStatsOut>('tracker.agentStats', { project_id: project.id })
  expect(projectStats.totals.launch_attempts).toBe(5)
  expect(projectStats.totals.reported_usage.total).toBe(100)
  expect(projectStats.totals.recorded_reviewer_rejects).toBe(1)
  expect(projectStats.totals.coverage.usage_status).toBe('partial')
  expect(projectStats.totals.coverage.classification_status).toBe('partial')

  await page.goto(`/projects/${project.id}`)
  const region = page.getByRole('region', { name: 'Agent activity', exact: true })
  await expect(page.getByRole('heading', { level: 1, name: project.name })).toBeVisible()
  await metrics(region, projectStats)
  await expect(region).toContainText('Partial · 4 of 5 attempts measured')
  await expect(region).toContainText('Partial · 4 of 5 attempts classified')
  const chartGroups = region.getByRole('figure', { name: 'Launch attempts by recorded group' }).getByRole('listitem')
  await expect(chartGroups).toHaveCount(projectStats.rows.length)
  for (let index = 0; index < projectStats.rows.length; index++) {
    const row = projectStats.rows[index]!
    await expect(chartGroups.nth(index)).toContainText(row.agent_name ?? 'Unknown agent')
    await expect(chartGroups.nth(index)).toContainText(row.model ?? 'Unknown model')
    await expect(chartGroups.nth(index)).toContainText(`${row.launch_attempts} launch attempts`)
  }
  const table = region.getByRole('table', { name: 'Agent activity breakdown' })
  await expect(table.locator('tbody tr')).toHaveCount(projectStats.rows.length)
  for (const row of projectStats.rows) {
    const rendered = table.locator('tbody tr').filter({ hasText: row.model ?? 'Unknown model' })
    await expect(rendered).toContainText(row.agent_name ?? 'Unknown agent')
    await expect(rendered).toContainText(row.reported_usage.total === null ? 'Unavailable' : String(row.reported_usage.total))
  }
  await expect(page.getByRole('heading', { name: 'Tickets by status', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Project connections', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Configured workflows', exact: true })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('project-agent-activity.png'), fullPage: true })

  await page.goto('/')
  const activeStats = await operation<SchemaTrackerAgentStatsOut>('tracker.agentStatsAll', { is_active: true })
  await metrics(region, activeStats)
  await expect(page.getByRole('region', { name: 'System status', exact: true })).toBeVisible()
  await expect(page.getByRole('table', { name: 'Project portfolio' })).toBeVisible()
  await expect(region.getByRole('table').getByText('Archived Activity', { exact: true })).toHaveCount(0)
  await page.getByRole('tab', { name: 'All', exact: true }).click()
  const allStats = await operation<SchemaTrackerAgentStatsOut>('tracker.agentStatsAll', { is_active: null })
  expect(allStats.totals.launch_attempts).toBe(8)
  expect(allStats.totals.reported_usage.total).toBe(150)
  await metrics(region, allStats)
  await expect(region.getByRole('table').locator('tbody tr')).toHaveCount(allStats.rows.length)
  await expect(region.getByRole('table').getByText('Activity Proof', { exact: true })).toHaveCount(projectStats.rows.length)
  await expect(region.getByRole('table').getByText('Archived Activity', { exact: true })).toBeVisible()
  await expect(page.getByText('Checking connections…', { exact: true })).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('global-agent-activity.png'), fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.getByRole('tab', { name: 'Archived', exact: true }).click()
  const archivedStats = await operation<SchemaTrackerAgentStatsOut>('tracker.agentStatsAll', { is_active: false })
  await metrics(region, archivedStats)
  expect(archivedStats.totals.reported_usage.total).toBe(50)
  await expect(region.getByRole('table').locator('tbody tr')).toHaveCount(1)
  await page.getByRole('tab', { name: 'Active', exact: true }).click()
  await metrics(region, activeStats)

  // Project-keyed route remounts clear previous values; measured zero differs from unavailable.
  await page.goto(`/projects/${legacy.id}`)
  await metrics(region, await operation<SchemaTrackerAgentStatsOut>('tracker.agentStats', { project_id: legacy.id }))
  await expect(region.locator('.ui-metric-card').nth(1)).toContainText('Unavailable')
  await page.getByRole('button', { name: /Switch project/ }).click()
  await page.getByRole('option', { name: /Measured Zero/ }).click()
  await expect(page).toHaveURL(new RegExp(`/projects/${zero.id}$`))
  await metrics(region, await operation<SchemaTrackerAgentStatsOut>('tracker.agentStats', { project_id: zero.id }))
  await expect(region.locator('.ui-metric-card').nth(1)).toContainText('Complete · 1 of 1 attempts measured')
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

test('Work uses projected task and ticket tokens through filtering, scope changes and refresh', async ({ page }, testInfo) => {
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
    ['empty', null],
  ] as const) {
    await operation('tracker.createTask', { project_id: project.id, key, title: `Usage ${key}` })
    if (automatic) {
      await operation('tracker.createTicket', { project_id: project.id, task_key: key, key: `${key}-ticket`, title: `Usage ${key} ticket` })
      await operation('tracker.updateTicket', { project_id: project.id, ticket_key: `${key}-ticket`, patch_json: { completion_evidence_json: { automatic_delivery: automatic } } })
    }
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
  await expect(board.getByRole('button', { name: /Synthetic statistics proof/ })).toContainText('100 tokens · Partial')
  await expect(board.getByRole('button', { name: /Usage zero$/ })).toContainText('0 tokens')
  await expect(board.getByRole('button', { name: /Usage unknown$/ })).toContainText('Tokens unavailable')
  await expect(board.getByRole('button', { name: /Usage empty$/ })).toContainText('No recorded attempts')
  await page.screenshot({ path: testInfo.outputPath('work-usage-board.png'), fullPage: true })

  await page.goto(`/projects/${project.id}/tasks?view=graph&task=synthetic-stats`)
  const header = page.locator('.tracker-command-panel__usage')
  await expect(header).toHaveText('Task total: 100 tokens · Partial')
  await expect(page.locator('.ticket-graph-node').filter({ hasText: 'synthetic-receipts' }).locator('.ticket-graph-node__usage')).toHaveText('100 tokens')
  await expect(page.locator('.ticket-graph-node').filter({ hasText: 'Legacy usage' }).locator('.ticket-graph-node__usage')).toHaveText('Tokens unavailable')
  await page.getByRole('region', { name: 'Task dependency graph' }).getByRole('button', { name: /^Complete/ }).click()
  await expect(page.locator('.ticket-graph-node')).toHaveCount(0)
  await expect(header).toHaveText('Task total: 100 tokens · Partial')
  await page.getByRole('button', { name: 'Clear graph filters' }).click()
  await expect(page.locator('.ticket-graph-node')).toHaveCount(2)
  await page.screenshot({ path: testInfo.outputPath('work-usage-graph.png'), fullPage: true })
  await page.setViewportSize({ width: 760, height: 800 })
  await expect(header).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('work-usage-narrow.png'), fullPage: true })

  for (const [key, label] of [['zero', '0 tokens'], ['unknown', 'Tokens unavailable'], ['empty', 'No recorded attempts']]) {
    await page.goto(`/projects/${project.id}/tasks?view=graph&task=${key}`)
    await expect(header).toHaveText(`Task total: ${label}`)
  }
  expect(await operation('tracker.get', { project_id: project.id })).toEqual(before)
  await operation('tracker.updateTicket', { project_id: project.id, ticket_key: 'synthetic-receipts', patch_json: { completion_evidence_json: { automatic_delivery: { attempt: 1, delivery: receipt('delivery_agent', 'success', 125, 'recorded-sol') } } } })
  await page.goto(`/projects/${project.id}/tasks?view=graph&task=synthetic-stats`)
  await expect(header).toHaveText('Task total: 125 tokens · Partial')
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(header).toHaveText('Task total: 125 tokens · Partial')
  errors.assertNone()
  writeFileSync(testInfo.outputPath('work-api-ui-reconciliation.json'), JSON.stringify({ synthetic: true, base_url: getBaseUrl(), index, focused, taskTotal, console_errors: errors.errors }, null, 2))
})
