import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useStackOsCatalogStore } from './plugins'
import { useConnectionCredentials } from '@/views/connections/useConnectionCredentials'

const originalFetch = globalThis.fetch
const wrappers: ReturnType<typeof mount>[] = []
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { 'content-type': 'application/json' },
})
const account = (id: number) => ({ credential_ref: `cred_${id}`, credential_id: id, provider_key: 'telegram', display_name: `Project ${id} account`, project_ids: [id], revoked_at: null, status: 'connected' })
const auth = (id: number | null) => ({ project_id: id, accounts: [account(id ?? 1)], providers: [] })
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(yes => { resolve = yes })
  return { promise, resolve }
}
function consumer(id: number) {
  let state!: ReturnType<typeof useConnectionCredentials>
  const wrapper = mount(defineComponent({ setup() {
    state = useConnectionCredentials(computed(() => id))
    return () => null
  } }))
  wrappers.push(wrapper)
  return { state, wrapper }
}
beforeEach(() => setActivePinia(createPinia()))
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); globalThis.fetch = originalFetch })

describe('project Account mutation readback ownership', () => {
  it.each(['attach', 'detach', 'connect', 'disconnect'] as const)('late %s cannot reclaim B or a new A lifetime', async action => {
    const store = useStackOsCatalogStore()
    for (const returnToA of [false, true]) {
      const write = deferred<Response>()
      globalThis.fetch = vi.fn(async (input, init) => {
        if (init?.method === 'POST' || init?.method === 'DELETE') return write.promise
        const url = String(input)
        return json(auth(url.includes('/projects/2/') ? 2 : url.includes('/projects/1/') ? 1 : null))
      }) as typeof fetch
      const a = consumer(1)
      await a.state.load()
      let mutation: Promise<unknown>
      if (action === 'attach') { a.state.selectAccount('cred_1'); mutation = a.state.attachSelectedAccount() }
      else if (action === 'detach') { a.state.requestDetach(a.state.connections.value[0]!); mutation = a.state.confirmDetach() }
      else if (action === 'connect') mutation = store.connectTelegramAccountSession(1, 'cred_1')
      else mutation = store.disconnectTelegramAccountSession(1, 'cred_1')
      a.wrapper.unmount()
      const b = consumer(2)
      await b.state.load()
      if (returnToA) { b.wrapper.unmount(); await consumer(1).state.load() }
      const accepted = store.authStatus
      const authReads = () => vi.mocked(fetch).mock.calls.filter(([url, init]) => String(url).includes('/connections/accounts') && !init?.method).length
      const reads = authReads()
      write.resolve(json({ data: action === 'attach' || action === 'detach' ? account(1) : { connected: action === 'connect' } }))
      await mutation
      expect(store.connectionProjectId).toBe(returnToA ? 1 : 2)
      expect(store.authStatus).toEqual(accepted)
      expect(authReads()).toBe(reads)
      if (!returnToA) expect(b.state.connections.value[0]?.credential_ref).toBe('cred_2')
      store.releaseAuth(returnToA ? 1 : 2)
    }
  })

  it('does not let a mutation captured before denial restore project authority', async () => {
    const store = useStackOsCatalogStore()
    const write = deferred<Response>()
    globalThis.fetch = vi.fn(async () => json(auth(1))) as typeof fetch
    await store.refreshAuth(1)
    globalThis.fetch = vi.fn(async (_input, init) => init?.method === 'POST' ? write.promise : json({ detail: 'Permission revoked' }, 403)) as typeof fetch
    const pending = store.attachAccount(1, 'cred_1')
    await store.refreshAuth(1)
    globalThis.fetch = vi.fn(async () => json(auth(1))) as typeof fetch
    write.resolve(json({ data: account(1) }))
    await pending
    expect(store.authStatus).toBeNull()
    expect(store.authError).toContain('Permission revoked')
    expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).toEqual(['/api/v1/auth/accounts'])
  })
})
