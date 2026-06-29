import { expect, test } from '@playwright/test'

import { createProject, resetProjects, trackConsoleErrors } from '../helpers'

test.describe('runs view — list + filters + sub-route navigation', () => {
  test.beforeEach(async () => {
    await resetProjects()
  })

  test('renders the audit-trail table with status pill bar', async ({ page }) => {
    const errors = trackConsoleErrors(page)
    const project = await createProject({
      name: 'Runs Project',
      slug: 'runs-project',
      domain: 'runs.example.com',
    })

    await page.goto(`/projects/${project.id}/runs`)
    await expect(page.getByRole('heading', { name: /^Runs/ })).toBeVisible()
    await expect(page.getByRole('tab', { name: 'Running' })).toBeVisible()
    await expect(page.getByRole('tab', { name: 'Success' })).toBeVisible()
    await expect(page.getByLabel('Kind')).toBeVisible()
    await expect(page.getByLabel('Parent run')).toBeVisible()

    errors.assertNone()
  })

  test('renders the empty-state correctly when no runs exist', async ({ page }) => {
    const errors = trackConsoleErrors(page)
    const project = await createProject({
      name: 'Runs Empty Project',
      slug: 'runs-empty-project',
      domain: 'runs-empty.example.com',
    })

    await page.goto(`/projects/${project.id}/runs`)
    await expect(page.getByRole('heading', { name: /^Runs/ })).toBeVisible()
    // The DataTable surfaces its empty-message slot.
    await expect(
      page.getByRole('cell', {
        name: 'No runs match these filters — runs are recorded when agents execute plans, skills, and tools.',
      }),
    ).toBeVisible()

    errors.assertNone()
  })
})
