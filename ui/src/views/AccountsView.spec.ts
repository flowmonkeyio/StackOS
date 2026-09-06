import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'

import AccountsView from './AccountsView.vue'
import {
  apiKeyMethod,
  authConnection,
  authProvider,
  clickButton,
  json,
} from './connections/connectionsViewTestSupport'

const ORIG_FETCH = globalThis.fetch

describe('AccountsView', () => {
  beforeEach(() => setActivePinia(createPinia()))

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('creates and verifies a reusable Account without rendering its secret', async () => {
    let accountCreated = false
    const writes: Array<{ url: string; body: unknown }> = []
    const provider = authProvider('firecrawl', 'Firecrawl', 'api-key', apiKeyMethod())
    const account = authConnection({
      revokedAt: null,
      credentialRef: 'cred_firecrawl',
      label: 'Firecrawl - Production',
    })

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) writes.push({ url, body: JSON.parse(String(init.body)) })
      if (url === '/api/v1/auth/accounts') {
        return json({
          project_id: null,
          provider_key: null,
          providers: [provider],
          accounts: accountCreated ? [account] : [],
        })
      }
      if (url === '/api/v1/projects?limit=50') {
        return json({ items: [], next_cursor: null, total_estimate: 0 })
      }
      if (url === '/api/v1/auth/accounts/firecrawl') {
        accountCreated = true
        return json({ data: account }, 201)
      }
      if (url === '/api/v1/auth/accounts/cred_firecrawl/test') {
        return json({
          data: {
            credential_ref: 'cred_firecrawl',
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
      routes: [{ path: '/accounts', component: AccountsView }],
    })
    await router.push('/accounts')
    await router.isReady()
    const wrapper = mount(
      { template: '<RouterView />' },
      { global: { plugins: [router], stubs: { teleport: true } } },
    )

    await vi.waitFor(() => expect(wrapper.text()).toContain('No Accounts yet'))
    await clickButton(wrapper, 'Add Account')
    await wrapper
      .find<HTMLInputElement>('input[placeholder="Service - Default"]')
      .setValue('Firecrawl - Production')
    await wrapper.find<HTMLInputElement>('input[placeholder="sk-..."]').setValue('fc-secret')
    await clickButton(wrapper, 'Save and verify')

    await vi.waitFor(() => expect(wrapper.text()).toContain('Firecrawl - Production'))
    expect(writes).toContainEqual({
      url: '/api/v1/auth/accounts/firecrawl',
      body: {
        auth_method_key: 'api_key',
        display_name: 'Firecrawl - Production',
        fields: { api_key: 'fc-secret' },
        attach_project_id: null,
      },
    })
    expect(wrapper.html()).not.toContain('fc-secret')
  })

  it('loads failed verification evidence as plain text and clears it after a successful retest', async () => {
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
        summary: 'Permission denied. <img src=x onerror="alert(1)">',
        checked_at: '2026-09-05T00:00:00Z',
        retryable: false,
        next_action: 'Use a properly scoped test key. [Claim](javascript:alert(1))',
        metadata: { ignored_provider_payload: 'never-render-this-canary' },
      },
    }
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (url === '/api/v1/auth/accounts') {
        return json({
          project_id: null,
          provider_key: null,
          providers: [provider],
          accounts: [account],
        })
      }
      if (url === '/api/v1/projects?limit=50') {
        return json({ items: [], next_cursor: null, total_estimate: 0 })
      }
      if (url === '/api/v1/auth/accounts/cred_stripe/test') {
        expect(init?.method).toBe('POST')
        account.last_test = {
          ...account.last_test,
          ok: true,
          summary: 'Account verified.',
          next_action: '',
          metadata: { ignored_provider_payload: '' },
        }
        return json({ data: account.last_test })
      }
      return json({})
    }) as typeof fetch
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/accounts', component: AccountsView }],
    })
    await router.push('/accounts')
    await router.isReady()
    const wrapper = mount(
      { template: '<RouterView />' },
      { global: { plugins: [router], stubs: { teleport: true } } },
    )

    await vi.waitFor(() => expect(wrapper.text()).toContain('Stripe - Sandbox'))
    expect(wrapper.text()).toContain('Verification failed')
    expect(wrapper.text()).toContain(account.last_test.summary)
    expect(wrapper.text()).toContain(account.last_test.next_action)
    expect(wrapper.find('img[onerror]').exists()).toBe(false)
    expect(wrapper.find('a[href^="javascript:"]').exists()).toBe(false)
    expect(wrapper.html()).not.toContain('never-render-this-canary')
    await clickButton(wrapper, 'Test')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Account verified.'))
    expect(wrapper.text()).not.toContain('Verification failed')
    expect(wrapper.text()).not.toContain('Permission denied.')
  })

  it('groups Accounts by provider and shows safe lifecycle and identity details', async () => {
    const firecrawl = authProvider('firecrawl', 'Firecrawl', 'api-key', apiKeyMethod())
    const slack = authProvider('slack-bot', 'Slack', 'oauth2', [])
    const expiredFirecrawl = {
      ...authConnection({
        revokedAt: null,
        status: 'expired',
        credentialRef: 'cred_firecrawl_expired',
        label: 'Firecrawl - Archive',
        account: { display_name: 'Archive crawler' },
        expiresAt: '2026-07-01T00:00:00Z',
        lastTestedAt: '2026-06-30T00:00:00Z',
      }),
      project_ids: [],
    }
    const slackAccount = authConnection({
      revokedAt: null,
      providerKey: 'slack-bot',
      credentialRef: 'cred_slack_ops',
      authType: 'oauth2',
      authMethodKey: 'oauth2',
      label: 'Slack - Operations',
      account: { display_name: 'Flow Monkey workspace' },
    })

    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url === '/api/v1/auth/accounts') {
        return json({
          project_id: null,
          provider_key: null,
          providers: [slack, firecrawl],
          accounts: [slackAccount, expiredFirecrawl],
        })
      }
      if (url === '/api/v1/projects?limit=50') {
        return json({
          items: [{ id: 1, name: 'Operations' }],
          next_cursor: null,
          total_estimate: 1,
        })
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/accounts', component: AccountsView },
        { path: '/projects/:id/connections', component: { template: '<div />' } },
      ],
    })
    await router.push('/accounts')
    await router.isReady()
    const wrapper = mount(
      { template: '<RouterView />' },
      { global: { plugins: [router], stubs: { teleport: true } } },
    )

    await vi.waitFor(() => expect(wrapper.text()).toContain('Firecrawl - Archive'))
    const groups = wrapper.findAll('section[aria-labelledby^="account-provider-"]')
    expect(groups).toHaveLength(2)
    expect(groups[0].text()).toContain('Firecrawl')
    expect(groups[1].text()).toContain('Slack')
    expect(wrapper.text()).toContain('Archive crawler')
    expect(wrapper.text()).toContain('Flow Monkey workspace')
    expect(wrapper.text()).toContain('cred_firecrawl_expired')
    expect(wrapper.text()).toContain('cred_slack_ops')
    expect(wrapper.text()).toContain('Not attached')
    expect(wrapper.text()).toContain('never tested')
    expect(wrapper.text()).toContain('expires')

    await wrapper
      .find<HTMLInputElement>('input[placeholder="Find an Account, provider, or project…"]')
      .setValue('Flow Monkey workspace')
    expect(wrapper.findAll('section[aria-labelledby^="account-provider-"]')).toHaveLength(1)
    expect(wrapper.text()).toContain('Slack - Operations')
    expect(wrapper.text()).not.toContain('Firecrawl - Archive')
  })

  it('places Slack webhook attention under only the Account and profile that own it', async () => {
    const slack = authProvider('slack-bot', 'Slack', 'oauth2', [])
    const usedAccount = {
      ...authConnection({
        revokedAt: null,
        providerKey: 'slack-bot',
        credentialRef: 'cred_slack_used',
        authType: 'oauth2',
        authMethodKey: 'oauth2',
        label: 'Slack - Operations',
      }),
      project_ids: [1],
    }
    const spareAccount = {
      ...authConnection({
        revokedAt: null,
        providerKey: 'slack-bot',
        credentialRef: 'cred_slack_spare',
        authType: 'oauth2',
        authMethodKey: 'oauth2',
        label: 'Slack - Spare',
      }),
      project_ids: [],
    }
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url === '/api/v1/auth/accounts') {
        return json({
          project_id: null,
          provider_key: null,
          providers: [slack],
          accounts: [usedAccount, spareAccount],
        })
      }
      if (url === '/api/v1/projects?limit=50') {
        return json({
          items: [{ id: 1, name: 'Operations' }],
          next_cursor: null,
          total_estimate: 1,
        })
      }
      if (url === '/api/v1/operations/communicationProfile.accountUsage/call') {
        return json({
          project_id: null,
          uses: [
            {
              project_id: 1,
              profile_ref: 'communication-profile:operator',
              profile_key: 'operator',
              profile_display_name: 'Operator Slack',
              provider_key: 'slack-bot',
              credential_ref: 'cred_slack_used',
              profile_enabled: true,
              ingress_enabled: true,
              owns_provider_ingress: true,
              binding_state: 'ready',
              repair_message: '',
              attention_required: true,
              attention_message:
                'Update the Slack Events API and Interactivity URLs for this profile.',
              ingress_url: 'https://example.test/slack',
            },
          ],
        })
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/accounts', component: AccountsView },
        { path: '/projects/:id/connections', component: { template: '<div />' } },
      ],
    })
    await router.push('/accounts')
    await router.isReady()
    const wrapper = mount(
      { template: '<RouterView />' },
      { global: { plugins: [router], stubs: { teleport: true } } },
    )

    await vi.waitFor(() => expect(wrapper.text()).toContain('Operator Slack needs a Slack webhook'))
    const accountRows = wrapper.findAll('li.px-4')
    const usedRow = accountRows.find((row) => row.text().includes('Slack - Operations'))
    const spareRow = accountRows.find((row) => row.text().includes('Slack - Spare'))
    expect(usedRow?.text()).toContain('Operator Slack needs a Slack webhook update')
    expect(usedRow?.text()).toContain('Communication profiles')
    expect(spareRow?.text()).not.toContain('webhook update')
  })

  it('announces and focuses a missing Account name', async () => {
    const provider = authProvider('firecrawl', 'Firecrawl', 'api-key', apiKeyMethod())
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url === '/api/v1/auth/accounts') {
        return json({
          project_id: null,
          provider_key: null,
          providers: [provider],
          accounts: [],
        })
      }
      if (url === '/api/v1/projects?limit=50') {
        return json({ items: [], next_cursor: null, total_estimate: 0 })
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/accounts', component: AccountsView }],
    })
    await router.push('/accounts')
    await router.isReady()
    const wrapper = mount(
      { template: '<RouterView />' },
      {
        attachTo: document.body,
        global: { plugins: [router], stubs: { teleport: true } },
      },
    )

    await vi.waitFor(() => expect(wrapper.text()).toContain('No Accounts yet'))
    await clickButton(wrapper, 'Add Account')
    const accountName = wrapper.find<HTMLInputElement>('#account-display-name')
    await accountName.setValue('')
    await wrapper.find<HTMLInputElement>('input[placeholder="sk-..."]').setValue('fc-secret')
    await clickButton(wrapper, 'Save and verify')

    await vi.waitFor(() => expect(wrapper.text()).toContain('Account name is required.'))
    expect(accountName.attributes('aria-invalid')).toBe('true')
    expect(document.activeElement).toBe(accountName.element)
    wrapper.unmount()
  })

  it('does not render an empty inventory when Accounts fail to load', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url === '/api/v1/auth/accounts') return json({ detail: 'offline' }, 503)
      if (url === '/api/v1/projects?limit=50') {
        return json({ items: [], next_cursor: null, total_estimate: 0 })
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/accounts', component: AccountsView }],
    })
    await router.push('/accounts')
    await router.isReady()
    const wrapper = mount(
      { template: '<RouterView />' },
      { global: { plugins: [router], stubs: { teleport: true } } },
    )

    await vi.waitFor(() => expect(wrapper.text()).toContain('Accounts unavailable'))
    expect(wrapper.text()).not.toContain('No Accounts yet')
    expect(wrapper.text()).toContain('Retry')
  })
})
