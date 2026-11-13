import { afterEach, expect, it, vi } from 'vitest'
import { defineComponent, ref } from 'vue'
import { createPinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { useProjectsStore, type Project } from '@/stores/projects'
import { useProjectOverview } from './useProjectOverview'
import { portfolioFixture } from './overviewFixtures'

const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })

it('reuses quiet metadata and joins explicit refresh with a concurrent quiet read without losing fresh empty-project facts', async () => {
  const pinia = createPinia()
  const projects = useProjectsStore(pinia)
  const initial: Project = { ...portfolioFixture(), order_index: 0, locale: 'en', niche: null, schedule_json: null }
  projects.items = [initial]
  let finish!: (response: Response) => void
  globalThis.fetch = vi.fn(async input => String(input).includes('/connections/accounts')
    ? new Response(JSON.stringify({ accounts: [] }), { headers: { 'content-type': 'application/json' } })
    : new Promise<Response>(resolve => { finish = resolve }))
  let overview!: ReturnType<typeof useProjectOverview>
  const wrapper = mount(defineComponent({ setup() {
    overview = useProjectOverview(ref(1))
    return () => null
  } }), { global: { plugins: [pinia] } })
  try {
    await overview.load()
    expect(overview.project.value?.name).toBe(initial.name)
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => url === '/api/v1/projects/1')).toHaveLength(0)
    const refresh = overview.load(true)
    const poll = overview.load()
    const repeatedRefresh = overview.load(true)
    await flushPromises()
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => url === '/api/v1/projects/1')).toHaveLength(1)
    finish(new Response(JSON.stringify({ ...initial, name: 'Fresh empty project', domain: null, updated_at: '2026-11-13T18:00:00Z' }), { headers: { 'content-type': 'application/json' } }))
    await Promise.all([refresh, poll, repeatedRefresh])
    expect(overview.project.value).toMatchObject({ name: 'Fresh empty project', domain: null })
    expect(projects.getById(1)).toMatchObject({ name: 'Fresh empty project', domain: null })
    expect(overview.errors.value).toEqual({})
  } finally { wrapper.unmount() }
})
