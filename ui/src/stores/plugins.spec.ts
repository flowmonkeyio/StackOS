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

  it('stores credentials through the local-admin endpoint and refreshes sanitized auth state', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      calls.push({ url, init })
      if (url === '/api/v1/auth/providers') return json([authProvider()])
      if (url === '/api/v1/projects/1/auth/status') return json(authStatus())
      if (url === '/api/v1/projects/1/auth/firecrawl/credentials') {
        return json({ data: authConnection() }, 201)
      }
      return json({})
    }) as typeof fetch

    const store = useStackOsCatalogStore()
    const response = await store.storeCredential(1, 'firecrawl', {
      auth_method_key: 'api_key',
      profile_key: 'default',
      label: 'Primary',
      fields: { api_key: 'fc-secret' },
    })

    const write = calls.find((call) => call.url.endsWith('/auth/firecrawl/credentials'))
    expect(write?.init?.method).toBe('POST')
    expect(JSON.parse(String(write?.init?.body))).toEqual({
      auth_method_key: 'api_key',
      profile_key: 'default',
      label: 'Primary',
      fields: { api_key: 'fc-secret' },
    })
    expect(response.data.credential_ref).toBe('cred_firecrawl')
    expect(store.authStatus?.connections[0].credential_ref).toBe('cred_firecrawl')
    expect(JSON.stringify(store.$state)).not.toContain('fc-secret')
  })

  it('tests and revokes credentials by opaque ref only', async () => {
    const postedBodies: unknown[] = []
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) postedBodies.push(JSON.parse(String(init.body)))
      if (url === '/api/v1/auth/providers') return json([authProvider()])
      if (url === '/api/v1/projects/1/auth/status') return json(authStatus())
      if (url === '/api/v1/projects/1/auth/test') {
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
      if (url === '/api/v1/projects/1/auth/revoke') {
        return json({
          data: {
            credential_ref: 'cred_firecrawl',
            provider_key: 'firecrawl',
            project_id: 1,
            revoked_at: '2026-05-22T00:01:00Z',
            status: 'revoked',
          },
        })
      }
      return json({})
    }) as typeof fetch

    const store = useStackOsCatalogStore()
    await store.testCredential(1, { credential_ref: 'cred_firecrawl' })
    await store.revokeCredential(1, { credential_ref: 'cred_firecrawl' })

    expect(postedBodies).toContainEqual({ credential_ref: 'cred_firecrawl' })
    expect(JSON.stringify(postedBodies)).not.toContain('secret')
  })
})

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
    project_id: 1,
    provider_key: 'firecrawl',
    auth_type: 'api-key',
    auth_method_key: 'api_key',
    profile_key: 'default',
    label: 'Primary',
    status: 'connected',
    expires_at: null,
    last_tested_at: null,
    revoked_at: null,
    scopes: [],
    account: null,
    setup_required: false,
  }
}

function authStatus() {
  return {
    project_id: 1,
    provider_key: null,
    providers: [authProvider()],
    connections: [authConnection()],
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}
