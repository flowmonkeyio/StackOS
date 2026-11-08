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
    const source = field === 'input' ? 'remaining_input' : field
    const value = region.locator(`.agent-usage-summary [data-usage-field="${field}"] dd[tabindex]`)
    if (stats.totals.reported_usage[source] == null || !stats.totals.launch_attempts || !stats.totals.coverage.fields?.[field === 'input' ? 'cache_read' : field]?.measured_attempts) { await expect(value).toHaveText('N/A'); continue }
    await expect(value).toHaveText(formatTokens(stats.totals.reported_usage[source]))
    await expect(value).toHaveAttribute('aria-label', new RegExp(`${stats.totals.reported_usage[source]!.toLocaleString('en-US')} tokens`))
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
  await expect(region).not.toContainText('Cache missing for 1 attempt')
  await expect(region).not.toContainText('Partial')
  await expect(region.getByLabel('From', { exact: true })).toHaveValue(filters.start_date)
  await expect(region.getByLabel('Through', { exact: true })).toHaveValue(filters.end_date)
  await expect(region).toContainText(filters.timezone)
  const table = region.getByRole('table', { name: 'Agent activity breakdown' })
  await expect(table.locator('tbody tr')).toHaveCount(projectStats.rows.length)
  for (const row of projectStats.rows) {
    const rendered = table.locator('tbody tr').filter({ hasText: row.model ?? 'Model not recorded' })
    for (const field of ['input', 'cache_read', 'output'] as const) {
      const source = field === 'input' ? 'remaining_input' : field
      const value = rendered.locator(`[aria-label^="${field === 'cache_read' ? 'Cache' : field === 'input' ? 'Input' : 'Output'}:"]`)
      if (row.reported_usage[source] == null || !row.coverage.fields?.[field === 'input' ? 'cache_read' : field]?.measured_attempts) await expect(value).toHaveText('N/A')
      else await expect(value).toHaveText(formatTokens(row.reported_usage[source]))
    }
  }
  await region.getByRole('button', { name: 'See token details' }).click()
  await expect(region.getByLabel('Review outcomes', { exact: true })).toContainText('Changes requested: 1')
  await expect(region.getByLabel('Review outcomes', { exact: true })).toContainText('Reviews passed: 1')
  await expect(region).not.toContainText('classified')
  await region.getByRole('button', { name: 'Hide token details' }).click()
  await region.locator('.activity-chart canvas').focus()
  await region.locator('.activity-chart canvas').press('End')
  await expect(region.locator('[aria-live="polite"]')).toContainText('100 tokens')
  const chartViews = region.getByRole('group', { name: 'Chart view', exact: true })
  await expect(chartViews.getByRole('button')).toHaveText(['Tokens', 'Agents'])
  await expect(region.getByLabel('Metric', { exact: true })).toHaveCount(0)
  await expect(region.getByLabel('Color by', { exact: true })).toHaveCount(0)
  await chartViews.getByRole('button', { name: 'Agents', exact: true }).click()
  await region.locator('.activity-chart canvas').focus()
  await region.locator('.activity-chart canvas').press('End')
  await expect(region.locator('[aria-live="polite"]')).toContainText('100 tokens')
  await region.getByText('Daily values', { exact: true }).click()
  const daily = region.getByRole('table', { name: 'Exact daily usage' })
  await expect(daily.locator('tbody tr').last()).toContainText('100')
  await expect(daily.locator('thead th')).toHaveText(['Date', 'Input', 'Cache', 'Output', 'Total'])
  await region.getByText('Daily values', { exact: true }).click()
  for (const [label, group_by] of [['Agent', 'agent'], ['Agent + model', 'agent_model'], ['Model', 'model']] as const) {
    await region.getByRole('group', { name: 'Group agent usage' }).getByRole('button', { name: label, exact: true }).click()
    const grouped = await operation<SchemaTrackerAgentStatsOut>('tracker.agentStats', { project_id: project.id, ...filters, group_by })
    await expect(table.locator('tbody tr')).toHaveCount(grouped.rows.length)
    await metrics(region, grouped)
    await expect(chartViews.getByRole('button', { name: 'Agents', exact: true })).toHaveAttribute('aria-pressed', 'true')
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
  await expect(region.locator('[data-usage-field="input"] dd')).toHaveText('N/A')
  await expect(region.getByLabel('Input details', { exact: true })).toHaveCount(0)
  await expect(region).not.toContainText('Not reported')
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
    const source = field === 'input' ? 'remaining_input' : field
    const value = region.locator(`[data-usage-field="${field}"] dd[tabindex]`)
    if (totals.reported_usage[source] == null || !totals.launch_attempts || !totals.coverage.fields?.[field === 'input' ? 'cache_read' : field]?.measured_attempts) {
      await expect(value).toHaveText('N/A')
      await expect(value).toHaveAttribute('title', `${field === 'input' ? 'Input' : field === 'cache_read' ? 'Cache' : 'Output'}: N/A`)
      continue
    }
    await expect(value).toHaveText(formatTokens(totals.reported_usage[source]))
    await expect(value).toHaveAttribute('title', new RegExp(`${totals.reported_usage[source]!.toLocaleString('en-US')} tokens`))
  }
  await expect(region).not.toContainText('Partial')
}

test('analytics keeps its frame stable with many real identities and omits metadata-only expansions', async ({ page }, testInfo) => {
  test.setTimeout(180_000)
  expect(new URL(getBaseUrl()).port).not.toBe('5180')
  await resetProjects()
  const errors = trackConsoleErrors(page)
  const project = await createProject({ name: 'Layout stress project', slug: 'layout-stress', domain: 'synthetic.test' })
  await operation('tracker.createTask', { project_id: project.id, key: 'layout-stress', title: 'Synthetic layout proof' })
  const filters = localActivityFilters()
  for (let index = 0; index < 12; index++) {
    const key = `series-${index}`
    const name = `Customer_Agent ${index} with a long preserved identity for layout verification`
    await operation('tracker.createTicket', { project_id: project.id, task_key: 'layout-stress', key, title: name })
    const entry = receipt(index % 2 ? 'review_agent' : 'delivery_agent', index % 3 ? 'success' : 'repair', 1_250_000_000 + index * 1000, index % 2 ? 'gpt-6-astra' : 'gpt-6.1-sol')
    entry.agent = name
    entry.launch.agent = name
    entry.launch.started_at = new Date(Date.now() - (index < 6 ? 86_400_000 : 0)).toISOString()
    await operation('tracker.updateTicket', { project_id: project.id, ticket_key: key, patch_json: { completion_evidence_json: { automatic_delivery: { attempt: 1, delivery: entry } } } })
  }
  const before = await operation('tracker.get', { project_id: project.id })
  const evidence: object[] = []
  for (const width of [1280, 1536]) for (const theme of ['light', 'dark']) for (const scope of ['project', 'global']) {
    await page.setViewportSize({ width, height: 800 })
    await page.goto(scope === 'project' ? `/projects/${project.id}` : '/')
    await page.evaluate(value => { document.documentElement.dataset.theme = value }, theme)
    const region = page.getByRole('region', { name: 'Agent activity', exact: true })
    const table = region.getByRole('table', { name: 'Agent activity breakdown' })
    await expect(table.locator('tbody tr')).toHaveCount(2)
    await expect(table.locator('..')).toHaveCSS('scrollbar-gutter', 'stable')
    const frame = () => region.evaluate(root => {
      const origin = root.getBoundingClientRect()
      const bounds = (selector: string) => {
        const box = root.querySelector(selector)!.getBoundingClientRect()
        return [box.x - origin.x, box.y - origin.y, box.width, box.height].map(value => Math.round(value * 100) / 100)
      }
      return { plot: bounds('.activity-chart__plot'), legend: bounds('.activity-chart__legend'), caption: bounds('.activity-chart__selection'), columns: [...root.querySelectorAll('table[aria-label="Agent activity breakdown"] thead th')].map(cell => Math.round(cell.getBoundingClientRect().width * 100) / 100) }
    })
    const baseline = await frame()
    for (const view of ['Agents', 'Tokens']) {
      await region.getByRole('group', { name: 'Chart view', exact: true }).getByRole('button', { name: view, exact: true }).click()
      await expect(region.getByRole('heading', { name: view === 'Tokens' ? 'Tokens per day' : 'Tokens by agent per day' })).toBeVisible()
      expect(await frame()).toEqual(baseline)
      const canvas = region.locator('canvas')
      // Pointer proof must run before focus: keyboard selection deliberately
      // hides hover details. Target the last, nonzero day inside the plot.
      await expect(canvas).not.toBeFocused()
      await canvas.scrollIntoViewIfNeeded()
      const box = await canvas.boundingBox()
      expect(box).not.toBeNull()
      await canvas.hover({ position: { x: box!.width - 36, y: 105 } })
      const hover = region.getByRole('tooltip', { name: 'Daily usage details' })
      await expect(hover).toBeVisible()
      await expect(hover.locator('li')).toHaveCount(view === 'Agents' ? 12 : 3)
      await expect(hover.locator('footer')).toContainText('7,500,051,000')
      const hoverBounds = await hover.boundingBox()
      const plotBox = await canvas.boundingBox()
      expect(hoverBounds!.x).toBeGreaterThanOrEqual(plotBox!.x)
      expect(hoverBounds!.y).toBeGreaterThanOrEqual(plotBox!.y)
      expect(hoverBounds!.x + hoverBounds!.width).toBeLessThanOrEqual(plotBox!.x + plotBox!.width)
      expect(hoverBounds!.y + hoverBounds!.height).toBeLessThanOrEqual(plotBox!.y + plotBox!.height)
      if (view === 'Agents') {
        const list = hover.getByRole('list', { name: 'Daily series values' })
        await list.hover()
        await page.mouse.wheel(0, 600)
        await expect.poll(() => list.evaluate(node => node.scrollTop)).toBeGreaterThan(0)
        await expect(hover.locator('li').last()).toBeInViewport()
        await expect(hover.locator('li').last()).toContainText('1,250,009,000')
        await expect.poll(async () => {
          const last = await hover.locator('li').last().boundingBox()
          const visible = await list.boundingBox()
          return last!.y + last!.height - visible!.y - visible!.height
        }).toBeLessThanOrEqual(0)
        await expect(hover.locator('footer')).toBeInViewport()
      }
      expect(await frame()).toEqual(baseline)
      await page.screenshot({ path: testInfo.outputPath(`hover-${view}-${scope}-${width}-${theme}.png`), fullPage: true })
      await canvas.focus()
      await canvas.press('End')
      await expect(hover).toHaveCount(0)
      expect(await frame()).toEqual(baseline)
      await expect(region.locator('.activity-chart__exact')).toHaveClass(/sr-only/)
      await expect(region.locator('.activity-chart__selection')).not.toContainText(/Reported total|Shown|Customer_Agent|Cache:/)
      await canvas.hover()
      expect(await frame()).toEqual(baseline)
    }
    for (const [label, group] of [['Agent', 'agent'], ['Agent + model', 'agent_model'], ['Model', 'model']] as const) {
      await region.getByRole('group', { name: 'Group agent usage' }).getByRole('button', { name: label, exact: true }).click()
      const stats = await operation<SchemaTrackerAgentStatsOut>(scope === 'project' ? 'tracker.agentStats' : 'tracker.agentStatsAll', { ...(scope === 'project' ? { project_id: project.id } : { is_active: true }), ...filters, group_by: group })
      await expect(table.locator('thead th').first()).toHaveText(group === 'model' ? 'Selected model' : label)
      await expect(table.locator('tbody tr')).toHaveCount(stats.rows.length)
      expect(await frame()).toEqual(baseline)
      await expect(table.locator('details, summary')).toHaveCount(0)
      await expect(table).not.toContainText('Codex')
    }
    const overflow = await region.evaluate(root => [...root.querySelectorAll<HTMLElement>('*')].filter(node => node.clientWidth > 0 && node.scrollWidth > node.clientWidth + 1 && !node.closest('.sr-only') && !node.matches('.truncate, .activity-chart__legend-label')).map(node => node.className))
    expect(overflow).toEqual([])
    await region.getByRole('group', { name: 'Chart view', exact: true }).getByRole('button', { name: 'Agents', exact: true }).click()
    await expect(region.locator('.activity-chart__legend-label')).toHaveCount(12)
    await expect(region.locator('.activity-chart__legend-label').first()).toHaveAttribute('title', /Customer_Agent/)
    expect(await frame()).toEqual(baseline)
    await region.getByText('Daily values', { exact: true }).click()
    const daily = region.getByRole('table', { name: 'Exact daily usage' })
    await expect(daily.locator('tbody span').last()).toHaveAttribute('title', /tokens/)
    expect((await frame()).plot).toEqual(baseline.plot)
    await page.screenshot({ path: testInfo.outputPath(`layout-${scope}-${width}-${theme}.png`), fullPage: true })
    evidence.push({ width, theme, scope, baseline, final: await frame(), overflow })
  }
  expect(await operation('tracker.get', { project_id: project.id })).toEqual(before)
  errors.assertNone()
  writeFileSync(testInfo.outputPath('analytics-layout-proof.json'), JSON.stringify({ synthetic: true, evidence, console_errors: errors.errors }, null, 2))
})

async function readableMissingValues(region: Locator) {
  await expect(region.locator('.agent-usage-summary dd').filter({ hasText: 'Not reported' })).toHaveCount(0)
}

test('Work uses shared Input Cache Output through filtering, details and refresh', async ({ page }, testInfo) => {
  expect(new URL(getBaseUrl()).port).not.toBe('5180')
  expect(process.env.STACKOS_E2E_BASE_URL).toBe(getBaseUrl())
  await resetProjects()
  const errors = trackConsoleErrors(page)
  const project = await createProject({ name: 'Work Usage Proof', slug: 'work-usage-proof', domain: 'work.test' })
  const exact = receipt('delivery_agent', 'success', 11545485, 'recorded-sol')
  exact.usage.measured = { ...exact.usage.measured, input: 11545485, cache_read: 11232768, output: 46397, total: 11591882 }
  await seed(project.id, { attempt: 1, delivery: exact })
  await operation('tracker.createTicket', { project_id: project.id, task_key: 'synthetic-stats', key: 'legacy', title: 'Legacy usage' })
  const unavailable = { attempt: 1, delivery: { agent: 'legacy', launch: { host_key: 'codex' } } }
  await operation('tracker.updateTicket', { project_id: project.id, ticket_key: 'legacy', patch_json: { completion_evidence_json: { automatic_delivery: unavailable } } })
  for (const [key, automatic] of [
    ['zero', { attempt: 1, delivery: receipt('delivery_agent', 'success', 0, 'zero-model') }],
    ['unknown', unavailable],
    ['missing-cache', { attempt: 1, delivery: { ...receipt('delivery_agent', 'success', 50, 'older-model'), usage: { unit: 'tokens', status: 'measured', measured: { input: 50, output: 0, total: 50 } } } }],
    ['empty', null],
    ['planning', null],
    ['running', unavailable],
    ['manual-empty', null],
    ['reset', { attempt: 1, delivery: { ...receipt('delivery_agent', 'success', 999, 'reset-model'), usage: { status: 'unavailable', reason: 'operator_reset', measured: null } } }],
    ['manual', { attempt: 1, delivery: receipt('delivery_agent', 'success', 520193, 'manual-fixture') }],
  ] as const) {
    await operation('tracker.createTask', { project_id: project.id, key, title: `Usage ${key}` })
    await operation('tracker.createTicket', { project_id: project.id, task_key: key, key: `${key}-ticket`, title: `Usage ${key} ticket` })
    if (automatic) {
      await operation('tracker.updateTicket', { project_id: project.id, ticket_key: `${key}-ticket`, patch_json: { completion_evidence_json: { automatic_delivery: automatic } } })
    }
  }
  // Persisted method identity only. No delivery jobs or native agents are started.
  const agent = { name: 'synthetic', instructions: 'Synthetic frozen identity fixture.' }
  for (const [task_key, status] of [['synthetic-stats', 'stopped'], ['zero', 'complete'], ['missing-cache', 'complete'], ['empty', 'complete'], ['planning', 'complete'], ['running', 'running']]) {
    await operation('tracker.updateTask', { project_id: project.id, task_key, patch_json: { metadata_json: { delivery: { status, job_ref: 'synthetic:never-execute', delivery_agent: agent, review_agent: agent } } } })
  }
  for (const [key, input, cache, output] of [['large', 181384707, 157292160, 520193], ['billion', 1250000000, 0, 0]] as const) {
    await operation('tracker.createTask', { project_id: project.id, key, title: `Long automated task title for ${key} usage and readable progress` })
    await operation('tracker.createTicket', { project_id: project.id, task_key: key, key: `${key}-ticket`, title: `Large ${key} ticket` })
    const recorded = receipt('delivery_agent', 'success', input, 'large-model')
    recorded.usage.measured = { ...recorded.usage.measured, input, cache_read: cache, output, total: input + output }
    await operation('tracker.updateTicket', { project_id: project.id, ticket_key: `${key}-ticket`, patch_json: { completion_evidence_json: { automatic_delivery: { attempt: 1, delivery: recorded } } } })
    await operation('tracker.updateTask', { project_id: project.id, task_key: key, patch_json: { metadata_json: { delivery: { status: 'complete', job_ref: 'synthetic:never-execute', delivery_agent: agent, review_agent: agent } } } })
  }
  const index = await operation<TrackerSnapshot>('tracker.get', { project_id: project.id, task_index_only: true, include_graph: false })
  const focused = await operation<TrackerSnapshot>('tracker.get', { project_id: project.id, task_key: 'synthetic-stats', include_graph: true })
  const taskTotal = focused.tasks[0]!.agent_stats!
  expect(taskTotal.reported_usage).toMatchObject({ input: 11545485, remaining_input: 312717, cache_read: 11232768, output: 46397, total: 11591882 })
  expect(taskTotal.coverage.usage_status).toBe('partial')
  expect(index.tasks.find(task => task.key === 'synthetic-stats')!.agent_stats).toEqual(taskTotal)
  expect(focused.tickets.find(ticket => ticket.key === 'synthetic-receipts')!.agent_stats!.reported_usage.total).toBe(11591882)
  const before = await operation('tracker.get', { project_id: project.id })
  await page.goto(`/projects/${project.id}/tasks`)
  const board = page.getByRole('region', { name: 'Task board', exact: true })
  const card = board.getByRole('button', { name: /Synthetic statistics proof/ })
  await workMetrics(card, taskTotal)
  await expect(card.locator('dt')).toHaveText(['Input', 'Cache', 'Output'])
  await expect(card.locator('[data-usage-field="input"] dd')).toHaveText('312.72K')
  await expect(card.locator('[data-usage-field="input"] dd')).toHaveAttribute('title', 'Input: 312,717 tokens. Input missing for 1 attempt')
  await expect(card.locator('.tracker-board__identity').first()).toContainText('Automated')
  await expect(board.getByRole('button', { name: /Usage zero$/ })).toContainText('Automated')
  await expect(board.getByRole('button', { name: /Usage unknown$/ })).not.toContainText('Automated')
  for (const theme of ['light', 'dark']) {
    await page.evaluate(theme => {
      document.documentElement.dataset.theme = theme
      document.documentElement.classList.toggle('dark', theme === 'dark')
      document.documentElement.style.colorScheme = theme
    }, theme)
    for (const key of ['large', 'billion']) {
      const large = board.getByRole('button', { name: new RegExp(`Long automated task title for ${key}`) })
      await large.scrollIntoViewIfNeeded()
      await workMetrics(large, index.tasks.find(task => task.key === key)!.agent_stats!)
      const dimensions = await large.evaluate(card => ({ width: card.getBoundingClientRect().width, overflowing: card.scrollWidth > card.clientWidth }))
      expect(dimensions.width).toBeGreaterThanOrEqual(280)
      expect(dimensions.width).toBeLessThanOrEqual(320)
      expect(dimensions.overflowing).toBe(false)
      for (const value of await large.locator('dd[tabindex]').all()) {
        await expect(value).toHaveCSS('white-space', 'nowrap')
        await value.focus()
        await expect(value).toBeFocused()
        await expect(value).toHaveAttribute('title', /tokens/)
      }
      await expect(large.locator('.ui-badge')).toHaveClass(/text-info-fg/)
    }
    await page.screenshot({ path: testInfo.outputPath(`work-large-${theme}.png`), fullPage: true })
  }
  for (const key of ['zero', 'unknown', 'missing-cache', 'empty', 'reset']) {
    const task = index.tasks.find(task => task.key === key)!
    const row = board.getByRole('button', { name: new RegExp(`Usage ${key}$`) })
    if (['unknown', 'reset'].includes(key)) await expect(row.locator('.agent-usage-summary')).toHaveCount(0)
    else await workMetrics(row, task.agent_stats!)
    await expect(row).not.toContainText('No recorded usage')
  }
  for (const width of [1280, 760]) {
    await page.setViewportSize({ width, height: 800 })
    await page.evaluate(() => document.fonts.ready)
    for (const key of ['missing-cache', 'unknown']) {
      const row = board.getByRole('button', { name: new RegExp(`Usage ${key}$`) })
      await row.scrollIntoViewIfNeeded()
      if (key === 'unknown') await expect(row.locator('.agent-usage-summary')).toHaveCount(0)
      else await workMetrics(row, index.tasks.find(task => task.key === key)!.agent_stats!)
      await readableMissingValues(row)
    }
  }
  await page.setViewportSize({ width: 1280, height: 800 })
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
    if (['unknown', 'reset'].includes(key)) await expect(header).toHaveCount(0)
    else await workMetrics(header, index.tasks.find(task => task.key === key)!.agent_stats!)
  }
  // Manual work remains free of usage on every inspection path, even with measured tokens.
  await page.goto(`/projects/${project.id}/tasks`)
  const manualCard = board.getByRole('button', { name: /Usage manual$/ })
  await expect(manualCard.locator('.agent-usage-summary')).toHaveCount(0)
  await manualCard.click()
  await expect(dialog.getByLabel('Lifetime task usage', { exact: true })).toHaveCount(0)
  await dialog.getByRole('button', { name: /Usage manual ticket/ }).click()
  await expect(dialog.getByLabel('Own ticket usage', { exact: true })).toHaveCount(0)
  await dialog.getByRole('button', { name: 'Close', exact: true }).first().click()
  await page.goto(`/projects/${project.id}/tasks?view=graph&task=manual`)
  await expect(header).toHaveCount(0)
  const manualNode = page.locator('.ticket-graph-node')
  await expect(manualNode.locator('.agent-usage-summary')).toHaveCount(0)
  await manualNode.click()
  await page.getByRole('button', { name: 'Details', exact: true }).click()
  await expect(page.getByLabel('Own ticket usage', { exact: true })).toHaveCount(0)
  expect(await operation('tracker.get', { project_id: project.id })).toEqual(before)
  // No native receipts (main-thread planning) and a pending native invocation:
  // fixed slots agree across all six Work consumers; manual empty stays absent.
  for (const key of ['planning', 'running', 'manual-empty']) {
    const totals = index.tasks.find(task => task.key === key)!.agent_stats!
    await page.goto(`/projects/${project.id}/tasks`)
    const row = board.getByRole('button', { name: new RegExp(`Usage ${key}$`) })
    if (key === 'manual-empty') await expect(row.locator('.agent-usage-summary')).toHaveCount(0)
    else {
      await workMetrics(row, totals)
      await expect(row.locator('dd')).toHaveText(['N/A', 'N/A', 'N/A'])
    }
    await row.click()
    if (key === 'manual-empty') await expect(dialog.locator('.agent-usage-summary')).toHaveCount(0)
    else await workMetrics(dialog.getByLabel('Lifetime task usage', { exact: true }), totals)
    await dialog.getByRole('button', { name: new RegExp(`Usage ${key} ticket`) }).click()
    if (key === 'manual-empty') await expect(dialog.locator('.agent-usage-summary')).toHaveCount(0)
    else await workMetrics(dialog.getByLabel('Own ticket usage', { exact: true }), totals)
    await dialog.getByRole('button', { name: 'Close', exact: true }).first().click()
    await page.goto(`/projects/${project.id}/tasks?view=graph&task=${key}`)
    const node = page.locator('.ticket-graph-node')
    if (key === 'manual-empty') {
      await expect(header).toHaveCount(0)
      await expect(node.locator('.agent-usage-summary')).toHaveCount(0)
    } else {
      await workMetrics(header, totals)
      await workMetrics(node, totals)
    }
    await node.click()
    await page.getByRole('button', { name: 'Details', exact: true }).click()
    if (key === 'manual-empty') await expect(page.getByLabel('Own ticket usage', { exact: true })).toHaveCount(0)
    else await workMetrics(page.getByLabel('Own ticket usage', { exact: true }), totals)
  }
  await operation('tracker.updateTicket', { project_id: project.id, ticket_key: 'running-ticket', patch_json: { completion_evidence_json: { automatic_delivery: { attempt: 1, delivery: receipt('delivery_agent', 'success', 100, 'recorded-sol') } } } })
  await page.goto(`/projects/${project.id}/tasks?view=graph&task=running`)
  await expect(header.locator('dd')).toHaveText(['40', '60', '0'])
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(header.locator('dd')).toHaveText(['40', '60', '0'])
  await operation('tracker.updateTicket', { project_id: project.id, ticket_key: 'synthetic-receipts', patch_json: { completion_evidence_json: { automatic_delivery: { attempt: 1, delivery: receipt('delivery_agent', 'success', 125, 'recorded-sol') } } } })
  await page.goto(`/projects/${project.id}/tasks?view=graph&task=synthetic-stats`)
  const refreshed = await operation<TrackerSnapshot>('tracker.get', { project_id: project.id, task_key: 'synthetic-stats', include_graph: true })
  await workMetrics(header, refreshed.tasks[0]!.agent_stats!)
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await workMetrics(header, refreshed.tasks[0]!.agent_stats!)
  errors.assertNone()
  writeFileSync(testInfo.outputPath('work-api-ui-reconciliation.json'), JSON.stringify({ synthetic: true, base_url: getBaseUrl(), index, focused, taskTotal, console_errors: errors.errors }, null, 2))
})
