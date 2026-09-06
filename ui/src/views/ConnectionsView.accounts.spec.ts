import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import ConnectionsView from './ConnectionsView.vue'
import {
  apiKeyMethod,
  authConnection,
  authProvider,
  catalogJson,
  clickButton,
  json,
  mountConnections,
} from './connections/connectionsViewTestSupport'

const ORIG_FETCH = globalThis.fetch

describe('ConnectionsView reusable Accounts', () => {
  beforeEach(() => setActivePinia(createPinia()))

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('attaches a second Account from the same provider without duplicating the first', async () => {
    let attached = false
    const provider = authProvider('cloudflare-dns', 'Cloudflare DNS', 'api-key', apiKeyMethod())
    const first = authConnection({
      revokedAt: null,
      providerKey: 'cloudflare-dns',
      credentialRef: 'cred_cf_source',
      label: 'Cloudflare DNS - Source',
    })
    const second = {
      ...authConnection({
        revokedAt: null,
        providerKey: 'cloudflare-dns',
        credentialRef: 'cred_cf_target',
        label: 'Cloudflare DNS - Target',
      }),
      project_ids: [],
    }
    const pending = {
      ...authConnection({
        revokedAt: null,
        status: 'pending',
        providerKey: 'cloudflare-dns',
        credentialRef: 'cred_cf_pending',
        label: 'Cloudflare DNS - Pending',
      }),
      project_ids: [],
    }
    const staleRevoked = {
      ...authConnection({
        // Defend against old or partially migrated rows where only the status
        // represents revocation.
        revokedAt: null,
        status: 'revoked',
        providerKey: 'cloudflare-dns',
        credentialRef: 'cred_cf_revoked',
        label: 'Cloudflare DNS - Revoked',
      }),
      project_ids: [],
    }

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse
      if (url === '/api/v1/auth/accounts') {
        return json({
          project_id: null,
          provider_key: null,
          providers: [provider],
          accounts: [
            first,
            { ...second, project_ids: attached ? [1] : [] },
            pending,
            staleRevoked,
          ],
        })
      }
      if (url === '/api/v1/projects/1/connections/accounts') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [provider],
          accounts: attached ? [first, { ...second, project_ids: [1] }] : [first],
        })
      }
      if (url === '/api/v1/projects/1/connections/accounts/cred_cf_target') {
        expect(init?.method).toBe('POST')
        expect(init?.body).toBeUndefined()
        attached = true
        return json({ data: { ...second, project_ids: [1] } })
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/projects/:id/connections', component: ConnectionsView },
        { path: '/accounts', component: { template: '<div />' } },
      ],
    })
    await router.push('/projects/1/connections')
    await router.isReady()
    const wrapper = mountConnections(router)

    await vi.waitFor(() => expect(wrapper.text()).toContain('Cloudflare DNS - Source'))
    const providerGroup = wrapper.find('section[aria-label="Cloudflare DNS"]')
    const addProviderConnection = providerGroup
      .findAll('button')
      .find((candidate) => candidate.text().trim() === 'Add connection')
    expect(addProviderConnection).toBeDefined()
    await addProviderConnection?.trigger('click')

    expect(wrapper.text()).toContain('Accounts not yet attached')
    const accountSelect = wrapper.find<HTMLButtonElement>('button[role="combobox"]')
    await accountSelect.trigger('click')
    const optionLabels = () => wrapper.findAll('[role="option"]').map((option) => option.text())
    expect(optionLabels()).toContain('Cloudflare DNS - Target')
    expect(optionLabels()).toContain('Cloudflare DNS - Pending')
    expect(optionLabels()).not.toContain('Cloudflare DNS - Source')
    expect(optionLabels()).not.toContain('Cloudflare DNS - Revoked')
    expect(optionLabels().every((label) => !label.includes(' · '))).toBe(true)

    await wrapper.find<HTMLInputElement>('input[aria-label="Search options"]').setValue('pending')
    expect(optionLabels()).toEqual(['Cloudflare DNS - Pending'])
    await wrapper.find<HTMLInputElement>('input[aria-label="Search options"]').setValue('target')
    expect(optionLabels()).toEqual(['Cloudflare DNS - Target'])
    await clickButton(wrapper, 'Attach Account')
    await vi.waitFor(() => {
      const updatedProviderGroup = wrapper.find('section[aria-label="Cloudflare DNS"]')
      expect(updatedProviderGroup.text()).toContain('Cloudflare DNS - Source')
      expect(updatedProviderGroup.text()).toContain('Cloudflare DNS - Target')
    })
  })

  it('requires confirmation to detach a Connection and keeps the Account globally reusable', async () => {
    let attached = true
    let detachCalls = 0
    const provider = authProvider('cloudflare-dns', 'Cloudflare DNS', 'api-key', apiKeyMethod())
    const account = authConnection({
      revokedAt: null,
      providerKey: 'cloudflare-dns',
      credentialRef: 'cred_cf_reusable',
      label: 'Cloudflare DNS - Reusable',
    })

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse
      if (url === '/api/v1/auth/accounts') {
        return json({
          project_id: null,
          provider_key: null,
          providers: [provider],
          accounts: [{ ...account, project_ids: attached ? [1] : [] }],
        })
      }
      if (url === '/api/v1/projects/1/connections/accounts') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [provider],
          accounts: attached ? [{ ...account, project_ids: [1] }] : [],
        })
      }
      if (url === '/api/v1/projects/1/connections/accounts/cred_cf_reusable') {
        expect(init?.method).toBe('DELETE')
        detachCalls += 1
        attached = false
        return json({ data: { ...account, project_ids: [] } })
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/projects/:id/connections', component: ConnectionsView },
        { path: '/accounts', component: { template: '<div />' } },
      ],
    })
    await router.push('/projects/1/connections')
    await router.isReady()
    const wrapper = mountConnections(router)

    await vi.waitFor(() => expect(wrapper.text()).toContain('Cloudflare DNS - Reusable'))
    await clickButton(wrapper, 'Detach')
    expect(wrapper.text()).toContain('The Account remains available elsewhere.')
    await clickButton(wrapper, 'Keep attached')
    expect(detachCalls).toBe(0)
    expect(wrapper.text()).toContain('Cloudflare DNS - Reusable')

    await clickButton(wrapper, 'Detach')
    await clickButton(wrapper, 'Detach Account')
    await vi.waitFor(() => expect(detachCalls).toBe(1))
    await vi.waitFor(() => {
      expect(wrapper.text()).not.toContain('Cloudflare DNS - Reusable')
    })

    const globalInventoryCall = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.find(
      ([input]) => String(input) === '/api/v1/auth/accounts',
    )
    expect(globalInventoryCall).toBeDefined()
  })

  it('opens the reusable Account panel and auto-attaches a newly created Account', async () => {
    let created = false
    const writes: unknown[] = []
    const provider = authProvider('firecrawl', 'Firecrawl', 'api-key', apiKeyMethod())
    const account = authConnection({
      revokedAt: null,
      credentialRef: 'cred_new',
      label: 'Firecrawl - New',
    })

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse
      if (init?.body) writes.push(JSON.parse(String(init.body)))
      if (url === '/api/v1/auth/accounts') {
        return json({
          project_id: null,
          provider_key: null,
          providers: [provider],
          accounts: created ? [account] : [],
        })
      }
      if (url === '/api/v1/projects/1/connections/accounts') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [provider],
          accounts: created ? [account] : [],
        })
      }
      if (url === '/api/v1/auth/accounts/firecrawl') {
        created = true
        return json({ data: account }, 201)
      }
      if (url === '/api/v1/auth/accounts/cred_new/test') {
        return json({
          data: {
            credential_ref: 'cred_new',
            provider_key: 'firecrawl',
            ok: true,
            status: 'connected',
            summary: 'Account verified.',
            checked_at: '2026-07-24T00:00:00Z',
            retryable: false,
            next_action: null,
            metadata: {},
          },
        })
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/connections', component: ConnectionsView }],
    })
    await router.push('/projects/1/connections')
    await router.isReady()
    const wrapper = mountConnections(router)

    await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected'))
    await clickButton(wrapper, 'Add connection')
    await clickButton(wrapper, 'Create another Account')
    await vi.waitFor(() =>
      expect(wrapper.find('input[placeholder="Service - Default"]').exists()).toBe(true),
    )
    await wrapper
      .find<HTMLInputElement>('input[placeholder="Service - Default"]')
      .setValue('Firecrawl - New')
    await wrapper.find<HTMLInputElement>('input[placeholder="sk-..."]').setValue('fc-secret')
    await clickButton(wrapper, 'Save and verify')

    await vi.waitFor(() => expect(wrapper.text()).toContain('Firecrawl - New'))
    expect(writes).toContainEqual({
      auth_method_key: 'api_key',
      display_name: 'Firecrawl - New',
      fields: { api_key: 'fc-secret' },
      attach_project_id: 1,
    })
    expect(wrapper.html()).not.toContain('fc-secret')
  })

  it('shows a saved verification failure after loading an attached Account', async () => {
    const provider = authProvider('stripe', 'Stripe', 'api-key', apiKeyMethod())
    const account = {
      ...authConnection({
        revokedAt: null,
        providerKey: 'stripe',
        credentialRef: 'cred_stripe',
        label: 'Stripe - Sandbox',
        lastTestedAt: '2026-09-05T00:00:00Z',
      }),
      last_test: {
        credential_ref: 'cred_stripe',
        provider_key: 'stripe',
        ok: false,
        status: 'connected',
        summary: 'This key cannot access the account verification endpoint.',
        checked_at: '2026-09-05T00:00:00Z',
        retryable: false,
        next_action: 'Use a test key with the required account read permission.',
        metadata: { authorization: 'Bearer never-render-this-canary' },
      },
    }
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse
      if (url === '/api/v1/auth/accounts' || url === '/api/v1/projects/1/connections/accounts') {
        return json({ project_id: 1, provider_key: null, providers: [provider], accounts: [account] })
      }
      return json({})
    }) as typeof fetch
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/connections', component: ConnectionsView }],
    })
    await router.push('/projects/1/connections')
    await router.isReady()

    for (let load = 0; load < 2; load += 1) {
      const wrapper = mountConnections(router)
      await vi.waitFor(() => expect(wrapper.text()).toContain('Stripe - Sandbox'))
      expect(wrapper.text()).toContain('Verification failed')
      expect(wrapper.text()).toContain(account.last_test.summary)
      expect(wrapper.text()).toContain(account.last_test.next_action)
      expect(wrapper.text()).toContain('1 connection needs attention')
      expect(wrapper.text()).not.toContain('service is ready')
      expect(wrapper.text()).not.toContain('never-render-this-canary')
      expect(wrapper.text()).toContain('1 connected')
      wrapper.unmount()
    }
  })

  it('keeps a create-time verification failure visible on the connected service', async () => {
    let created = false
    const provider = authProvider('firecrawl', 'Firecrawl', 'api-key', apiKeyMethod())
    const account = authConnection({
      revokedAt: null,
      credentialRef: 'cred_new',
      label: 'Firecrawl - New',
    })

    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse
      if (url === '/api/v1/auth/accounts') {
        return json({
          project_id: null,
          provider_key: null,
          providers: [provider],
          accounts: created ? [account] : [],
        })
      }
      if (url === '/api/v1/projects/1/connections/accounts') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [provider],
          accounts: created ? [account] : [],
        })
      }
      if (url === '/api/v1/auth/accounts/firecrawl') {
        created = true
        return json({ data: account }, 201)
      }
      if (url === '/api/v1/auth/accounts/cred_new/test') {
        return json({
          data: {
            credential_ref: 'cred_new',
            provider_key: 'firecrawl',
            ok: false,
            status: 'connected',
            summary: 'The API key could not be verified.',
            checked_at: '2026-07-24T00:00:00Z',
            retryable: false,
            next_action: 'Replace the key with one that has the required permissions.',
            metadata: {},
          },
        })
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/connections', component: ConnectionsView }],
    })
    await router.push('/projects/1/connections')
    await router.isReady()
    const wrapper = mountConnections(router)

    await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected'))
    await clickButton(wrapper, 'Add connection')
    await clickButton(wrapper, 'Create another Account')
    await vi.waitFor(() =>
      expect(wrapper.find('input[placeholder="Service - Default"]').exists()).toBe(true),
    )
    await wrapper
      .find<HTMLInputElement>('input[placeholder="Service - Default"]')
      .setValue('Firecrawl - New')
    await wrapper.find<HTMLInputElement>('input[placeholder="sk-..."]').setValue('fc-secret')
    await clickButton(wrapper, 'Save and verify')

    await vi.waitFor(() => {
      expect(wrapper.text()).toContain('Firecrawl - New')
      expect(wrapper.text()).toContain(
        'The API key could not be verified. Replace the key with one that has the required permissions. The Account was saved.',
      )
      expect(wrapper.text()).not.toContain('retry verification')
    })
  })

  it('shows a failed Account attach inside the still-open Account selector', async () => {
    const provider = authProvider('cloudflare-dns', 'Cloudflare DNS', 'api-key', apiKeyMethod())
    const attachedAccount = authConnection({
      revokedAt: null,
      providerKey: 'cloudflare-dns',
      credentialRef: 'cred_cf_source',
      label: 'Cloudflare DNS - Source',
    })
    const availableAccount = {
      ...authConnection({
        revokedAt: null,
        providerKey: 'cloudflare-dns',
        credentialRef: 'cred_cf_target',
        label: 'Cloudflare DNS - Target',
      }),
      project_ids: [],
    }

    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse
      if (url === '/api/v1/auth/accounts') {
        return json({
          project_id: null,
          provider_key: null,
          providers: [provider],
          accounts: [attachedAccount, availableAccount],
        })
      }
      if (url === '/api/v1/projects/1/connections/accounts') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [provider],
          accounts: [attachedAccount],
        })
      }
      if (url === '/api/v1/projects/1/connections/accounts/cred_cf_target') {
        return json({ detail: 'Cloudflare access could not be attached.' }, 503)
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/connections', component: ConnectionsView }],
    })
    await router.push('/projects/1/connections')
    await router.isReady()
    const wrapper = mountConnections(router)

    await vi.waitFor(() => expect(wrapper.text()).toContain('Cloudflare DNS - Source'))
    await clickButton(wrapper, 'Add connection')
    await clickButton(wrapper, 'Attach Account')

    await vi.waitFor(() => {
      expect(wrapper.text()).toContain('Cloudflare access could not be attached.')
      expect(wrapper.text()).toContain('Choose an Account not yet attached to this project.')
    })
  })

  it('does not render an empty project inventory when Connections fail to load', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse
      if (url === '/api/v1/auth/accounts') {
        return json({ project_id: null, provider_key: null, providers: [], accounts: [] })
      }
      if (url === '/api/v1/projects/1/connections/accounts') {
        return json({ detail: 'offline' }, 503)
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/connections', component: ConnectionsView }],
    })
    await router.push('/projects/1/connections')
    await router.isReady()
    const wrapper = mountConnections(router)

    await vi.waitFor(() => expect(wrapper.text()).toContain('Services unavailable'))
    expect(wrapper.text()).not.toContain('No services connected')
    expect(wrapper.text()).toContain('Retry')
  })
})
