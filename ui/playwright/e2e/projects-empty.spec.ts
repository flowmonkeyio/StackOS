import { expect, test } from '@playwright/test'

import {
  getBaseUrl,
  getDaemonToken,
  resetProjects,
  trackConsoleErrors,
} from '../helpers'

test.describe('projects view — empty state', () => {
  test.beforeEach(async () => {
    await resetProjects()
  })

  test('shows the portfolio empty state without creation controls', async ({ page }) => {
    const errors = trackConsoleErrors(page)
    await page.goto('/projects')
    await expect(page.getByRole('heading', { level: 1, name: 'StackOS' })).toBeVisible()
    await expect(page.getByText('No projects yet')).toBeVisible()
    await expect(page.getByRole('button', { name: 'New project' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Create project' })).toHaveCount(0)

    const token = getDaemonToken()
    const base = getBaseUrl()
    const res = await fetch(`${base}/api/v1/projects?limit=1`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    expect(res.ok).toBeTruthy()
    const body = (await res.json()) as { items: unknown[] }
    expect(body.items).toHaveLength(0)
    errors.assertNone()
  })
})
