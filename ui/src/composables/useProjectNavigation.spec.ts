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

  it('remounts Agents in the selected project scope', async () => {
    const harness = await setupNavigation('/projects/1/agents')
    await expect(harness.navigation.selectProject(2)).resolves.toBe(true)
    expect(harness.router.currentRoute.value.path).toBe('/projects/2/agents')
    expect(harness.navigation.routeViewKey.value).toBe('project:2')
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

  it('hands exact management target IDs to rename/delete without selecting, routing or writing', async () => {
    const harness = await setupNavigation('/projects/1/connections')
    vi.mocked(fetch).mockClear()
    harness.navigation.requestProjectRename(2)
    harness.navigation.requestProjectDelete(2)
    expect(harness.navigation.renameProjectTarget.value?.id).toBe(2)
    expect(harness.navigation.deleteProjectTarget.value?.id).toBe(2)
    expect(harness.projects.activeProjectId).toBe(1)
    expect(harness.router.currentRoute.value.fullPath).toBe('/projects/1/connections')
    harness.projects.items = harness.projects.items.map((p) => p.id === 2 ? { ...p, name: 'Beta renamed' } : p)
    expect(harness.navigation.deleteProjectTarget.value?.name).toBe('Beta renamed')
    harness.navigation.requestProjectDelete(999)
    expect(harness.navigation.deleteProjectTarget.value?.id).toBe(2)
    harness.navigation.closeProjectDelete()
    harness.navigation.closeProjectRename()
    expect(harness.navigation.deleteProjectTarget.value).toBeNull()
    expect(harness.navigation.renameProjectTarget.value).toBeNull()
    expect(fetch).not.toHaveBeenCalled()
    harness.wrapper.unmount()
  })

  it('deletes the current project then switches through canonical routing to the first survivor in stored order', async () => {
    const harness = await setupNavigation('/projects/1/connections?section=bots&provider_key=old', [
      projectFixture(1, 'Alpha'), { ...projectFixture(3, 'Third'), order_index: 0 }, projectFixture(2, 'Beta'),
    ])
    harness.projects.items.sort((a, b) => a.order_index - b.order_index)
    harness.projects.catalogComplete = true
    harness.navigation.requestProjectDelete(1)
    mockDelete(1)
    await harness.projects.deleteProject(harness.projects.getById(1)!)
    expect(harness.navigation.deleteProjectTarget.value?.slug).toBe('alpha')
    expect(harness.navigation.projectNavSections.value).toEqual([])
    expect(await harness.navigation.finishProjectDeletion(1)).toBe(true)
    expect(harness.router.currentRoute.value.fullPath).toBe('/projects/3/connections?section=bots')
    expect(harness.projects.activeProjectId).toBe(3)
    harness.wrapper.unmount()
  })

  it.each(['/projects/1/agents', '/accounts?account=keep', '/'])(
    'deleting a noncurrent target preserves route and selection on %s', async (path) => {
      const harness = await setupNavigation(path)
      mockDelete(2)
      await harness.projects.deleteProject(harness.projects.getById(2)!)
      expect(await harness.navigation.finishProjectDeletion(2)).toBe(true)
      expect(harness.router.currentRoute.value.fullPath).toBe(path)
      expect(harness.projects.activeProjectId).toBe(1)
      harness.wrapper.unmount()
    },
  )

  it('goes to global Overview after the last project, with no deleted project links or reselection', async () => {
    const harness = await setupNavigation('/projects/1/agents', [projectFixture(1, 'Alpha')])
    harness.projects.catalogComplete = true
    mockDelete(1)
    await harness.projects.deleteProject(harness.projects.getById(1)!)
    expect(await harness.navigation.finishProjectDeletion(1)).toBe(true)
    expect(harness.router.currentRoute.value.fullPath).toBe('/')
    expect(harness.projects.activeProjectId).toBeNull()
    expect(harness.navigation.projectNavSections.value).toEqual([])
    expect(await harness.navigation.selectProject(1)).toBe(false)
    expect(harness.projects.activeProjectId).toBeNull()
    harness.wrapper.unmount()
  })

  it('retains successful cleanup when routing is blocked and permits navigation-only retry', async () => {
    const harness = await setupNavigation('/projects/1/connections')
    harness.projects.catalogComplete = true
    const removeGuard = harness.router.beforeEach((to) => to.params.id === '2' ? false : true)
    mockDelete(1)
    await harness.projects.deleteProject(harness.projects.getById(1)!)
    expect(await harness.navigation.finishProjectDeletion(1)).toBe(false)
    expect(harness.projects.getById(1)).toBeNull()
    expect(harness.navigation.projectNavSections.value).toEqual([])
    expect(harness.navigation.currentProject.value).toBeNull()
    removeGuard()
    expect(await harness.navigation.finishProjectDeletion(1)).toBe(true)
    expect(harness.projects.activeProjectId).toBe(2)
    harness.wrapper.unmount()
  })

  it('preserves a newer selection made while cleanup was in flight and opens blocked Work through the same router', async () => {
    const harness = await setupNavigation('/projects/1/agents')
    let finish!: (response: Response) => void
    globalThis.fetch = vi.fn((input) => String(input).includes('project.delete')
      ? new Promise<Response>((resolve) => { finish = resolve }) : Promise.resolve(json([])))
    const deletion = harness.projects.deleteProject(harness.projects.getById(1)!)
    await harness.navigation.selectProject(2)
    finish(json({ data: { project_id: 1, status: 'deleted' } }))
    await deletion
    expect(await harness.navigation.finishProjectDeletion(1)).toBe(true)
    expect(harness.router.currentRoute.value.path).toBe('/projects/2/agents')
    expect(harness.projects.activeProjectId).toBe(2)
    expect(await harness.navigation.viewProjectWork(2)).toBe(true)
    expect(harness.router.currentRoute.value.path).toBe('/projects/2/tasks')
    harness.wrapper.unmount()
  })

  it('loads all survivor pages before choosing and blocks navigation when that required read fails', async () => {
    const harness = await setupNavigation('/projects/1/agents', [projectFixture(1, 'Alpha')])
    mockDelete(1)
    await harness.projects.deleteProject(harness.projects.getById(1)!)
    globalThis.fetch = vi.fn(async () => { throw new Error('unavailable') })
    expect(await harness.navigation.finishProjectDeletion(1)).toBe(false)
    expect(harness.router.currentRoute.value.path).toBe('/projects/1/agents')
    globalThis.fetch = vi.fn(async (input) => String(input).includes('/projects?')
      ? json({ items: [projectFixture(8, 'Unseen')], next_cursor: null }) : json([]))
    expect(await harness.navigation.finishProjectDeletion(1)).toBe(true)
    expect(harness.router.currentRoute.value.path).toBe('/projects/8/agents')
    harness.wrapper.unmount()
  })

  it('preserves a newer selection made while survivor loading is pending', async () => {
    const harness = await setupNavigation('/projects/1/agents', [
      projectFixture(1, 'Alpha'), projectFixture(2, 'Beta'), projectFixture(3, 'Gamma'),
    ])
    mockDelete(1)
    await harness.projects.deleteProject(harness.projects.getById(1)!)
    let finishCatalog!: (response: Response) => void
    globalThis.fetch = vi.fn((input) => String(input).includes('/projects?')
      ? new Promise<Response>((resolve) => { finishCatalog = resolve }) : Promise.resolve(json([])))
    const finishing = harness.navigation.finishProjectDeletion(1)
    expect(await harness.navigation.selectProject(3)).toBe(true)
    finishCatalog(json({ items: [projectFixture(2, 'Beta'), projectFixture(3, 'Gamma')], next_cursor: null }))
    expect(await finishing).toBe(true)
    expect(harness.router.currentRoute.value.path).toBe('/projects/3/agents')
    expect(harness.projects.activeProjectId).toBe(3)
    harness.wrapper.unmount()
  })

  it('does not supersede a later project selection still waiting for its leave guard', async () => {
    const harness = await setupNavigation('/projects/1/agents', [
      projectFixture(1, 'Alpha'), projectFixture(2, 'Beta'), projectFixture(3, 'Gamma'),
    ])
    mockDelete(1)
    await harness.projects.deleteProject(harness.projects.getById(1)!)
    let finishCatalog!: (response: Response) => void
    globalThis.fetch = vi.fn((input) => String(input).includes('/projects?')
      ? new Promise<Response>((resolve) => { finishCatalog = resolve }) : Promise.resolve(json([])))
    const finishing = harness.navigation.finishProjectDeletion(1)
    let resumeGuard!: (allowed: boolean) => void
    harness.router.beforeEach((to) => to.params.id === '3'
      ? new Promise<boolean>((resolve) => { resumeGuard = resolve }) : true)
    const selecting = harness.navigation.selectProject(3)
    await flushPromises()
    finishCatalog(json({ items: [projectFixture(2, 'Beta'), projectFixture(3, 'Gamma')], next_cursor: null }))
    // Keep navigation-only retry available until that newer choice commits.
    expect(await finishing).toBe(false)
    resumeGuard(true)
    expect(await selecting).toBe(true)
    expect(harness.router.currentRoute.value.path).toBe('/projects/3/agents')
    harness.wrapper.unmount()
  })

  it.each(['selector', 'router'])('rejects a now-deleted target after asynchronous %s navigation guards', async (caller) => {
    const harness = await setupNavigation('/projects/1/agents')
    let resumeGuard!: (allowed: boolean) => void
    harness.router.beforeEach((to) => to.params.id === '2'
      ? new Promise<boolean>((resolve) => { resumeGuard = resolve }) : true)
    const selecting = caller === 'selector' ? harness.navigation.selectProject(2)
      : harness.router.push('/projects/2/agents')
    await flushPromises()
    mockDelete(2)
    await harness.projects.deleteProject(harness.projects.getById(2)!)
    expect(await harness.navigation.finishProjectDeletion(2)).toBe(true)
    resumeGuard(true)
    if (caller === 'selector') expect(await selecting).toBe(false)
    else await selecting
    expect(harness.router.currentRoute.value.path).toBe('/projects/1/agents')
    expect(harness.projects.activeProjectId).toBe(1)
    expect(harness.navigation.currentProject.value?.id).toBe(1)
    harness.wrapper.unmount()
  })
})

function mockDelete(id: number): void {
  globalThis.fetch = vi.fn(async (input) => String(input).includes('project.delete')
    ? json({ data: { project_id: id, status: 'deleted' } }) : json([]))
}

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
      { path: '/projects/:id/agents', component: { template: '<div />' } },
      { path: '/projects/:id/tasks', component: { template: '<div />' } },
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
    order_index: id,
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
