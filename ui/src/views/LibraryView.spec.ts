import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import LibraryView from './LibraryView.vue'

const client = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/client', () => ({ apiFetch: client.apiFetch, formatApiError: (e: unknown) => String(e) }))
const page = (items: object[], next_cursor: number | null = null) => ({ items, next_cursor, total_estimate: items.length })
const decision = (id: number, project_id = 1) => ({ id, project_id, title: `Decision ${id}`, decision: `Saved decision ${id}`, rationale: 'Actual reason', status: 'accepted', created_at: '2026-11-09' })
const wrappers: ReturnType<typeof mount>[] = []
async function open(path = '/projects/1/library') {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/projects/:id/library', component: LibraryView }] })
  await router.push(path)
  await router.isReady()
  const wrapper = mount({ template: '<RouterView />' }, { attachTo: document.body, global: { plugins: [router, createPinia()], stubs: { ProjectPageHeader: { template: '<header><h1>Library</h1><slot name="actions"/><slot name="tabs"/></header>' } } } })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, router }
}
function button(wrapper: ReturnType<typeof mount>, text: string) { return wrapper.findAll('button').find(x => x.text() === text)! }

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
})
