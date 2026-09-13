import { expect, test } from '@playwright/test'

import { createProject, getBaseUrl, getDaemonToken, resetProjects, trackConsoleErrors } from '../helpers'

test.describe('home — tickets and local service', () => {
  test.beforeEach(async () => {
    await resetProjects()
  })

  test('opens actual status-filtered tickets without implying live agents', async ({ page }) => {
    const errors = trackConsoleErrors(page)
    const project = await createProject({
      name: 'Overview Project',
      slug: 'overview-project',
      domain: 'overview.example.com',
      niche: 'qa',
    })
    await seedTickets(project.id)
    // /overview redirects to the console at /projects/:id.
    await page.goto(`/projects/${project.id}/overview`)

    await expect(page.getByRole('heading', { level: 1, name: 'Overview Project' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Tickets by status', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Project connections', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Configured workflows', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Latest action calls', exact: true })).toHaveCount(0)
    await expect(page.getByText('Agents are working', { exact: true })).toHaveCount(0)
    await expect(page.getByText('Agents at work', { exact: true })).toHaveCount(0)
    await page.getByRole('button', { name: 'In Progress: 1 tickets', exact: true }).click()
    await expect(page).toHaveURL(/\/tasks\?.*status=in-progress/)
    const destination = new URL(page.url())
    expect(destination.searchParams.get('view')).toBe('tickets')
    await expect(page.getByText('Active fixture ticket', { exact: true })).toBeVisible()
    await expect(page.getByText('Completed fixture ticket', { exact: true })).toHaveCount(0)
    errors.assertNone()
  })

  test('global status filters projects and the full service section stays visible in browser', async ({ page }) => {
    const errors = trackConsoleErrors(page)
    const project = await createProject({ name: 'Directory Project', slug: 'directory-project', domain: 'directory.example.com' })
    await createProject({ name: 'Another Project', slug: 'another-project', domain: 'another.example.com' })
    await seedTickets(project.id)
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Tickets by status', exact: true })).toBeVisible()
    const service = page.getByRole('region', { name: 'System status', exact: true })
    await expect(service.getByText('AI tool connections', { exact: true })).toBeVisible()
    await expect(service.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled()
    for (const name of ['Restart', 'Run doctor', 'Install or repair']) {
      await expect(service.getByRole('button', { name, exact: true })).toBeVisible()
      await expect(service.getByRole('button', { name, exact: true })).toBeDisabled()
    }
    await page.getByRole('button', { name: 'In Progress: 1 tickets', exact: true }).first().click()
    const portfolio = page.getByRole('table', { name: 'Project portfolio' })
    await expect(portfolio.getByText('Directory Project', { exact: true })).toBeVisible()
    await expect(portfolio.getByText('Another Project', { exact: true })).toHaveCount(0)
    await page.getByRole('button', { name: 'Clear ticket status filter', exact: true }).click()
    await page.getByRole('textbox', { name: 'Search projects' }).fill('Directory')
    // Search is explicit so typing alone cannot replace the current results.
    await page.getByRole('button', { name: 'Search', exact: true }).click()
    await page.getByRole('table', { name: 'Project portfolio' }).getByText('Directory Project', { exact: true }).click()
    await expect(page).toHaveURL(new RegExp(`/projects/${project.id}$`))
    errors.assertNone()
  })
})

async function seedTickets(projectId: number): Promise<void> {
  const headers = { Authorization: `Bearer ${getDaemonToken()}`, 'content-type': 'application/json' }
  for (const [operation, args] of [
    ['tracker.createTask', { project_id: projectId, key: 'fixture-work', title: 'Fixture work' }],
    ['tracker.createTicket', { project_id: projectId, task_key: 'fixture-work', key: 'fixture-active', title: 'Active fixture ticket', status: 'in-progress' }],
    ['tracker.createTicket', { project_id: projectId, task_key: 'fixture-work', key: 'fixture-complete', title: 'Completed fixture ticket', status: 'complete' }],
  ] as const) {
    const response = await fetch(`${getBaseUrl()}/api/v1/operations/${operation}/call`, {
      method: 'POST', headers, body: JSON.stringify({ arguments: args }),
    })
    expect(response.ok, `seed ${operation}`).toBe(true)
  }
}
