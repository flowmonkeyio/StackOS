import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'

test('communicates the product and completes the core evaluation flow', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByRole('heading', { level: 1 })).toContainText('StackOS keeps it on track')
  await expect(page.getByLabel('StackOS local workflow execution preview')).toBeVisible()
  await expect(page.getByText('25', { exact: true })).toBeVisible()
  await expect(page.getByText('Claude Code connected', { exact: true })).toBeAttached()

  const downloadUrl = 'https://stackos.flowmonkey.io/StackOS/stackos-latest-mac-arm64.dmg'
  await expect(page.locator('.site-nav--desktop a[data-download="stackos-mac"]')).toHaveAttribute('href', downloadUrl)

  await page.getByRole('link', { name: 'See how it works' }).click()
  await expect(page.locator('#workflow')).toBeInViewport()

  await page.getByRole('tab', { name: /Shopify/ }).click()
  await expect(page.getByRole('heading', { name: 'A coordinated collection launch' })).toBeVisible()
  await expect(page.getByText('Build the collection', { exact: true }).first()).toBeVisible()

  await page.getByRole('tab', { name: /Finance/ }).click()
  await expect(page.getByRole('heading', { name: 'A repeatable month-end review' })).toBeVisible()
  await page.getByRole('button', { name: 'Replay ↻' }).click()
  const workflow = page.locator('#workflow')
  await expect(workflow.getByText('Understanding your request', { exact: true })).toBeVisible()
  await expect(workflow.getByText('Plan ready for review', { exact: true })).toBeVisible({ timeout: 3_000 })
  await expect(workflow.getByText('Step 1 of 5 is moving', { exact: true })).toBeVisible({ timeout: 3_000 })

  await page.locator('#install').scrollIntoViewIfNeeded()
  await expect(page.locator('#install').getByRole('link', { name: 'Download for Mac' })).toHaveAttribute('href', downloadUrl)
  await expect(page.locator('#install').getByText('Latest release · Apple silicon')).toBeVisible()

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
})

test('has no serious accessibility violations', async ({ page }) => {
  await page.goto('/')
  await page.waitForLoadState('networkidle')

  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()

  const materialViolations = results.violations.filter((violation) =>
    ['serious', 'critical'].includes(violation.impact ?? ''),
  )
  expect(materialViolations).toEqual([])
})

test('unknown routes return a branded, useful, and accessible 404 page', async ({ page }) => {
  const response = await page.goto('/this-page-does-not-exist')

  expect(response?.status()).toBe(404)
  await expect(page).toHaveTitle('Page not found | StackOS')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(
    'This page isn’t here. The rest of StackOS is.',
  )
  await expect(page.getByRole('link', { name: 'Go to StackOS home' })).toHaveAttribute('href', '/')
  await expect(page.getByRole('link', { name: 'Open getting started' })).toHaveAttribute(
    'href',
    '/getting-started',
  )
  await expect(page.getByRole('navigation', { name: 'Useful destinations' })).toBeVisible()
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex, nofollow')

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)

  const results = await new AxeBuilder({ page })
    .exclude('nuxt-error-overlay')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  const materialViolations = results.violations.filter((violation) =>
    ['serious', 'critical'].includes(violation.impact ?? ''),
  )
  expect(materialViolations).toEqual([])

  const htaccess = await readFile(new URL('../public/.htaccess', import.meta.url), 'utf8')
  expect(htaccess).toContain('ErrorDocument 404 /404.html')
  expect(htaccess).toContain('AddType application/x-apple-diskimage .dmg')

  await page.getByRole('link', { name: 'Go to StackOS home' }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
})

test('getting-started is a designed, user-first guide with one canonical Markdown source', async ({ page }) => {
  await page.goto('/getting-started')

  await expect(page.getByRole('heading', { level: 1 })).toContainText('StackOS is installed')
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Turn your first request')
  await expect(page.getByText('You installed StackOS. Now give it one real piece of work.')).toBeVisible()
  await expect(page.locator('.guide-hero .guide-start-path')).toHaveCount(0)
  await expect(page.locator('#guide-content .guide-start-path')).toBeVisible()
  await expect(page.locator('#guide-content .guide-client-paths')).toBeVisible()
  await expect(page.locator('#guide-content .guide-system-map')).toBeVisible()
  await expect(page.locator('.guide-client-paths__desktop-logo')).toHaveAttribute('src', '/images/claude.webp')
  const providerIcons = page.locator('.guide-system-map__providers img')
  await expect(providerIcons).toHaveCount(3)
  expect(await providerIcons.evaluateAll((images) => images.map((image) => image.getAttribute('src')))).toEqual([
    '/images/integrations/slack-icon.png',
    '/images/integrations/shopify-icon.png',
    '/images/integrations/wordpress-icon.png',
  ])
  if (test.info().project.name.startsWith('mobile')) {
    const picker = page.locator('.guide-client-paths__picker')
    await expect(picker).toBeVisible()
    await picker.getByRole('button', { name: 'Claude Desktop' }).click()
    await expect(page.locator('#guide-client-path-desktop')).toBeVisible()
    await expect(page.locator('#guide-client-path-folder')).toBeHidden()
  }
  await expect(page.getByRole('heading', { level: 2, name: '1. Open StackOS once' })).toBeVisible()
  await expect(page.getByText('Use StackOS for this project. Tell me which project you connected to')).toBeVisible()
  await expect(page.getByText('workspace.startSession')).toHaveCount(0)
  await expect(page.getByText('MCP bridge')).toHaveCount(0)
  await expect(page.locator('.guide-hero [data-download="stackos-mac"]')).toHaveCount(0)

  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute(
    'href',
    'https://stackos.flowmonkey.io/getting-started',
  )
  await expect(page).toHaveTitle('Getting started with StackOS after installation | StackOS')
  const structuredData = await page.locator('script[type="application/ld+json"]').allTextContents()
  expect(structuredData.some((entry) => entry.includes('Article'))).toBe(true)
  expect(structuredData.some((entry) => entry.includes('BreadcrumbList'))).toBe(true)

  const markdownResponse = await page.request.get('/getting-started.md')
  expect(markdownResponse.status()).toBe(200)
  expect(markdownResponse.headers()['content-type']).toContain('text/markdown')
  expect(markdownResponse.headers()['x-robots-tag']).toBe('noindex')
  expect(markdownResponse.headers().link).toContain('https://stackos.flowmonkey.io/getting-started')
  const markdown = await markdownResponse.text()
  expect(markdown).toContain('title: StackOS is installed. What happens next?')
  expect(markdown).toContain('::guide-start-path')
  expect(markdown).not.toContain('toolbox.call')

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
})

test('getting-started visuals stay contained and aligned at common screen widths', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'), 'One responsive sweep is sufficient')

  for (const width of [390, 768, 1024, 1280, 1366, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/getting-started')

    const layout = await page.evaluate(() => {
      const bounds = (selector: string) => {
        const rect = document.querySelector(selector)!.getBoundingClientRect()
        return { left: rect.left, right: rect.right, top: rect.top }
      }
      const desktopNav = document.querySelector<HTMLElement>('.guide-nav--desktop')!
      const mobileNav = document.querySelector<HTMLDetailsElement>('.guide-nav--mobile')!
      const clientPicker = document.querySelector<HTMLElement>('.guide-client-paths__picker')!
      const visibleClientPanels = Array.from(
        document.querySelectorAll<HTMLElement>('.guide-client-paths__routes article'),
      ).filter((panel) => getComputedStyle(panel).display !== 'none').length
      const chartBounds = ['.guide-start-path', '.guide-client-paths', '.guide-system-map'].map(bounds)
      const statusStyle = getComputedStyle(document.querySelector<HTMLElement>('.guide-start-path li:first-child code')!)
      const stepTops = Array.from(document.querySelectorAll<HTMLElement>('.guide-start-path__node')).map(
        (node) => node.getBoundingClientRect().top,
      )
      const targetIds = Array.from(document.querySelectorAll<HTMLAnchorElement>('.guide-nav a[href^="#"]')).map(
        (link) => link.hash.slice(1),
      )

      return {
        clientWidth: document.documentElement.clientWidth,
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        chartBounds,
        desktopNavDisplay: getComputedStyle(desktopNav).display,
        desktopNavRight: desktopNav.getBoundingClientRect().right,
        mobileNavDisplay: getComputedStyle(mobileNav).display,
        mobileNavOpen: mobileNav.open,
        clientPickerDisplay: getComputedStyle(clientPicker).display,
        visibleClientPanels,
        statusBackground: statusStyle.backgroundColor,
        statusFontSize: Number.parseFloat(statusStyle.fontSize),
        stepTopSpread: Math.max(...stepTops) - Math.min(...stepTops),
        missingTargets: targetIds.filter((id) => !document.getElementById(id)),
      }
    })

    expect(layout.overflow, `${width}px page overflow`).toBeLessThanOrEqual(1)
    for (const chart of layout.chartBounds) {
      expect(chart.left, `${width}px chart left edge`).toBeGreaterThanOrEqual(-1)
      expect(chart.right, `${width}px chart right edge`).toBeLessThanOrEqual(layout.clientWidth + 1)
    }
    expect(layout.statusBackground).toBe('rgba(0, 0, 0, 0)')
    expect(layout.statusFontSize).toBeGreaterThanOrEqual(9)
    expect(layout.missingTargets).toEqual([])

    if (width <= 760) {
      expect(layout.clientPickerDisplay).not.toBe('none')
      expect(layout.visibleClientPanels).toBe(1)
    } else {
      expect(layout.clientPickerDisplay).toBe('none')
      expect(layout.visibleClientPanels).toBe(2)
    }

    if (layout.desktopNavDisplay !== 'none') {
      expect(layout.chartBounds[1].left - layout.desktopNavRight, `${width}px chart/sidebar gap`).toBeGreaterThanOrEqual(24)
    } else {
      expect(layout.mobileNavDisplay).not.toBe('none')
      expect(layout.mobileNavOpen).toBe(false)
    }

    if (width > 700) expect(layout.stepTopSpread, `${width}px step alignment`).toBeLessThanOrEqual(1)
  }
})

test('getting-started guide has no serious accessibility violations', async ({ page }) => {
  await page.goto('/getting-started')
  await page.waitForLoadState('networkidle')

  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()

  const materialViolations = results.violations.filter((violation) =>
    ['serious', 'critical'].includes(violation.impact ?? ''),
  )
  expect(materialViolations).toEqual([])
})

test('loads the supplied brand assets and keeps compact interface copy readable', async ({ page }) => {
  await page.goto('/')
  await page.waitForLoadState('networkidle')

  for (const src of [
    '/images/openai.webp',
    '/images/claude.webp',
    '/images/gemini.webp',
    '/images/apple.webp',
  ]) {
    const image = page.locator(`img[src="${src}"]`).first()
    await expect(image).toBeAttached()
    expect(await image.evaluate((element: HTMLImageElement) => element.complete && element.naturalWidth > 0)).toBe(true)
  }

  await page.locator('#workflow').scrollIntoViewIfNeeded()
  await expect(page.locator('.workflow-conversation small').first()).toBeVisible()

  for (const [selector, minimumSize] of [
    ['.hero__proof', 9],
    ['.workbench__bar > span', 10],
    ['.workflow-conversation small', 9],
    ['.execution-receipt__row span', 9],
    ['.trust-principles span', 9],
    ['.site-footer__bottom', 9],
  ] as const) {
    const fontSize = await page.locator(selector).first().evaluate((element) =>
      Number.parseFloat(window.getComputedStyle(element).fontSize),
    )
    expect(fontSize).toBeGreaterThanOrEqual(minimumSize)
  }
})

test('keeps the story available with reduced motion', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/')
  await page.waitForLoadState('networkidle')

  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await page.locator('#workflow').scrollIntoViewIfNeeded()
  await expect(page.getByRole('tab', { name: /Content/ })).toBeVisible()
  await expect(page.getByText('Gather the source material', { exact: true }).first()).toBeVisible()
  await expect(page.locator('.workflow-marquee__track')).toHaveCSS('animation-name', 'none')
})

test('mobile navigation exposes every evaluation path', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('mobile'), 'Mobile-only navigation check')
  await page.goto('/')

  const toggle = page.getByRole('button', { name: 'Toggle navigation' })
  await toggle.click()
  await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible()
  await page.locator('#site-navigation').getByRole('link', { name: 'Security' }).click()
  await expect(page).toHaveURL(/#security$/)
  await page.locator('#security').scrollIntoViewIfNeeded()
  await expect(page.locator('#security')).toBeInViewport()
})

test('library exposes workflows, articles, cross-links, and production metadata', async ({ page }) => {
  await page.goto('/library')

  await expect(page.getByRole('heading', { level: 1 })).toContainText('See how AI work')
  await expect(page.getByRole('link', { name: /Engineering Tracked Delivery/ })).toBeVisible()
  await expect(page.getByRole('heading', { level: 2, name: /Three parts.*One complete job/ })).toBeVisible()

  const canonical = page.locator('link[rel="canonical"]')
  await expect(canonical).toHaveAttribute('href', 'https://stackos.flowmonkey.io/library')
  await expect(page.locator('script[type="application/ld+json"]').first()).toBeAttached()

  await page.goto('/library/workflows/branding-content-production')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Branding Content Production')
  await expect(page.getByRole('heading', { level: 2, name: 'The reusable stages of the work.' })).toBeVisible()
  await expect(page.getByText('What the agent does')).toBeVisible()
  await expect(page.getByText('Project setup required').first()).toBeVisible()
  await expect(page.getByRole('heading', { level: 2, name: 'Useful even when the whole path cannot run.' })).toBeVisible()
  await expect(page.getByText('Live plan')).toHaveCount(0)
  await expect(page.getByText('Works across connected apps')).toHaveCount(0)
  await expect(page.getByRole('link', { name: /Branding Narrative Writer/ })).toHaveAttribute('href', '/library/agents/branding-narrative-writer')

  await page.goto('/library/articles/what-is-an-agentic-workflow')
  await expect(page.getByRole('heading', { level: 1 })).toContainText('What is an agentic workflow')
  await expect(page.getByText('A complete content workflow, from request to verified result')).toBeVisible()
  await expect(
    page.getByRole('link', { name: 'AI agent vs. workflow vs. orchestrator', exact: true }),
  ).toBeVisible()

  if (test.info().project.name.startsWith('mobile')) {
    await expect(page.locator('.workflow-map__mobile').getByText('Interview Capture')).toBeVisible()
  }

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
})

test('catalog indexes render every generated definition on first navigation', async ({ page }) => {
  for (const [path, count, answer, titleTerm] of [
    ['/library/workflows', 25, 'What is AI workflow automation?', 'AI workflow automation'],
    ['/library/agents', 43, 'What are AI agents for business?', 'AI agents for business'],
    ['/library/orchestrators', 4, 'What is AI agent orchestration?', 'AI agent orchestration'],
  ] as const) {
    await page.goto(path)
    await expect(page.locator('.catalog-card')).toHaveCount(count)
    await expect(page.getByText(`${count} in the library`, { exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { level: 2, name: answer })).toBeVisible()
    await expect(page).toHaveTitle(new RegExp(titleTerm, 'i'))
    const hasFaqSchema = await page.locator('script[type="application/ld+json"]').evaluateAll(
      (scripts, question) => scripts.some((script) => script.textContent?.includes(question)),
      answer,
    )
    expect(hasFaqSchema).toBe(true)
  }
})

test('library collection routes share one page wrapper and hero rhythm', async ({ page }) => {
  async function measure(path: string) {
    await page.goto(path, { waitUntil: 'networkidle' })
    await page.evaluate(() => document.fonts.ready)
    await expect(page.locator('.library-collection-hero')).toBeVisible()

    return page.evaluate(() => {
      const box = (selector: string) => {
        const rect = document.querySelector(selector)!.getBoundingClientRect()
        return {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          width: Math.round(rect.width),
        }
      }

      return {
        shell: box('.library-collection-hero > .shell'),
        kicker: box('.library-collection-hero .library-kicker'),
        heading: box('.library-collection-hero h1'),
      }
    })
  }

  const orchestrators = await measure('/library/orchestrators')
  const integrations = await measure('/library/integrations')

  expect(integrations.shell).toEqual(orchestrators.shell)
  expect(integrations.kicker.x).toBe(orchestrators.kicker.x)
  expect(integrations.kicker.y).toBe(orchestrators.kicker.y)
  expect(integrations.heading.x).toBe(orchestrators.heading.x)
  expect(integrations.heading.y).toBe(orchestrators.heading.y)
})

test('library navigation and editorial visuals are compact, readable, and icon-led', async ({ page }) => {
  await page.goto('/library/articles/ai-agent-vs-workflow-vs-orchestrator')

  const navigationHeight = await page.locator('.library-nav').evaluate((element) =>
    Math.round(element.getBoundingClientRect().height),
  )
  expect(navigationHeight).toBeLessThanOrEqual(50)

  const scrollBehavior = await page.evaluate(() => window.getComputedStyle(document.documentElement).scrollBehavior)
  expect(scrollBehavior).toBe('auto')

  const heroVisual = page.locator('.article-hero__visual')
  await expect(heroVisual).toBeVisible()
  for (const src of ['/images/openai.webp', '/images/claude.webp', '/images/gemini.webp', '/images/stackos-icon.png']) {
    await expect(heroVisual.locator(`img[src="${src}"]`)).toBeVisible()
  }

  const heroVisualHeight = await heroVisual.evaluate((element) => Math.round(element.getBoundingClientRect().height))
  // The desktop visual may use a second title line; keep that readable without letting it dominate the hero.
  expect(heroVisualHeight).toBeLessThanOrEqual(test.info().project.name.startsWith('mobile') ? 250 : 320)

  const headingLink = page.locator('.article-prose h2 a').first()
  await expect(headingLink).toBeVisible()
  const headingStyle = await headingLink.evaluate((element) => {
    const style = window.getComputedStyle(element)
    return { fontSize: Number.parseFloat(style.fontSize), textDecoration: style.textDecorationLine }
  })
  expect(headingStyle.fontSize).toBeLessThanOrEqual(42)
  expect(headingStyle.textDecoration).toBe('none')

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
})

test('article tables remain structured and contained at every viewport', async ({ page }) => {
  await page.goto('/library/articles/ai-workflow-automation')

  const tableRegion = page.getByRole('region', { name: 'Scrollable table' })
  const table = tableRegion.locator('table')
  await expect(tableRegion).toBeVisible()
  await expect(table.getByRole('columnheader')).toHaveCount(3)
  await expect(table.getByRole('row')).toHaveCount(8)

  const layout = await tableRegion.evaluate((region) => {
    const tableElement = region.querySelector('table')!
    const firstCell = tableElement.querySelector('tbody td')!
    const header = tableElement.querySelector('th')!
    return {
      clientWidth: region.clientWidth,
      scrollWidth: region.scrollWidth,
      tableWidth: Math.round(tableElement.getBoundingClientRect().width),
      cellPaddingInline: Number.parseFloat(getComputedStyle(firstCell).paddingInline),
      headerBorder: getComputedStyle(header).borderBottomWidth,
    }
  })

  expect(layout.tableWidth).toBeGreaterThanOrEqual(720)
  expect(layout.cellPaddingInline).toBeGreaterThanOrEqual(16)
  expect(layout.headerBorder).toBe('1px')

  if (test.info().project.name.startsWith('mobile')) {
    expect(layout.scrollWidth).toBeGreaterThan(layout.clientWidth)
  } else {
    expect(layout.scrollWidth - layout.clientWidth).toBeLessThanOrEqual(1)
  }

  const pageOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(pageOverflow).toBeLessThanOrEqual(1)
})

test('workflow maps keep a readable, connected layout when stage copy is long', async ({ page }) => {
  await page.goto('/library/workflows/branding-content-production')
  const map = page.locator('.workflow-map')
  await expect(map).toBeVisible()

  if (test.info().project.name.startsWith('mobile')) {
    await expect(map.locator('.workflow-map__canvas')).toBeHidden()
    await expect(map.locator('.workflow-map__mobile li')).toHaveCount(10)

    const typography = await map.locator('.workflow-map__mobile li').first().evaluate((item) => ({
      title: Number.parseFloat(getComputedStyle(item.querySelector('strong')!).fontSize),
      summary: Number.parseFloat(getComputedStyle(item.querySelector('p')!).fontSize),
      status: Number.parseFloat(getComputedStyle(item.querySelector('b')!).fontSize),
    }))
    expect(typography).toEqual({ title: 19, summary: 16, status: 11 })
    return
  }

  const nodes = map.locator('.workflow-node')
  await expect(nodes).toHaveCount(10)

  const layout = await nodes.evaluateAll((items) => {
    const boxes = items.map((item) => {
      const rect = item.getBoundingClientRect()
      const title = item.querySelector('h3')!
      const summary = item.querySelector('p')!
      const scale = rect.width / 264
      return {
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height,
        effectiveTitleSize: Number.parseFloat(getComputedStyle(title).fontSize) * scale,
        effectiveSummarySize: Number.parseFloat(getComputedStyle(summary).fontSize) * scale,
        titleColor: getComputedStyle(title).color,
      }
    })

    const overlaps = boxes.some((box, index) => boxes.slice(index + 1).some((other) =>
      box.left < other.right && box.right > other.left && box.top < other.bottom && box.bottom > other.top,
    ))
    const rowTops = [...new Set(boxes.map((box) => Math.round(box.top)))].sort((a, b) => a - b)
    const rowGaps = rowTops.slice(1).map((top, index) => {
      const previousRowBottom = Math.max(...boxes
        .filter((box) => Math.abs(box.top - rowTops[index]!) < 3)
        .map((box) => box.bottom))
      return top - previousRowBottom
    })

    return { boxes, overlaps, rowGaps }
  })

  expect(layout.overlaps).toBe(false)
  expect(Math.max(...layout.boxes.map((box) => box.height)) - Math.min(...layout.boxes.map((box) => box.height))).toBeLessThan(1)
  expect(Math.min(...layout.rowGaps)).toBeGreaterThan(30)
  expect(Math.min(...layout.boxes.map((box) => box.effectiveTitleSize))).toBeGreaterThanOrEqual(18)
  expect(Math.min(...layout.boxes.map((box) => box.effectiveSummarySize))).toBeGreaterThanOrEqual(13)
  expect(layout.boxes.every((box) => box.titleColor === 'rgb(243, 241, 234)')).toBe(true)

  const edges = map.locator('.vue-flow__edge-path')
  await expect(edges).toHaveCount(9)
  const edgeState = await edges.evaluateAll((paths) => paths.map((path) => ({
    length: (path as SVGPathElement).getTotalLength(),
    dash: getComputedStyle(path).strokeDasharray,
  })))
  expect(edgeState.every((edge) => edge.length > 10 && (edge.dash === 'none' || edge.dash === ''))).toBe(true)

  const markerAlignment = await page.locator('.detail-list li').first().evaluate((item) => {
    const style = getComputedStyle(item)
    const marker = getComputedStyle(item, '::before')
    const firstLineCenter = Number.parseFloat(style.paddingTop) + Number.parseFloat(style.lineHeight) / 2
    const markerCenter = Number.parseFloat(marker.top) + Number.parseFloat(marker.height) / 2
    return Math.abs(firstLineCenter - markerCenter)
  })
  expect(markerAlignment).toBeLessThan(4)
})

test('generated visuals use one readable type system in cards and article heroes', async ({ page }) => {
  await page.goto('/library/articles')
  const compact = page.locator(
    '.article-card[href="/library/articles/ai-agent-vs-workflow-vs-orchestrator"] .generated-visual.is-compact',
  )
  await expect(compact).toBeVisible()

  const compactVisual = await compact.evaluate((visual) => {
    const read = (selector: string) => visual.querySelector(selector)?.textContent?.trim()
    const size = (selector: string) => Number.parseFloat(getComputedStyle(visual.querySelector(selector)!).fontSize)
    return {
      color: getComputedStyle(visual).getPropertyValue('--visual-color').trim(),
      eyebrow: read('.generated-visual__header div > span'),
      title: read('.generated-visual__header strong'),
      badge: read('.generated-visual__header > small'),
      titleSize: size('.generated-visual__header strong'),
      labelSize: size('.visual-node__label'),
      clientSize: size('.client-chip b'),
      outcomeSize: size('.outcome-list > span'),
    }
  })
  expect(compactVisual.titleSize).toBeGreaterThanOrEqual(17)
  expect(compactVisual.labelSize).toBeGreaterThanOrEqual(11)
  expect(compactVisual.clientSize).toBeGreaterThanOrEqual(11)
  expect(compactVisual.outcomeSize).toBeGreaterThanOrEqual(11)

  await page.goto('/library/articles/ai-agent-vs-workflow-vs-orchestrator')
  const full = page.locator('.generated-visual--roles').first()
  await expect(full).toBeVisible()
  const fullVisual = await full.evaluate((visual) => ({
    color: getComputedStyle(visual).getPropertyValue('--visual-color').trim(),
    eyebrow: visual.querySelector('.generated-visual__header div > span')?.textContent?.trim(),
    title: visual.querySelector('.generated-visual__header strong')?.textContent?.trim(),
    badge: visual.querySelector('.generated-visual__header > small')?.textContent?.trim(),
  }))
  expect(fullVisual).toEqual({
    color: compactVisual.color,
    eyebrow: compactVisual.eyebrow,
    title: compactVisual.title,
    badge: compactVisual.badge,
  })

  const selectionColor = await page.locator('.article-hero h1').evaluate((heading) =>
    getComputedStyle(heading, '::selection').backgroundColor,
  )
  expect(selectionColor).not.toBe('rgb(217, 255, 99)')
})

test('GA4 remains unloaded until analytics consent is granted', async ({ page, context }) => {
  await context.clearCookies()
  await page.goto('/library')

  await expect(page.locator('script[src*="googletagmanager.com/gtag/js"]')).toHaveCount(0)
  const collectionRequest = page.waitForRequest(
    request => /google-analytics\.com\/g\/collect/.test(request.url()),
    { timeout: 15_000 },
  )
  await page.getByRole('button', { name: 'Allow analytics' }).click()
  await expect(page.locator('script[src*="googletagmanager.com/gtag/js?id=G-KPZXCGXGG5"]')).toHaveCount(1)
  await expect.poll(() => page.evaluate(() => typeof window.gtag)).toBe('function')

  const analyticsRequest = await collectionRequest
  expect(new URL(analyticsRequest.url()).searchParams.get('tid')).toBe('G-KPZXCGXGG5')

  const queuedCommandType = await page.evaluate(() => {
    window.gtag('event', 'stackos_analytics_test')
    return Object.prototype.toString.call(window.dataLayer.at(-1))
  })
  expect(queuedCommandType).toBe('[object Arguments]')
})

test('integrations open plugin-first with a custom sort and exact brand assets', async ({ page }) => {
  await page.context().addCookies([{
    name: 'stackos-analytics-consent',
    value: 'denied',
    domain: '127.0.0.1',
    path: '/',
  }])
  await page.goto('/library/integrations')

  await expect(page.locator('.integration-search__view .is-active')).toHaveText('Plugins')
  await expect(page.locator('.integration-plugin')).toHaveCount(10)
  await expect(page.locator('.integrations-hero__map .integration-mark img')).toHaveCount(8)
  await expect(page.locator('.integrations-hero__map .integration-mark b')).toHaveCount(0)

  const sort = page.locator('.integration-sort__trigger')
  await expect(sort).toHaveText(/A–Z/)
  await sort.click()
  await page.getByRole('option', { name: 'Most actions' }).click()
  await expect(page.locator('.integration-plugin h2').first()).toHaveText('Trackbooth')

  await page.getByRole('button', { name: 'Providers' }).click()
  await expect(page.locator('.integration-card')).toHaveCount(52)
  await expect(page.locator('.integration-card .integration-mark img')).toHaveCount(51)
  await page.waitForFunction(() => Array.from(
    document.querySelectorAll<HTMLImageElement>('.integration-card .integration-mark img'),
  ).every((image) => image.complete && image.naturalWidth > 0))

  const logoState = await page.locator('.integration-card .integration-mark img').evaluateAll((images) => ({
    sources: images.map((image) => image.getAttribute('src')),
    broken: images.filter((image) => !(image as HTMLImageElement).complete || (image as HTMLImageElement).naturalWidth === 0).length,
  }))
  expect(logoState.broken).toBe(0)
  expect(logoState.sources).toContain('/images/integrations/slack-icon.png')
  expect(logoState.sources).toContain('/images/integrations/shopify-icon.png')
  expect(logoState.sources).toContain('/images/integrations/wordpress-icon.png')
  expect(logoState.sources).toContain('/images/integrations/channel-metrics.svg')
  expect(logoState.sources).toContain('/images/integrations/alibaba-wan.png')
  expect(logoState.sources).toContain('/images/integrations/clay.png')
  expect(logoState.sources).toContain('/images/integrations/dataforseo.jpeg')
  expect(logoState.sources).toContain('/images/integrations/google-g.png')
  expect(logoState.sources).toContain('/images/integrations/hubspot-icon.png')
  expect(logoState.sources).toContain('/images/integrations/microsoft-365.png')
  expect(logoState.sources).toContain('/images/integrations/trackbooth.png')
  expect(logoState.sources).toContain('/images/integrations/salesloft.jpeg')
  expect(logoState.sources).toContain('/images/integrations/outbrain.svg')
  expect(logoState.sources).toContain('/images/integrations/serper.svg')
  expect(logoState.sources).toContain('/images/openai.webp')
  expect(logoState.sources).toContain('/images/gemini.webp')

  await page.locator('#integration-query').fill('Ghost')
  await expect(page.locator('.integration-card')).toHaveCount(1)
  await expect(page.locator('.integration-card h2')).toHaveText('Ghost')
  const wordmarkWidth = await page.locator('.integration-card .integration-mark.is-wordmark').evaluate((mark) => mark.getBoundingClientRect().width)
  expect(wordmarkWidth).toBeGreaterThanOrEqual(100)

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
})

test('integration detail uses a readable logo, compact actions, and a connected Vue Flow path', async ({ page }) => {
  await page.goto('/library/integrations/ghost')

  await expect(page.getByRole('heading', { level: 1, name: 'Ghost' })).toBeVisible()
  const heroLogo = page.locator('.integration-detail-hero .integration-mark img')
  await expect(heroLogo).toHaveAttribute('src', '/images/integrations/ghost.png')
  expect(await heroLogo.evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth > 0)).toBe(true)

  await expect(page.locator('.integration-route-node')).toHaveCount(4)
  await expect(page.locator('.integration-route-flow .vue-flow__edge-path')).toHaveCount(3)
  await expect(page.locator('.integration-route-node').nth(0)).toContainText('Your request')
  await expect(page.locator('.integration-route-node').nth(1)).toContainText('StackOS plan')
  await expect(page.locator('.integration-route-node').nth(2)).toContainText('Ghost')
  await expect(page.locator('.integration-route-node').nth(3)).toContainText('Checked result')

  if (test.info().project.name.startsWith('mobile')) {
    await expect(page.locator('.integration-route-flow')).toHaveClass(/is-compact/)
  } else {
    await page.goto('/library/integrations/trackbooth')
    const actionCards = page.locator('.integration-action-grid article')
    await expect(actionCards).toHaveCount(36)
    const firstThree = await actionCards.evaluateAll((cards) => cards.slice(0, 3).map((card) => {
      const rect = card.getBoundingClientRect()
      return { top: Math.round(rect.top), height: Math.round(rect.height) }
    }))
    expect(new Set(firstThree.map((card) => card.top)).size).toBe(1)
    expect(Math.max(...firstThree.map((card) => card.height))).toBeLessThanOrEqual(180)
  }

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
})

test('plugin provider chips keep the overflow count aligned', async ({ page }) => {
  await page.goto('/library/integrations')
  const card = page.locator('.integration-plugin').filter({ hasText: 'Branding' })
  const providerRow = card.locator('.integration-plugin__providers')
  const more = card.locator('.integration-plugin__more')
  await expect(more).toBeVisible()

  const alignment = await providerRow.evaluate((row) => {
    const names = row.querySelector('div')!.getBoundingClientRect()
    const count = row.querySelector('.integration-plugin__more')!.getBoundingClientRect()
    return { topDelta: Math.abs(names.top - count.top), rowHeight: row.getBoundingClientRect().height, countHeight: count.height }
  })
  expect(alignment.topDelta).toBeLessThan(1)
  expect(alignment.rowHeight).toBeLessThanOrEqual(alignment.countHeight + 1)
})

test('library has no serious accessibility violations', async ({ page }) => {
  await page.goto('/library')
  await page.waitForLoadState('networkidle')

  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()

  const materialViolations = results.violations.filter((violation) =>
    ['serious', 'critical'].includes(violation.impact ?? ''),
  )
  expect(materialViolations).toEqual([])
})
