// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useStackOsCatalogStore } from './plugins'

const ORIG_FETCH = globalThis.fetch

describe('StackOS catalog store auth controls', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('shares concurrent plugin discovery between the shell and a domain view', async () => {
    let resolve!: (response: Response) => void
    globalThis.fetch = vi.fn(() => new Promise<Response>(value => { resolve = value })) as typeof fetch
    const store = useStackOsCatalogStore()
    const first = store.refreshPlugins(1)
    const second = store.refreshPlugins(1)
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
    resolve(json([{ slug: 'engineering', enabled_for_project: true }]))
    expect(await first).toBe(true)
    expect(await second).toBe(true)
    expect(store.plugins[0].slug).toBe('engineering')
  })

  it.each(['compact', 'full'])('supersedes pre-mutation %s catalog reads while coalescing the fresh read', async (kind) => {
    const old = deferred<Response>()
    const fresh = deferred<Response>()
    let compactReads = 0
    globalThis.fetch = vi.fn((input) => {
      const url = String(input)
      if (url === '/api/v1/plugins?project_id=1&compact=true') {
        compactReads++
        if (kind === 'full' && compactReads === 1) return Promise.resolve(json([plugin('finance', false)]))
        return kind === 'compact' && compactReads === 1 ? old.promise : fresh.promise
      }
      if (url.endsWith('action.list/call')) return kind === 'full' ? old.promise : Promise.resolve(json({ items: [], count: 0 }))
      return Promise.resolve(json([]))
    }) as typeof fetch
    const store = useStackOsCatalogStore()
    const older = kind === 'compact' ? store.refreshPlugins(1) : store.refresh(1)
    const newer = store.refreshPlugins(1, { silent: true, force: true })
    const shared = store.refreshPlugins(1, { silent: true })
    expect(compactReads).toBe(2)
    fresh.resolve(json([plugin('finance', true)]))
    expect(await newer).toBe(true)
    expect(await shared).toBe(true)
    old.resolve(json(kind === 'full' ? { items: [], count: 0 } : [plugin('finance', false)]))
    await older
    expect(store.enabledPlugins.map(row => row.slug)).toContain('finance')
    expect(store.plugins.filter(row => row.enabled_for_project).map(row => row.slug)).toContain('finance')
    expect(store.pluginProjectId).toBe(1)
    expect(store.loading).toBe(false)
    expect(store.catalogLoading).toBe(false)
  })

  it('stores credentials through the local-admin endpoint and refreshes sanitized auth state', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      calls.push({ url, init })
      if (url === '/api/v1/auth/accounts') return json(authStatus(null))
      if (url === '/api/v1/auth/accounts/firecrawl') {
        return json({ data: authConnection() }, 201)
      }
      return json({})
    }) as typeof fetch

    const store = useStackOsCatalogStore()
    const response = await store.storeCredential('firecrawl', {
      auth_method_key: 'api_key',
      display_name: 'Firecrawl - Primary',
      fields: { api_key: 'fc-secret' },
      attach_project_id: 1,
    })

    const write = calls.find((call) => call.url.endsWith('/auth/accounts/firecrawl'))
    expect(write?.init?.method).toBe('POST')
    expect(JSON.parse(String(write?.init?.body))).toEqual({
      auth_method_key: 'api_key',
      display_name: 'Firecrawl - Primary',
      fields: { api_key: 'fc-secret' },
      attach_project_id: 1,
    })
    expect(response.data.credential_ref).toBe('cred_firecrawl')
    expect(store.globalAccountsStatus?.accounts[0].credential_ref).toBe('cred_firecrawl')
    expect(JSON.stringify(store.$state)).not.toContain('fc-secret')
  })

  it('tests and revokes credentials by opaque ref only', async () => {
    const postedBodies: unknown[] = []
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) postedBodies.push(JSON.parse(String(init.body)))
      if (url === '/api/v1/auth/accounts') return json(authStatus(null))
      if (url === '/api/v1/auth/accounts/cred_firecrawl/test') {
        return json({
          data: {
            credential_ref: 'cred_firecrawl',
            provider_key: 'firecrawl',
            ok: true,
            status: 'ok',
            summary: 'Firecrawl credentials are reachable',
            checked_at: '2026-05-22T00:00:00Z',
            retryable: false,
            next_action: null,
            metadata: {},
          },
        })
      }
      if (url === '/api/v1/auth/accounts/cred_firecrawl/revoke') {
        return json({
          data: {
            credential_ref: 'cred_firecrawl',
            provider_key: 'firecrawl',
            revoked_at: '2026-05-22T00:01:00Z',
            status: 'revoked',
          },
        })
      }
      return json({})
    }) as typeof fetch

    const store = useStackOsCatalogStore()
    await store.testCredential('cred_firecrawl')
    await store.revokeCredential('cred_firecrawl')

    expect(postedBodies).toEqual([])
    expect(JSON.stringify(postedBodies)).not.toContain('secret')
  })

  it('controls an attached Telegram session with an explicit project-scoped request', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    const disconnected = telegramSession({ connected: false, desired_connected: false })
    const connected = telegramSession({ connected: true, desired_connected: true })
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      calls.push({ url, init })
      if (url.endsWith('/session')) return json(disconnected)
      if (url.endsWith('/session/connect')) return json({ data: connected })
      if (url.endsWith('/session/disconnect')) return json({ data: disconnected })
      if (url === '/api/v1/auth/accounts') return json(authStatus(null))
      if (url === '/api/v1/projects/7/connections/accounts') return json(authStatus(7))
      return json({})
    }) as typeof fetch

    const store = useStackOsCatalogStore()
    expect(await store.getTelegramAccountSession(7, 'cred_telegram')).toEqual(disconnected)
    expect(await store.connectTelegramAccountSession(7, 'cred_telegram')).toEqual(connected)
    expect(await store.disconnectTelegramAccountSession(7, 'cred_telegram')).toEqual(disconnected)

    expect(calls.map((call) => [call.url, call.init?.method ?? 'GET'])).toContainEqual([
      '/api/v1/projects/7/connections/accounts/cred_telegram/session',
      'GET',
    ])
    expect(calls.map((call) => [call.url, call.init?.method ?? 'GET'])).toContainEqual([
      '/api/v1/projects/7/connections/accounts/cred_telegram/session/connect',
      'POST',
    ])
    expect(calls.map((call) => [call.url, call.init?.method ?? 'GET'])).toContainEqual([
      '/api/v1/projects/7/connections/accounts/cred_telegram/session/disconnect',
      'POST',
    ])
    expect(JSON.stringify(calls)).not.toContain('bot_token')
  })

  it('keeps connector auth state scoped to the newest project request', async () => {
    const projectOne = deferred<void>()
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url.includes('/projects/1/')) await projectOne.promise
      const projectId = url.includes('/projects/1/') ? 1 : 2
      return json(authStatus(projectId))
    }) as typeof fetch
    const store = useStackOsCatalogStore()

    const older = store.refreshAuth(1)
    await store.refreshAuth(2)
    projectOne.resolve()
    await older

    expect(store.connectionProjectId).toBe(2)
    expect(store.authStatus?.project_id).toBe(2)
  })

  it.each([true, false])('keeps account and project-auth reads independent (accounts first: %s)', async (accountsFirst) => {
    const store = useStackOsCatalogStore()
    globalThis.fetch = vi.fn(async (input) => json(authStatus(String(input).includes('/projects/') ? 1 : null))) as typeof fetch
    await Promise.all([store.refreshAccounts(), store.refreshAuth(1)])
    const acceptedAccounts = store.globalAccountsStatus
    const acceptedAuth = store.authStatus
    const accounts = deferred<Response>()
    const auth = deferred<Response>()
    globalThis.fetch = vi.fn((input) => String(input).includes('/projects/') ? auth.promise : accounts.promise) as typeof fetch
    const accountRead = store.refreshAccounts()
    const authRead = store.refreshAuth(1)
    expect(store.globalAccountsStatus).toEqual(acceptedAccounts)
    expect(store.authStatus).toEqual(acceptedAuth)
    expect(store.accountsLoading).toBe(true)
    expect(store.authLoading).toBe(true)
    if (accountsFirst) {
      accounts.resolve(json({ detail: 'Accounts offline' }, 503))
      await accountRead
      expect(store.accountsError).toContain('Accounts offline')
      expect(store.authLoading).toBe(true)
      auth.resolve(json(authStatus(1)))
    } else {
      auth.resolve(json({ detail: 'Connections offline' }, 503))
      await authRead
      expect(store.authError).toContain('Connections offline')
      expect(store.accountsLoading).toBe(true)
      accounts.resolve(json(authStatus(null)))
    }
    await Promise.all([accountRead, authRead])
    expect(store.accountsLoading).toBe(false)
    expect(store.authLoading).toBe(false)
    expect(accountsFirst ? store.accountsError : store.authError).toContain('offline')
    expect(store.globalAccountsStatus).toEqual(acceptedAccounts)
    expect(store.authStatus).toEqual(acceptedAuth)
  })

  it('clears project authority after a denied read and ignores results after scope exit', async () => {
    const store = useStackOsCatalogStore()
    globalThis.fetch = vi.fn(async () => json(authStatus(1))) as typeof fetch
    await store.refreshAuth(1)
    globalThis.fetch = vi.fn(async () => json({ detail: 'Forbidden project' }, 403)) as typeof fetch
    await store.refreshAuth(2)
    expect(store.authStatus).toBeNull()
    expect(store.authError).toContain('Forbidden project')
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
    const late = deferred<Response>()
    globalThis.fetch = vi.fn(() => late.promise) as typeof fetch
    const read = store.refreshAuth(1)
    store.releaseAuth(1)
    late.resolve(json(authStatus(1)))
    await read
    expect(store.authStatus).toBeNull()
    expect(store.connectionProjectId).toBeNull()
    expect(store.authLoading).toBe(false)
  })

  it('keeps confirmed revocation authoritative when readback fails and an older read arrives', async () => {
    const store = useStackOsCatalogStore()
    globalThis.fetch = vi.fn(async () => json(authStatus(null))) as typeof fetch
    await store.refreshAccounts()
    const late = deferred<Response>()
    globalThis.fetch = vi.fn((input) => {
      if (String(input).endsWith('/revoke')) return Promise.resolve(json({ data: {
        credential_ref: 'cred_firecrawl', provider_key: 'firecrawl', revoked_at: '2026-11-13T00:00:00Z', status: 'revoked',
      } }))
      return late.promise
    }) as typeof fetch
    const old = store.refreshAccounts()
    let readCount = 0
    const before = globalThis.fetch
    globalThis.fetch = vi.fn((input, init) => {
      if (String(input) === '/api/v1/auth/accounts' && ++readCount === 1)
        return Promise.resolve(json({ detail: 'Readback offline' }, 503))
      return before(input, init)
    }) as typeof fetch
    await store.revokeCredential('cred_firecrawl')
    late.resolve(json(authStatus(null)))
    await old
    expect(store.globalAccountsStatus?.accounts[0]?.status).toBe('revoked')
    expect(store.accountsError).toContain('Readback offline')
  })

  it('keeps confirmed attachment changes when both readbacks fail', async () => {
    const store = useStackOsCatalogStore()
    globalThis.fetch = vi.fn(async input => json(authStatus(String(input).includes('/projects/') ? 1 : null))) as typeof fetch
    await Promise.all([store.refreshAccounts(), store.refreshAuth(1)])
    const account = { ...store.globalAccountsStatus!.accounts[0]!, project_ids: [] as number[] }
    globalThis.fetch = vi.fn(async (_input, init) => {
      if (init?.method === 'DELETE') return json({ data: account })
      if (init?.method === 'POST') return json({ data: { ...account, project_ids: [1] } })
      return json({ detail: 'Readback offline' }, 503)
    }) as typeof fetch
    await store.detachAccount(1, account.credential_ref)
    expect(store.authStatus?.accounts).toEqual([])
    expect(store.globalAccountsStatus?.accounts[0]?.project_ids).toEqual([])
    await store.attachAccount(1, account.credential_ref)
    expect(store.authStatus?.accounts[0]?.credential_ref).toBe(account.credential_ref)
    expect(store.globalAccountsStatus?.accounts[0]?.project_ids).toEqual([1])
    expect(store.authError).toContain('Readback offline')
    expect(store.accountsError).toContain('Readback offline')
  })

  it('reads the complete schema-free inventory without unused resource or manifest reads', async () => {
    const calls: string[] = []
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      calls.push(url)
      if (url === '/api/v1/plugins?project_id=1&compact=true') {
        return json([
          plugin('utils', true),
          plugin('seo', false),
        ])
      }
      if (url === '/api/v1/capabilities?project_id=1') {
        return json([{ plugin_slug: 'utils', key: 'web-retrieval' }])
      }
      if (url === '/api/v1/providers?project_id=1') {
        return json([{ plugin_slug: 'utils', key: 'firecrawl' }])
      }
      if (url === '/api/v1/operations/action.list/call') {
        return json({ items: Array.from({ length: 31 }, (_, i) => ({ plugin_slug: 'utils', key: `web.scrape.${i}` })), count: 31 })
      }
      return json({})
    }) as typeof fetch

    const store = useStackOsCatalogStore()
    await store.refresh(1)

    expect(calls).toEqual([
      '/api/v1/plugins?project_id=1&compact=true',
      '/api/v1/capabilities?project_id=1',
      '/api/v1/providers?project_id=1',
      '/api/v1/operations/action.list/call',
    ])
    const actionCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('action.list/call'))!
    expect(JSON.parse(String(actionCall[1]?.body))).toEqual({ arguments: {
      project_id: 1, include_unavailable_integrations: true, response_mode: 'raw',
    } })
    expect(store.plugins).toHaveLength(2)
    expect(store.enabledPlugins.map((row) => row.slug)).toEqual(['utils'])
    expect(store.actions).toHaveLength(31)
    expect(store.actions.at(-1)?.key).toBe('web.scrape.30')
    expect(store.catalogLoaded).toBe(true)
  })

  it('refreshes just plugin rows for app-shell navigation', async () => {
    const calls: string[] = []
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      calls.push(url)
      if (url === '/api/v1/plugins?project_id=1&compact=true') {
        return json([plugin('utils', true)])
      }
      return json({})
    }) as typeof fetch

    const store = useStackOsCatalogStore()
    const refreshed = await store.refreshPlugins(1, { silent: true })

    expect(refreshed).toBe(true)
    expect(calls).toEqual(['/api/v1/plugins?project_id=1&compact=true'])
    expect(store.loading).toBe(false)
    expect(store.pluginProjectId).toBe(1)
    expect(store.enabledPlugins.map((row) => row.slug)).toEqual(['utils'])
  })

  it('clears incompatible plugin links immediately even when the new scope fails', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url.includes('project_id=1')) return json([plugin('utils', true)])
      return json({ detail: 'unavailable' }, 503)
    }) as typeof fetch

    const store = useStackOsCatalogStore()
    expect(await store.refreshPlugins(1, { silent: true })).toBe(true)
    expect(await store.refreshPlugins(2, { silent: true })).toBe(false)

    expect(store.pluginProjectId).toBe(null)
    expect(store.enabledPlugins).toEqual([])
  })

  it('prevents an older full catalog refresh from replacing a newer plugin scope', async () => {
    let releaseProjectOne!: () => void
    const projectOneGate = new Promise<void>((resolve) => {
      releaseProjectOne = resolve
    })
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url.includes('project_id=1')) {
        await projectOneGate
        return url.includes('/plugins') ? json([plugin('utils', true)]) : json([])
      }
      if (url === '/api/v1/plugins?project_id=2&compact=true') {
        return json([plugin('seo', true)])
      }
      return json([])
    }) as typeof fetch

    const store = useStackOsCatalogStore()
    const older = store.refresh(1)
    const newer = store.refreshPlugins(2, { silent: true })
    await newer
    releaseProjectOne()
    await older

    expect(store.pluginProjectId).toBe(2)
    expect(store.enabledPlugins.map((row) => row.slug)).toEqual(['seo'])
  })

  it('publishes navigation before slow actions and retains it through refresh and catalog failure', async () => {
    const actionRead = deferred<Response>()
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url.includes('/plugins?')) return json([plugin('utils', true)])
      if (url.endsWith('action.list/call')) return actionRead.promise
      return json([])
    }) as typeof fetch
    const store = useStackOsCatalogStore()
    const first = store.refresh(1)
    await vi.waitFor(() => expect(store.pluginProjectId).toBe(1))
    expect(store.catalogLoading).toBe(true)
    const retained = store.plugins[0]
    actionRead.resolve(json({ items: [], count: 0 }))
    await first
    const failedRead = deferred<Response>()
    globalThis.fetch = vi.fn(async (input) => String(input).endsWith('action.list/call')
      ? failedRead.promise : String(input).includes('/plugins?')
        ? json([plugin('utils', true)]) : json([])) as typeof fetch
    const refresh = store.refresh(1)
    expect(store.plugins[0]).toBe(retained)
    expect(store.pluginProjectId).toBe(1)
    expect(store.catalogLoaded).toBe(true)
    failedRead.resolve(json({ detail: 'Actions unavailable' }, 503))
    await refresh
    expect(store.enabledPlugins.map(row => row.slug)).toEqual(['utils'])
    expect(store.catalogLoaded).toBe(true)
    expect(store.catalogError).toContain('Actions unavailable')
  })

  it('rejects a truncated action envelope and ignores late A results after A to B to A', async () => {
    const oldActions = deferred<Response>()
    let actionCalls = 0
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url.includes('/plugins?')) return json([plugin('utils', true)])
      if (url.endsWith('action.list/call')) {
        actionCalls++
        if (actionCalls === 1) return oldActions.promise
        return json({ items: [], count: 0 })
      }
      return json([])
    }) as typeof fetch
    const store = useStackOsCatalogStore()
    const old = store.refresh(1)
    await store.refresh(2)
    await store.refresh(1)
    oldActions.resolve(json({ items: [{ key: 'obsolete' }], count: 1 }))
    await old
    expect(store.actions).toEqual([])
    expect(store.catalogLoading).toBe(false)
    globalThis.fetch = vi.fn(async (input) => String(input).endsWith('action.list/call')
      ? json({ items: [], count: 31 }) : String(input).includes('/plugins?')
        ? json([plugin('utils', true)]) : json([])) as typeof fetch
    await store.refresh(1)
    expect(store.catalogError).toContain('Incomplete StackOS action inventory')
  })
})

function plugin(slug: string, enabledForProject: boolean) {
  return {
    id: slug === 'utils' ? 1 : 2,
    slug,
    name: slug,
    version: '1.0.0',
    description: '',
    source: 'builtin',
    manifest_json: {},
    created_at: '2026-05-26T00:00:00Z',
    updated_at: '2026-05-26T00:00:00Z',
    enabled_for_project: enabledForProject,
  }
}

function authProvider() {
  return {
    id: 1,
    plugin_id: 1,
    plugin_slug: 'utils',
    key: 'firecrawl',
    name: 'Firecrawl',
    description: 'Web crawling and scraping provider.',
    auth_type: 'api-key',
    auth_methods: [
      {
        key: 'api_key',
        label: 'API key',
        auth_type: 'api-key',
        description: '',
        interactive: false,
        payload_format: 'raw',
        payload_field: 'api_key',
        fields: [
          {
            key: 'api_key',
            label: 'API Key',
            type: 'secret',
            secret: true,
            required: true,
          },
        ],
        config: null,
      },
    ],
    scopes: [],
    config_json: null,
  }
}

function authConnection() {
  return {
    credential_ref: 'cred_firecrawl',
    credential_id: 1,
    provider_key: 'firecrawl',
    display_name: 'Firecrawl - Primary',
    auth_type: 'api-key',
    auth_method_key: 'api_key',
    status: 'connected',
    expires_at: null,
    last_tested_at: null,
    revoked_at: null,
    scopes: [],
    account: null,
    project_ids: [1],
    setup_required: false,
  }
}

function telegramSession(
  state: Pick<
    {
      desired_connected: boolean
      connected: boolean
    },
    'desired_connected' | 'connected'
  >,
) {
  return {
    credential_ref: 'cred_telegram',
    provider_key: 'telegram',
    ...state,
    status: state.connected ? 'connected' : 'disconnected',
    project_ids: [7, 8],
    affects_other_projects: true,
    next_action: state.connected ? null : 'Connect this Telegram Account when it is needed.',
  }
}

function authStatus(projectId: number | null = 1) {
  return {
    project_id: projectId,
    provider_key: null,
    providers: [authProvider()],
    accounts: [authConnection()],
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  const promise = new Promise<T>((next) => {
    resolve = next
  })
  return { promise, resolve }
}
