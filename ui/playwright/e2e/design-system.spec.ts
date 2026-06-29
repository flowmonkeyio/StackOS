// Design-system verification protocol.
//
// For the core operator surfaces:
//   - every viewport in the responsive set renders with a visible h1, no
//     horizontal page overflow, and zero console errors;
//   - axe (WCAG 2.0/2.1 A+AA) passes at mobile and desktop widths in both
//     light and dark themes.

import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

import { createProject, resetProjects, trackConsoleErrors } from '../helpers'

const VIEWPORTS = [
  { width: 360, height: 740 },
  { width: 640, height: 800 },
  { width: 768, height: 900 },
  { width: 1024, height: 800 },
  { width: 1280, height: 800 },
  { width: 1440, height: 900 },
] as const

const ROUTES = [
  // overview redirects to the operations console (h1 = project name).
  { path: 'overview', heading: 'Protocol Project' },
  { path: 'inbox', heading: 'Inbox' },
  { path: 'activity', heading: 'Activity' },
  { path: 'setup', heading: 'Setup' },
  { path: 'runs', heading: /^Runs/ },
  { path: 'connections', heading: 'Connections' },
  { path: 'workflow-templates', heading: 'Workflow library' },
] as const

async function assertNoHorizontalOverflow(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => {
    const el = document.documentElement
    return el.scrollWidth - el.clientWidth
  })
  expect(overflow, 'document should not scroll horizontally').toBeLessThanOrEqual(0)
}

async function runAxe(page: Page, label: string): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  expect(
    results.violations,
    `axe violations on ${label}: ${results.violations
      .map((violation) => `${violation.id} (${violation.nodes.length})`)
      .join(', ')}`,
  ).toEqual([])
}

test.describe('design system — responsive + accessibility protocol', () => {
  test.beforeEach(async () => {
    await resetProjects()
  })

  test('core surfaces render across the viewport set without console errors', async ({ page }) => {
    test.setTimeout(180_000)
    const errors = trackConsoleErrors(page)
    const project = await createProject({
      name: 'Protocol Project',
      slug: 'protocol-project',
      domain: 'protocol.example.com',
      niche: 'qa',
    })

    for (const route of ROUTES) {
      for (const viewport of VIEWPORTS) {
        await page.setViewportSize(viewport)
        await page.goto(`/projects/${project.id}/${route.path}`)
        await expect(
          page.getByRole('heading', { level: 1, name: route.heading }),
        ).toBeVisible()
        await assertNoHorizontalOverflow(page)
      }
    }

    errors.assertNone()
  })

  test('axe passes on light theme at mobile and desktop widths', async ({ page }) => {
    test.setTimeout(180_000)
    const project = await createProject({
      name: 'Protocol Project',
      slug: 'protocol-project',
      domain: 'protocol.example.com',
      niche: 'qa',
    })

    for (const route of ROUTES) {
      for (const width of [360, 1280] as const) {
        await page.setViewportSize({ width, height: width < 600 ? 740 : 800 })
        await page.goto(`/projects/${project.id}/${route.path}`)
        await expect(
          page.getByRole('heading', { level: 1, name: route.heading }),
        ).toBeVisible()
        await runAxe(page, `${route.path} @ ${width} (light)`)
      }
    }
  })

  test('axe passes on dark theme at mobile and desktop widths', async ({ page }) => {
    test.setTimeout(180_000)
    const project = await createProject({
      name: 'Protocol Project',
      slug: 'protocol-project',
      domain: 'protocol.example.com',
      niche: 'qa',
    })

    await page.addInitScript(() => {
      window.localStorage.setItem('cs:theme', 'dark')
    })

    for (const route of ROUTES) {
      for (const width of [360, 1280] as const) {
        await page.setViewportSize({ width, height: width < 600 ? 740 : 800 })
        await page.goto(`/projects/${project.id}/${route.path}`)
        await expect(
          page.getByRole('heading', { level: 1, name: route.heading }),
        ).toBeVisible()
        await expect(page.locator('html.dark')).toHaveCount(1)
        await runAxe(page, `${route.path} @ ${width} (dark)`)
      }
    }
  })

  test('root portfolio holds the protocol across viewports + axe', async ({ page }) => {
    test.setTimeout(120_000)
    const errors = trackConsoleErrors(page)
    const project = await createProject({
      name: 'Protocol Project',
      slug: 'protocol-project',
      domain: 'protocol.example.com',
      niche: 'qa',
    })
    // Boot auto-opens the active project, so reach the portfolio via the brand
    // link (SPA nav, not a full reload that would redirect again).
    await page.goto(`/projects/${project.id}`)
    await page.getByRole('link', { name: 'StackOS home' }).click()
    await expect(page.getByRole('heading', { level: 1, name: 'StackOS' })).toBeVisible()

    for (const viewport of VIEWPORTS) {
      await page.setViewportSize(viewport)
      await assertNoHorizontalOverflow(page)
    }

    await page.setViewportSize({ width: 1280, height: 800 })
    await runAxe(page, 'portfolio @ 1280 (light)')
    errors.assertNone()
  })
})
