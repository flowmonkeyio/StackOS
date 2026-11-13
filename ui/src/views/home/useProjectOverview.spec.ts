import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { defineComponent, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { callOperation } from '@/lib/operations'
import { useProjectOverview } from './useProjectOverview'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
const originalFetch = globalThis.fetch
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { 'content-type': 'application/json' },
})
beforeEach(() => { setActivePinia(createPinia()); vi.mocked(callOperation).mockResolvedValue({ items: [] }) })
afterEach(() => { globalThis.fetch = originalFetch; vi.resetAllMocks() })

it('retains Home credentials for transient failure, clears denied data, and fences older reads', async () => {
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
    globalThis.fetch = vi.fn(async () => json({ detail: 'Permission revoked' }, 403)) as typeof fetch
    await state.load()
    resolve(json(accepted))
    await pending
    expect(state.accounts.value).toEqual([])
    expect(state.connectionsLoaded.value).toBe(false)
    expect(state.errors.value.connections).toContain('Permission revoked')
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
  } finally { wrapper.unmount() }
})
