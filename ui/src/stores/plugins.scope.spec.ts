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

describe('shared catalog authorization lifetime', () => {
  function catalogResponse(input: RequestInfo | URL) {
    const url = String(input)
    if (url.includes('/plugins?')) return json([{ slug: 'engineering', enabled_for_project: true }])
    if (url.endsWith('action.list/call')) return json({ items: [{ key: 'build' }], count: 1 })
    return json([{ key: 'catalog-row' }])
  }

  it.each([401, 403, 404])('invalidates accepted catalog on HTTP %s and permits an authorized empty retry', async status => {
    const store = useStackOsCatalogStore()
    globalThis.fetch = vi.fn(async () => json(auth(1))) as typeof fetch
    await store.refreshAuth(1)
    await store.refreshAccounts()
    const acceptedAuth = store.authStatus
    const acceptedAccounts = store.globalAccountsStatus
    globalThis.fetch = vi.fn(async input => catalogResponse(input)) as typeof fetch
    await store.refresh(1)
    expect(store.catalogLoaded).toBe(true)
    expect(store.actions).toHaveLength(1)
    globalThis.fetch = vi.fn(async () => json({ detail: 'Catalog denied' }, status)) as typeof fetch
    expect(await store.refreshPlugins(1)).toBe(false)
    expect(store.plugins).toEqual([])
    expect(store.pluginProjectId).toBeNull()
    expect(store.capabilities).toEqual([])
    expect(store.providers).toEqual([])
    expect(store.actions).toEqual([])
    expect(store.catalogLoaded).toBe(false)
    expect(store.catalogLoading).toBe(false)
    expect(store.loading).toBe(false)
    expect(store.pluginErrorStatus).toBe(status)
    expect(store.pluginError).toContain('Catalog denied')
    expect(store.catalogError).toContain('Catalog denied')
    expect(store.authStatus).toBe(acceptedAuth)
    expect(store.globalAccountsStatus).toBe(acceptedAccounts)
    globalThis.fetch = vi.fn(async input => String(input).endsWith('action.list/call') ? json({ items: [], count: 0 }) : json([])) as typeof fetch
    await store.refresh(1)
    expect(store.pluginProjectId).toBe(1)
    expect(store.catalogLoaded).toBe(true)
    expect(store.plugins).toEqual([])
    expect(store.pluginError).toBeNull()
    expect(store.catalogError).toBeNull()
  })

  it.each(['http', 'network'])('retains compatible catalog on transient %s failure', async failure => {
    const store = useStackOsCatalogStore()
    globalThis.fetch = vi.fn(async input => catalogResponse(input)) as typeof fetch
    await store.refresh(1)
    const accepted = [store.plugins, store.capabilities, store.providers, store.actions]
    globalThis.fetch = vi.fn(async () => {
      if (failure === 'network') throw new TypeError('Network offline')
      return json({ detail: 'Temporary outage' }, 503)
    }) as typeof fetch
    await store.refresh(1)
    expect([store.plugins, store.capabilities, store.providers, store.actions]).toEqual(accepted)
    expect(store.pluginProjectId).toBe(1)
    expect(store.catalogLoaded).toBe(true)
    expect(store.pluginError).toMatch(/offline|outage/)
    expect(store.catalogError).toBeTruthy()
    expect(store.catalogLoading).toBe(false)
  })

  it('fences an older full catalog after plugin denial, even across fresh recovery', async () => {
    const store = useStackOsCatalogStore()
    const actions = deferred<Response>()
    globalThis.fetch = vi.fn(async input => String(input).endsWith('action.list/call') ? actions.promise : catalogResponse(input)) as typeof fetch
    const older = store.refresh(1)
    await vi.waitFor(() => expect(store.pluginProjectId).toBe(1))
    globalThis.fetch = vi.fn(async () => json({ detail: 'Denied now' }, 403)) as typeof fetch
    await store.refreshPlugins(1)
    expect(store.catalogLoading).toBe(false)
    globalThis.fetch = vi.fn(async () => json([])) as typeof fetch
    expect(await store.refreshPlugins(1)).toBe(true)
    actions.resolve(json({ items: [{ key: 'obsolete' }], count: 1 }))
    await older
    expect(store.plugins).toEqual([])
    expect(store.actions).toEqual([])
    expect(store.capabilities).toEqual([])
    expect(store.providers).toEqual([])
    expect(store.catalogLoaded).toBe(false)
  })

  it.each(['/capabilities?', '/providers?', 'action.list/call'])('catalog denial at %s retires pending plugin reads without blocking a new read', async deniedPath => {
    const store = useStackOsCatalogStore()
    const oldPlugins = deferred<Response>()
    globalThis.fetch = vi.fn(async input => String(input).includes('/plugins?') ? oldPlugins.promise
      : String(input).includes(deniedPath) ? json({ detail: 'Inventory denied' }, 403) : catalogResponse(input)) as typeof fetch
    const older = store.refresh(1)
    const pending = store.refreshPlugins(1)
    await older
    expect(store.pluginErrorStatus).toBe(403)
    globalThis.fetch = vi.fn(async () => json([])) as typeof fetch
    expect(await store.refreshPlugins(1)).toBe(true)
    oldPlugins.resolve(catalogResponse('/api/v1/plugins?'))
    expect(await pending).toBe(false)
    expect(store.loading).toBe(false)
    expect(store.pluginProjectId).toBe(1)
    expect(store.plugins).toEqual([])
    expect(store.pluginError).toBeNull()
  })

  it('ignores old-project denial and old-project finally while the new project is loading', async () => {
    const store = useStackOsCatalogStore()
    const oldRead = deferred<Response>()
    const currentRead = deferred<Response>()
    globalThis.fetch = vi.fn(async input => String(input).includes('project_id=1') ? oldRead.promise : currentRead.promise) as typeof fetch
    const older = store.refreshPlugins(1)
    const current = store.refreshPlugins(2)
    expect(store.plugins).toEqual([])
    oldRead.resolve(json({ detail: 'Old project denied' }, 403))
    await older
    expect(store.loading).toBe(true)
    expect(store.pluginError).toBeNull()
    currentRead.resolve(catalogResponse('/api/v1/plugins?'))
    expect(await current).toBe(true)
    expect(store.pluginProjectId).toBe(2)
    expect(store.loading).toBe(false)
  })
})

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
