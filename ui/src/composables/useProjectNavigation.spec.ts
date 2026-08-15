import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { useProjectNavigation } from './useProjectNavigation'
import { useStackOsCatalogStore } from '@/stores/plugins'
import { useProjectsStore, type Project } from '@/stores/projects'

const ORIG_FETCH = globalThis.fetch

describe('useProjectNavigation', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn(async () => json([])) as typeof fetch
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('navigates first, then commits the selected project and scoped catalog', async () => {
    const harness = await setupNavigation('/projects/1/connections?section=bots&provider_key=old')
    vi.mocked(globalThis.fetch).mockClear()

    await expect(harness.navigation.selectProject(2)).resolves.toBe(true)
    await flushPromises()

    expect(harness.router.currentRoute.value.fullPath).toBe(
      '/projects/2/connections?section=bots',
    )
    expect(harness.projects.activeProjectId).toBe(2)
    expect(harness.navigation.navigationProjectId.value).toBe(2)
    expect(harness.navigation.routeViewKey.value).toBe('project:2')
    expect(harness.navigation.currentProject.value?.id).toBe(2)
    expect(requestedPluginProjects()).toContain(2)

    harness.wrapper.unmount()
  })

  it('keeps the old scope when navigation is prevented', async () => {
    const harness = await setupNavigation('/projects/1/connections')
    vi.mocked(globalThis.fetch).mockClear()
    harness.router.beforeEach((to) => (to.params.id === '2' ? false : true))

    await expect(harness.navigation.selectProject(2)).resolves.toBe(false)
    await flushPromises()

    expect(harness.router.currentRoute.value.fullPath).toBe('/projects/1/connections')
    expect(harness.projects.activeProjectId).toBe(1)
    expect(harness.navigation.navigationProjectId.value).toBe(1)
    expect(requestedPluginProjects()).not.toContain(2)

    harness.wrapper.unmount()
  })

  it('changes only the navigation fallback while a global surface stays mounted', async () => {
    const harness = await setupNavigation('/accounts?account=credential_123')
    vi.mocked(globalThis.fetch).mockClear()

    await expect(harness.navigation.selectProject(2)).resolves.toBe(true)
    await flushPromises()

    expect(harness.router.currentRoute.value.fullPath).toBe(
      '/accounts?account=credential_123',
    )
    expect(harness.projects.activeProjectId).toBe(2)
    expect(harness.navigation.navigationProjectId.value).toBe(2)
    expect(harness.navigation.routeViewKey.value).toBe('/accounts')
    expect(requestedPluginProjects()).toContain(2)

    harness.wrapper.unmount()
  })

  it('does not show another project identity for an unresolved project route', async () => {
    const harness = await setupNavigation('/projects/2', [projectFixture(1, 'Alpha')], 1)

    expect(harness.navigation.navigationProjectId.value).toBe(2)
    expect(harness.navigation.currentProject.value).toBeNull()

    harness.wrapper.unmount()

    const invalidHarness = await setupNavigation(
      '/projects/2junk',
      [projectFixture(1, 'Alpha')],
      1,
    )
    expect(invalidHarness.navigation.navigationProjectId.value).toBeNull()
    expect(invalidHarness.navigation.currentProject.value).toBeNull()
    expect(invalidHarness.navigation.routeViewKey.value).toBe('project:invalid')

    invalidHarness.wrapper.unmount()
  })

  it('retries a failed navigation-catalog refresh for the same project', async () => {
    let projectTwoAttempts = 0
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url.includes('project_id=2')) {
        projectTwoAttempts += 1
        if (projectTwoAttempts === 1) return json({ detail: 'unavailable' }, 503)
      }
      return json([])
    }) as typeof fetch
    const harness = await setupNavigation('/accounts')

    await harness.navigation.selectProject(2)
    await harness.navigation.selectProject(2)
    await flushPromises()

    expect(projectTwoAttempts).toBe(2)
    expect(harness.catalog.pluginProjectId).toBe(2)

    harness.wrapper.unmount()
  })
})

interface NavigationHarness {
  pinia: Pinia
  router: Router
  projects: ReturnType<typeof useProjectsStore>
  catalog: ReturnType<typeof useStackOsCatalogStore>
  navigation: ReturnType<typeof useProjectNavigation>
  wrapper: VueWrapper
}

async function setupNavigation(
  path: string,
  projectsList = [projectFixture(1, 'Alpha'), projectFixture(2, 'Beta')],
  activeProjectId = 1,
): Promise<NavigationHarness> {
  const pinia = createPinia()
  setActivePinia(pinia)
  const projects = useProjectsStore()
  const catalog = useStackOsCatalogStore()
  projects.items = projectsList
  projects.activeProjectId = activeProjectId

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/accounts', component: { template: '<div />' } },
      { path: '/projects/:id', component: { template: '<div />' } },
      { path: '/projects/:id/connections', component: { template: '<div />' } },
      { path: '/projects/:id/runs', component: { template: '<div />' } },
      { path: '/projects/:id/runs/:run_id', component: { template: '<div />' } },
    ],
  })
  await router.push(path)
  await router.isReady()

  let navigation!: ReturnType<typeof useProjectNavigation>
  const wrapper = mount(
    defineComponent({
      setup() {
        navigation = useProjectNavigation()
        return () => h('div')
      },
    }),
    { global: { plugins: [pinia, router] } },
  )
  await flushPromises()

  return { pinia, router, projects, catalog, navigation, wrapper }
}

function requestedPluginProjects(): number[] {
  return vi
    .mocked(globalThis.fetch)
    .mock.calls.map(([input]) => /project_id=(\d+)/.exec(String(input))?.[1])
    .filter((value): value is string => Boolean(value))
    .map(Number)
}

function projectFixture(id: number, name: string): Project {
  return {
    id,
    name,
    slug: name.toLowerCase(),
    domain: `${name.toLowerCase()}.test`,
    niche: 'platform',
    locale: 'en-US',
    is_active: true,
    schedule_json: null,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}
