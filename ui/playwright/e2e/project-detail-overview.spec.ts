import { expect, test } from '@playwright/test'

import { createProject, resetProjects, trackConsoleErrors } from '../helpers'

test.describe('project home — operations console', () => {
  test.beforeEach(async () => {
    await resetProjects()
  })

  test('renders the console sections and project metadata', async ({ page }) => {
    const errors = trackConsoleErrors(page)
    const project = await createProject({
      name: 'Overview Project',
      slug: 'overview-project',
      domain: 'overview.example.com',
      niche: 'qa',
    })
    // /overview redirects to the console at /projects/:id.
    await page.goto(`/projects/${project.id}/overview`)

    const main = page.getByRole('main')
    await expect(page.getByRole('heading', { level: 1, name: 'Overview Project' })).toBeVisible()
    await expect(main.getByText('overview-project').first()).toBeVisible()
    await expect(page.getByRole('heading', { level: 2, name: 'Needs you' })).toBeVisible()
    await expect(page.getByRole('heading', { level: 2, name: 'Agents at work' })).toBeVisible()
    await expect(page.getByRole('heading', { level: 2, name: 'Recent activity' })).toBeVisible()
    await expect(page.getByText('en-US').first()).toBeVisible()
    errors.assertNone()
  })
})
