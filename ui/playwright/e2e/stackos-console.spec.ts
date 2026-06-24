import { expect, test } from '@playwright/test'

import { createProject, resetProjects, trackConsoleErrors } from '../helpers'

test.describe('StackOS console — generic project surfaces', () => {
  test.beforeEach(async () => {
    await resetProjects()
  })

  test('walks the current generic project routes without console errors', async ({ page }) => {
    const errors = trackConsoleErrors(page)
    const project = await createProject({
      name: 'Console Project',
      slug: 'console-project',
      domain: 'console.example.com',
      niche: 'qa',
    })
    const routes = [
      // The overview is the project home — its h1 is the project name.
      { path: 'overview', heading: 'Console Project' },
      { path: 'inbox', heading: 'Inbox' },
      { path: 'activity', heading: 'Activity' },
      { path: 'setup', heading: 'Setup' },
      { path: 'plugins', heading: 'Plugins' },
      { path: 'capabilities', heading: 'Capabilities' },
      { path: 'connections', heading: 'Connections' },
      { path: 'workflow-templates', heading: 'Workflow library' },
      { path: 'runs', heading: /^Runs/ },
      { path: 'data', heading: 'Project data' },
      { path: 'resources', heading: 'Data explorer' },
      { path: 'schedules', heading: 'Schedules' },
      { path: 'cost-budget', heading: 'Cost & Budget' },
    ] as const

    for (const route of routes) {
      await page.goto(`/projects/${project.id}/${route.path}`)
      await expect(page.getByRole('heading', { level: 1, name: route.heading })).toBeVisible()
    }

    errors.assertNone()
  })
})
