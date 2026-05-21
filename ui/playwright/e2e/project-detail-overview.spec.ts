import { expect, test } from '@playwright/test'

import { createProject, resetProjects, trackConsoleErrors } from '../helpers'

test.describe('project detail — overview tab', () => {
  test.beforeEach(async () => {
    await resetProjects()
  })

  test('renders project metadata + recent activity table', async ({ page }) => {
    const errors = trackConsoleErrors(page)
    const project = await createProject({
      name: 'Overview Project',
      slug: 'overview-project',
      domain: 'overview.example.com',
      niche: 'qa',
    })
    await page.goto(`/projects/${project.id}/overview`)

    await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
    await expect(page.getByText('overview-project').first()).toBeVisible()
    await expect(page.getByText('Overview Project').first()).toBeVisible()
    await expect(page.getByRole('heading', { level: 3, name: 'Recent Runs' })).toBeVisible()
    await expect(
      page.getByRole('heading', { level: 3, name: 'Latest Resource Records' }),
    ).toBeVisible()
    await expect(page.getByText('en-US').first()).toBeVisible()
    errors.assertNone()
  })
})
