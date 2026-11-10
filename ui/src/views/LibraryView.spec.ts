import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import LibraryView from './LibraryView.vue'
import { useStackOsResourcesStore } from '@/stores/stackosResources'

const client = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/client', () => ({ apiFetch: client.apiFetch, formatApiError: (e: unknown) => String(e) }))
const page = (items: object[], next_cursor: number | null = null) => ({ items, next_cursor, total_estimate: items.length })
const decision = (id: number, project_id = 1) => ({ id, project_id, title: `Decision ${id}`, decision: `Saved decision ${id}`, rationale: 'Actual reason', status: 'accepted', created_at: '2026-11-09' })
const wrappers: ReturnType<typeof mount>[] = []
async function open(path = '/projects/1/library', pinia = createPinia()) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/projects/:id/library', component: LibraryView }] })
  await router.push(path)
  await router.isReady()
  const wrapper = mount({ template: '<RouterView />' }, { attachTo: document.body, global: { plugins: [router, pinia], stubs: { ProjectPageHeader: { template: '<header><h1>Library</h1><slot name="actions"/><slot name="tabs"/></header>' } } } })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, router }
}
function button(wrapper: ReturnType<typeof mount>, text: string) { return wrapper.findAll('button').find(x => x.text() === text)! }

async function failedRefreshFixture() {
  const state = { fail: false, hold: false, resolve: (() => {}) as () => void }
  client.apiFetch.mockImplementation(async (url: string) => {
    if (url.startsWith('/api/v1/resources?')) return []
    if (url.includes('/artifacts?')) return page([{ id: 2, project_id: 1, name: 'Current file', kind: 'document', uri: 'https://example.test/file' }])
    if (!url.includes('/decisions?')) return page([])
    if (url.includes('/projects/2/')) return page([decision(2, 2)])
    if (url.includes('after=50')) {
      if (state.fail) { state.fail = false; throw new Error('Rehydration unavailable') }
      if (state.hold) { state.hold = false; await new Promise<void>(resolve => { state.resolve = resolve }) }
      return page(Array.from({ length: 50 }, (_, i) => decision(i + 51)), 100)
    }
    if (url.includes('after=100')) return page([decision(101)])
    return page(Array.from({ length: 50 }, (_, i) => decision(i + 1)), 50)
  })
  const view = await open()
  for (let i = 0; i < 2; i++) {
    await button(view.wrapper, 'Load more Decisions').trigger('click')
    await flushPromises()
  }
  await view.wrapper.get('[data-source="decision:101"]').trigger('click')
  await flushPromises()
  const list = view.wrapper.get('table').element.parentElement!
  Object.defineProperty(list, 'clientHeight', { configurable: true, value: 200 })
  list.scrollTop = 1200
  const detail = view.wrapper.get('[data-library-detail]').element
  ;(view.wrapper.get('[data-library-detail] details').element as HTMLDetailsElement).open = true
  detail.scrollTop = 90
  state.fail = true
  await button(view.wrapper, 'Refresh').trigger('click')
  await flushPromises()
  expect(view.wrapper.text()).toContain('Rehydration unavailable')
  return { ...view, state, list }
}

describe('Library', () => {
  beforeEach(() => { client.apiFetch.mockReset().mockImplementation(async (url: string) => url.startsWith('/api/v1/resources?') ? [] : page([])) })
  afterEach(() => { wrappers.forEach(x => x.unmount()); wrappers.length = 0; document.body.innerHTML = '' })

  it('exposes exactly two tabs and keeps all human content types with typed identities', async () => {
    client.apiFetch.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/v1/resources?')) return []
      if (url.includes('/decisions?')) return page([decision(1)])
      if (url.includes('/resource-records?')) return page([{ id: 1, project_id: 1, title: 'Decision 1', plugin_slug: 'engineering', resource_key: 'engineering-evidence', data_json: { summary: 'Old unlinked evidence' } }])
      if (url.includes('/learnings?')) return page([{ id: 1, project_id: 1, statement: 'Old learning' }])
      if (url.includes('/experiments?')) return page([{ id: 1, project_id: 1, name: 'Meaningful experiment', hypothesis: 'Test a change' }])
      return page([])
    })
    const { wrapper } = await open()
    expect(wrapper.findAll('[role="tab"]').map(x => x.text())).toEqual(['Saved information', 'Files'])
    expect(wrapper.findAll('tbody tr')).toHaveLength(4)
    expect(client.apiFetch.mock.calls.some(([url]) => /timeline|snapshots|observations|metrics/.test(url))).toBe(false)
    await wrapper.find('[data-source="decision:1"]').trigger('keydown', { key: 'Enter' })
    expect(wrapper.get('[aria-label="Selected saved content"]').text()).toContain('Saved decision 1')
    await wrapper.get('input[placeholder="Search this list"]').setValue('Old learning')
    expect(wrapper.find('[aria-label="Selected saved content"]').exists()).toBe(false)
    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
  })

  it('preserves a later-page selection and scroll on refresh and restores keyboard focus on close', async () => {
    client.apiFetch.mockImplementation(async (url: string) => url.startsWith('/api/v1/resources?') ? [] : url.includes('/decisions?') ? url.includes('after=50') ? page([decision(51)]) : page(Array.from({ length: 50 }, (_, i) => decision(i + 1)), 50) : page([]))
    const { wrapper } = await open()
    await button(wrapper, 'Load more Decisions').trigger('click')
    await flushPromises()
    const row = wrapper.get('[data-source="decision:51"]')
    ;(row.element as HTMLElement).focus()
    await row.trigger('keydown', { key: 'Enter' })
    const list = wrapper.get('table').element.parentElement!
    Object.defineProperty(list, 'clientHeight', { configurable: true, value: 200 })
    list.scrollTop = 120
    await button(wrapper, 'Refresh').trigger('click')
    await flushPromises()
    expect(wrapper.get('[aria-label="Selected saved content"]').text()).toContain('Saved decision 51')
    expect(list.scrollTop).toBe(120)
    await wrapper.get('[aria-label="Close details"]').trigger('click')
    await flushPromises()
    expect(document.activeElement?.getAttribute('data-source')).toBe('decision:51')
  })

  it('keeps partial errors distinct, retries only the failed source, and rejects late project pages', async () => {
    let resolve!: (x: unknown) => void
    client.apiFetch.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/v1/resources?')) return []
      if (url.includes('/projects/1/decisions?')) return new Promise(r => { resolve = r })
      if (url.includes('/projects/1/learnings?')) throw new Error('Learning unavailable')
      if (url.includes('/projects/2/decisions?')) return page([decision(2, 2)])
      return page([])
    })
    const { wrapper, router } = await open()
    expect(wrapper.text()).toContain('Learning unavailable')
    const before = client.apiFetch.mock.calls.length
    client.apiFetch.mockImplementation(async () => page([]))
    await button(wrapper, 'Retry Learnings').trigger('click')
    await flushPromises()
    expect(client.apiFetch.mock.calls.length - before).toBe(1)
    client.apiFetch.mockImplementation(async (url: string) => url.startsWith('/api/v1/resources?') ? [] : url.includes('/projects/2/decisions?') ? page([decision(2, 2)]) : page([]))
    await router.push('/projects/2/library')
    await flushPromises()
    resolve(page([decision(99)]))
    await flushPromises()
    expect(wrapper.find('[data-source="decision:99"]').exists()).toBe(false)
    expect(wrapper.find('[data-source="decision:2"]').exists()).toBe(true)
  })

  it('uses canonical file safety and retains unsupported and Finance metadata', async () => {
    client.apiFetch.mockImplementation(async (url: string) => url.includes('/artifacts?') ? page([{ id: 4, project_id: 1, name: 'Private file', kind: 'document', plugin_slug: 'finance', uri: 'https://example.test/private.pdf' }, { id: 5, project_id: 1, name: 'Local reference', kind: 'file', uri: 'file:///private/old.pdf' }]) : [])
    const { wrapper } = await open('/projects/1/library?tab=files&item=artifact:4')
    expect(wrapper.text()).toContain('Finance files remain available through Finance')
    expect(wrapper.find('a[target="_blank"]').exists()).toBe(false)
    await wrapper.get('[data-source="artifact:5"]').trigger('click')
    expect(wrapper.text()).toContain('Reference unavailable')
    expect(wrapper.find('a[target="_blank"]').exists()).toBe(false)
  })

  it('synchronizes successful history navigation, tab reads, search and external filters', async () => {
    client.apiFetch.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/v1/resources?')) return []
      if (url.includes('/decisions?')) return page([decision(1)])
      if (url.includes('/artifacts?')) return page([{ id: 2, project_id: 1, name: 'Saved file', kind: 'document', uri: 'https://example.test/file' }])
      return page([])
    })
    const { wrapper, router } = await open('/projects/1/library?item=decision:1')
    await wrapper.get('input[type="search"]').setValue('Decision')
    await router.push('/projects/1/library?tab=files&item=artifact:2')
    await flushPromises()
    expect(wrapper.get<HTMLInputElement>('input[type="search"]').element.value).toBe('')
    expect(wrapper.get('[data-library-detail]').text()).toContain('Saved file')

    router.back()
    await flushPromises()
    expect(wrapper.get('[data-library-detail]').text()).toContain('Saved decision 1')
    router.forward()
    await flushPromises()
    expect(wrapper.get('[data-library-detail]').text()).toContain('Saved file')
    const reads = client.apiFetch.mock.calls.length
    await router.push('/projects/1/library?tab=files&item=artifact:2&plugin=absent')
    await flushPromises()
    expect(wrapper.find('[data-library-detail]').exists()).toBe(false)
    expect(router.currentRoute.value.query.item).toBeUndefined()
    expect(client.apiFetch.mock.calls).toHaveLength(reads)
  })

  it('ignores cancelled navigation and removes its route listener on unmount', async () => {
    client.apiFetch.mockImplementation(async (url: string) => url.startsWith('/api/v1/resources?') ? [] : url.includes('/decisions?') ? page([decision(1)]) : page([]))
    const { wrapper, router } = await open('/projects/1/library?item=decision:1')
    const reads = client.apiFetch.mock.calls.length
    const removeGuard = router.beforeEach(() => false)
    await router.push('/projects/2/library?tab=files')
    await flushPromises()
    expect(wrapper.get('[data-library-detail]').text()).toContain('Saved decision 1')
    expect(client.apiFetch.mock.calls).toHaveLength(reads)
    removeGuard()
    wrapper.unmount()
    await router.push('/projects/2/library?tab=files')
    await flushPromises()
    expect(client.apiFetch.mock.calls).toHaveLength(reads)
  })

  it('filters loaded plugin/type rows, retains compatible selection, and clears incompatible details', async () => {
    client.apiFetch.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/v1/resources?')) return []
      if (url.includes('/decisions?')) return page([decision(1)])
      if (url.includes('/resource-records?')) return page([
        { id: 1, project_id: 1, resource_id: 1, title: 'Evidence', plugin_slug: 'engineering', resource_key: 'engineering-evidence', data_json: {} },
        { id: 2, project_id: 1, resource_id: 2, title: 'Decision', plugin_slug: 'engineering', resource_key: 'engineering-decision', data_json: {} },
      ])
      return page([])
    })
    const { wrapper } = await open('/projects/1/library?item=resource_record:1')
    await wrapper.get('[aria-label="Plugin"]').trigger('click')
    await wrapper.findAll('[role="option"]').find(x => x.text() === 'Engineering')!.trigger('click')
    await flushPromises()
    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
    expect(wrapper.find('[aria-label="Selected saved content"]').exists()).toBe(true)
    await wrapper.get('[aria-label="Type"]').trigger('click')
    const option = wrapper.findAll('[role="option"]').find(x => x.text() === 'Engineering decision')!
    await option.trigger('click')
    await flushPromises()
    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
    expect(wrapper.find('[aria-label="Selected saved content"]').exists()).toBe(false)
    await wrapper.get('[data-source="resource_record:2"]').trigger('keydown', { key: ' ' })
    await flushPromises()
    expect(wrapper.find('[aria-label="Selected saved content"]').exists()).toBe(true)
  })

  describe.each([false, true])('full Library scope with paged=%s', paged => {
    it.each([
      ['saved', { pluginSlug: 'engineering' }],
      ['saved', { resourceKey: 'evidence' }],
      ['files', { pluginSlug: 'engineering' }],
    ] as const)('reconciles %s after a filtered canonical read %j', async (tab, filters) => {
      const records = [
        { id: 1, project_id: 1, resource_id: 1, title: 'Engineering evidence', name: 'Engineering file', plugin_slug: 'engineering', resource_key: 'evidence', kind: 'document' },
        { id: 2, project_id: 1, resource_id: 2, title: 'Branding guide', name: 'Branding file', plugin_slug: 'branding', resource_key: 'guide', kind: 'document' },
      ]
      const schemas = records.map(row => ({ id: row.resource_id, plugin_slug: row.plugin_slug, key: row.resource_key, name: row.title }))
      client.apiFetch.mockImplementation(async (url: string) => {
        const params = new URL(url, 'http://fixture.test').searchParams
        const scoped = records.filter(row => (!params.has('plugin_slug') || params.get('plugin_slug') === row.plugin_slug)
          && (!params.has('resource_key') || params.get('resource_key') === row.resource_key))
        if (url.startsWith('/api/v1/resources?')) return schemas.filter(schema => !params.has('plugin_slug') || schema.plugin_slug === params.get('plugin_slug'))
        if (!/resource-records|artifacts/.test(url)) return page([])
        return paged && scoped.length > 1 && !params.has('after') ? page(scoped.slice(0, 1), 1)
          : page(params.has('after') ? scoped.slice(1) : scoped)
      })
      const pinia = createPinia()
      const store = useStackOsResourcesStore(pinia)
      await store.refresh(1, filters)
      const { wrapper } = await open(`/projects/1/library?tab=${tab}`, pinia)
      if (paged) {
        expect(wrapper.text()).toContain('More available')
        await button(wrapper, tab === 'files' ? 'Load more Files' : 'Load more Saved records').trigger('click')
        await flushPromises()
      }
      expect(wrapper.find(`[data-source="${tab === 'files' ? 'artifact' : 'resource_record'}:2"]`).exists()).toBe(true)
      expect(wrapper.text()).toContain('All pages loaded')
      if (tab === 'saved') expect(store.resources.map(schema => schema.plugin_slug)).toEqual(['engineering', 'branding'])
    })
  })

  it('recovers the original third-page selection and reading position after refresh rehydration fails', async () => {
    const { wrapper, list } = await failedRefreshFixture()
    expect(wrapper.get('[data-library-detail]').text()).not.toContain('Saved decision 101')
    // A failed refresh can clamp the actual scroll viewport; retry must retain the original position.
    list.scrollTop = 500
    await button(wrapper, 'Retry Decisions').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-library-detail]').text()).toContain('Saved decision 101')
    expect(list.scrollTop).toBe(1200)
    expect(wrapper.get('[data-library-detail]').element.scrollTop).toBe(90)
    expect((wrapper.get('[data-library-detail] details').element as HTMLDetailsElement).open).toBe(true)
    expect(wrapper.text()).not.toContain('Rehydration unavailable')
  })

  it.each(['project', 'tab'])('discards pending recovery after a %s change', async change => {
    const { wrapper, router, state, list } = await failedRefreshFixture()
    state.hold = true
    await button(wrapper, 'Retry Decisions').trigger('click')
    await flushPromises()
    await router.push(change === 'project' ? '/projects/2/library' : '/projects/1/library?tab=files')
    await flushPromises()
    list.scrollTop = 7
    const before = client.apiFetch.mock.calls.length
    state.resolve()
    await flushPromises()
    expect(client.apiFetch.mock.calls.length).toBe(before)
    expect(wrapper.find('[data-library-detail]').exists()).toBe(false)
    expect(wrapper.find(`[data-source="${change === 'project' ? 'decision' : 'artifact'}:2"]`).exists()).toBe(true)
    expect(list.scrollTop).toBe(7)
    expect(wrapper.text()).not.toContain('Rehydration unavailable')
  })
})
