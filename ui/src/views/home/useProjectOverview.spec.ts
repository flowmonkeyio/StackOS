import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { defineComponent, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { useProjectsStore, type Project } from '@/stores/projects'
import { useProjectOverview } from './useProjectOverview'
import { portfolioFixture } from './overviewFixtures'

const originalFetch = globalThis.fetch
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { 'content-type': 'application/json' },
})
beforeEach(() => { setActivePinia(createPinia()) })
afterEach(() => { globalThis.fetch = originalFetch; vi.resetAllMocks() })

it('retains Home credentials for transient failure, clears denied data, and fences older reads', async () => {
  useProjectsStore().items = [{ ...portfolioFixture(), order_index: 0, locale: 'en', niche: null, schedule_json: null }]
  let state!: ReturnType<typeof useProjectOverview>
  const wrapper = mount(defineComponent({ setup() {
    state = useProjectOverview(ref(1))
    return () => null
  } }))
  try {
    const accepted = { project_id: 1, providers: [], accounts: [{ credential_ref: 'private-A', status: 'connected' }] }
    globalThis.fetch = vi.fn(async () => json(accepted)) as typeof fetch
    await state.load()
    globalThis.fetch = vi.fn(async () => json({ detail: 'Temporary outage' }, 503)) as typeof fetch
    await state.load()
    expect(state.accounts.value).toEqual(accepted.accounts)
    expect(state.connectionsLoaded.value).toBe(true)
    let resolve!: (value: Response) => void
    const older = new Promise<Response>(yes => { resolve = yes })
    globalThis.fetch = vi.fn(() => older) as typeof fetch
    const pending = state.load()
    const joined = state.load()
    expect(vi.mocked(fetch)).toHaveBeenCalledOnce()
    resolve(json(accepted))
    await Promise.all([pending, joined])
    globalThis.fetch = vi.fn(async () => json({ detail: 'Permission revoked' }, 403)) as typeof fetch
    await state.load()
    expect(state.accounts.value).toEqual([])
    expect(state.connectionsLoaded.value).toBe(false)
    expect(state.errors.value.connections).toContain('Permission revoked')
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
  } finally { wrapper.unmount() }
})

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
