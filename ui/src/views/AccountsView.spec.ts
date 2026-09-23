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

const telegramUserMethod = [
  {
    key: 'tdlib-user-session',
    label: 'User account',
    auth_type: 'tdlib-user-session',
    description: '',
    interactive: false,
    payload_format: 'json',
    payload_field: null,
    fields: [],
    config: { native_authorization: true, account_kind: 'user' },
  },
]

const telegramBotMethod = [
  {
    key: 'tdlib-bot-token',
    label: 'Bot',
    auth_type: 'tdlib-bot-token',
    description: '',
    interactive: false,
    payload_format: 'json',
    payload_field: null,
    fields: [
      { key: 'bot_token', label: 'Bot Token', type: 'secret', secret: true, required: true },
    ],
    config: { native_authorization: true, account_kind: 'bot' },
  },
]

describe('AccountsView', () => {
  beforeEach(() => setActivePinia(createPinia()))

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('verifies a Telegram bot once during creation and shows its saved offline authorization', async () => {
    const provider = authProvider('telegram', 'Telegram', 'tdlib', telegramBotMethod)
    const account = authConnection({
      revokedAt: null,
      status: 'disconnected',
      setupRequired: false,
      providerKey: 'telegram',
      credentialRef: 'cred_telegram_bot',
      authType: 'tdlib-bot-token',
      authMethodKey: 'tdlib-bot-token',
      label: 'Telegram - Default',
      account: { provider_account_id: '456', display_name: '@example_bot' },
    })
    const writes: string[] = []
    let createdFields: Record<string, unknown> | null = null
    let created = false
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.method === 'POST') writes.push(url)
      if (url === '/api/v1/operations/account.application.status/call') {
        return json({ configured: false })
      }
      if (url === '/api/v1/auth/accounts') {
        return json({ project_id: null, provider_key: null, providers: [provider], accounts: created ? [account] : [] })
      }
      if (url === '/api/v1/projects?limit=50') {
        return json({ items: [], next_cursor: null, total_estimate: 0 })
      }
      if (url === '/api/v1/auth/accounts/telegram') {
        createdFields = (JSON.parse(String(init?.body)) as { fields: Record<string, unknown> }).fields
        created = true
        return json({ data: account }, 201)
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
    expect(wrapper.text()).toContain('Verify bot')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Telegram application · one-time setup'))
    await wrapper.find<HTMLInputElement>('#connection-field-api_id').setValue('12345')
    await wrapper.find<HTMLInputElement>('#connection-field-api_hash').setValue('api-hash-secret')
    await wrapper.find<HTMLInputElement>('#connection-field-bot_token').setValue('123456:bot-secret')
    await clickButton(wrapper, 'Save and verify')

    await vi.waitFor(() => expect(wrapper.text()).toContain('Telegram bot authorization saved'))
    expect(wrapper.text()).toContain('Disconnected')
    expect(wrapper.text()).toContain('Authorization saved')
    expect(wrapper.find('ul[aria-label="Telegram Accounts"] > li').classes()).not.toContain('bg-warning-subtle')
    expect(writes.filter((url) => url.startsWith('/api/v1/auth/'))).toEqual([
      '/api/v1/auth/accounts/telegram',
    ])
    expect(createdFields).toEqual({ api_id: 12345, api_hash: 'api-hash-secret', bot_token: '123456:bot-secret' })
    expect(wrapper.html()).not.toContain('123456:bot-secret')
  })

  it('offers local verification retry when a bot Account was saved but native authorization failed', async () => {
    const provider = authProvider('telegram', 'Telegram', 'tdlib', telegramBotMethod)
    const account = authConnection({
      revokedAt: null,
      status: 'pending',
      setupRequired: true,
      providerKey: 'telegram',
      credentialRef: 'cred_telegram_bot',
      authType: 'tdlib-bot-token',
      authMethodKey: 'tdlib-bot-token',
      label: 'Telegram - Default',
    })
    const writes: string[] = []
    let createdFields: Record<string, unknown> | null = null
    let created = false
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.method === 'POST') writes.push(url)
      if (url === '/api/v1/operations/account.application.status/call') {
        return json({ configured: true })
      }
      if (url === '/api/v1/auth/accounts') {
        return json({ project_id: null, provider_key: null, providers: [provider], accounts: created ? [account] : [] })
      }
      if (url === '/api/v1/projects?limit=50') {
        return json({ items: [], next_cursor: null, total_estimate: 0 })
      }
      if (url === '/api/v1/auth/accounts/telegram') {
        createdFields = (JSON.parse(String(init?.body)) as { fields: Record<string, unknown> }).fields
        created = true
        return json({ data: account }, 201)
      }
      if (url === '/api/v1/auth/accounts/cred_telegram_bot/authorization') {
        return json({
          credential_ref: 'cred_telegram_bot',
          provider_key: 'telegram',
          status: 'pending',
          generation: null,
          challenge: null,
          repair_hint: 'Telegram could not verify this bot token. Check it and retry.',
        })
      }
      if (url === '/api/v1/auth/accounts/telegram/start') {
        account.status = 'disconnected'
        account.setup_required = false
        account.account = { provider_account_id: '456', display_name: '@example_bot' }
        return json({ data: {
          credential_ref: 'cred_telegram_bot',
          provider_key: 'telegram',
          status: 'disconnected',
          challenge: null,
        } })
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
    await vi.waitFor(() => expect(wrapper.text()).not.toContain('Checking saved Telegram application'))
    expect(wrapper.find('#connection-field-api_id').exists()).toBe(false)
    expect(wrapper.find('#connection-field-api_hash').exists()).toBe(false)
    await wrapper.find<HTMLInputElement>('#connection-field-bot_token').setValue('123456:bot-secret')
    await clickButton(wrapper, 'Save and verify')

    await vi.waitFor(() => expect(wrapper.text()).toContain('Retry bot verification'))
    expect(wrapper.text()).toContain('Telegram could not verify this bot token.')
    expect(wrapper.text()).toContain('Review verification')
    expect(wrapper.find('ul[aria-label="Telegram Accounts"] > li').classes()).toContain('bg-warning-subtle')
    await clickButton(wrapper, 'Retry bot verification')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Telegram bot authorization saved'))
    expect(wrapper.find('ul[aria-label="Telegram Accounts"] > li').classes()).not.toContain('bg-warning-subtle')
    expect(writes.filter((url) => url.startsWith('/api/v1/auth/'))).toEqual([
      '/api/v1/auth/accounts/telegram',
      '/api/v1/auth/accounts/telegram/start',
    ])
    expect(createdFields).toEqual({ bot_token: '123456:bot-secret' })
    expect(wrapper.html()).not.toContain('123456:bot-secret')
  })

  it('blocks Telegram creation when application status is unavailable and recovers on retry', async () => {
    const provider = authProvider('telegram', 'Telegram', 'tdlib', telegramBotMethod)
    const account = authConnection({
      revokedAt: null,
      status: 'disconnected',
      providerKey: 'telegram',
      credentialRef: 'cred_new_bot',
      authType: 'tdlib-bot-token',
      authMethodKey: 'tdlib-bot-token',
    })
    let statusAvailable = false
    let createdFields: Record<string, unknown> | null = null
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (url === '/api/v1/operations/account.application.status/call') {
        return statusAvailable
          ? json({ configured: true })
          : json({ detail: 'Telegram application status is unavailable.' }, 503)
      }
      if (url === '/api/v1/auth/accounts') {
        return json({ project_id: null, provider_key: null, providers: [provider], accounts: createdFields ? [account] : [] })
      }
      if (url === '/api/v1/projects?limit=50') {
        return json({ items: [], next_cursor: null, total_estimate: 0 })
      }
      if (url === '/api/v1/auth/accounts/telegram') {
        createdFields = (JSON.parse(String(init?.body)) as { fields: Record<string, unknown> }).fields
        return json({ data: account }, 201)
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
    await vi.waitFor(() => expect(wrapper.text()).toContain('Telegram application status is unavailable.'))
    expect(wrapper.find('#connection-field-api_id').exists()).toBe(false)
    expect(wrapper.find('button[form="account-credential-form"]').attributes('disabled')).toBeDefined()
    expect(createdFields).toBeNull()

    statusAvailable = true
    await clickButton(wrapper, 'Retry')
    await vi.waitFor(() =>
      expect(wrapper.find('button[form="account-credential-form"]').attributes('disabled')).toBeUndefined(),
    )
    await wrapper.find<HTMLInputElement>('#connection-field-bot_token').setValue('123456:bot-secret')
    await clickButton(wrapper, 'Save and verify')
    await vi.waitFor(() => expect(createdFields).toEqual({ bot_token: '123456:bot-secret' }))
  })

    it('returns a saved Telegram bot Account to verification after its token changes', async () => {
      const provider = authProvider('telegram', 'Telegram', 'tdlib', telegramBotMethod)
      const account = authConnection({
        revokedAt: null,
        status: 'disconnected',
        setupRequired: false,
        providerKey: 'telegram',
        credentialRef: 'cred_telegram_edit',
        authType: 'tdlib-bot-token',
        authMethodKey: 'tdlib-bot-token',
        label: 'Telegram - Default',
        account: { provider_account_id: '123456', display_name: '@operator' },
      })
      const writes: Array<{ url: string; body: Record<string, unknown> }> = []
      let failNextUpdate = true
      globalThis.fetch = vi.fn(async (input, init) => {
        const url = String(input)
        if (init?.method === 'PATCH') {
          writes.push({ url, body: JSON.parse(String(init.body)) as Record<string, unknown> })
          if (failNextUpdate) {
            failNextUpdate = false
            return json({ detail: 'Telegram settings could not be saved.' }, 400)
          }
          account.status = 'pending'
          account.setup_required = true
          account.account = null
          return json({ data: account })
        }
        if (url === '/api/v1/auth/accounts') {
          return json({ project_id: null, provider_key: null, providers: [provider], accounts: [account] })
        }
        if (url === '/api/v1/projects?limit=50') {
          return json({ items: [], next_cursor: null, total_estimate: 0 })
        }
        if (url === '/api/v1/auth/accounts/cred_telegram_edit') {
          return json({
            account,
            values: {},
            secret_present: { bot_token: true },
          })
        }
        if (url === '/api/v1/auth/accounts/cred_telegram_edit/authorization') {
          return json({
            credential_ref: 'cred_telegram_edit',
            provider_key: 'telegram',
            status: account.status,
            generation: null,
            challenge: null,
            repair_hint: account.setup_required ? 'Sign in again after changing identity settings.' : null,
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

      await vi.waitFor(() => expect(wrapper.text()).toContain('Telegram - Default'))
      await clickButton(wrapper, 'Edit')
      await vi.waitFor(() => expect(wrapper.text()).toContain('Telegram bot authorization saved'))
      await clickButton(wrapper, 'Edit details')
      expect(wrapper.find('#connection-field-api_id').exists()).toBe(false)
      expect(wrapper.find('#connection-field-api_hash').exists()).toBe(false)
      await wrapper.find<HTMLInputElement>('#connection-field-bot_token').setValue('123456:new-bot-token')
      await clickButton(wrapper, 'Save changes')
      await vi.waitFor(() => expect(wrapper.text()).toContain('Telegram settings could not be saved.'))
      expect(wrapper.find<HTMLInputElement>('#connection-field-bot_token').exists()).toBe(true)
      await clickButton(wrapper, 'Save changes')

      await vi.waitFor(() => expect(wrapper.text()).toContain('Verify Telegram bot'))
      expect(wrapper.text()).not.toContain('Telegram bot authorization saved')
      expect(wrapper.text()).toContain('Sign in again after changing identity settings.')
      expect(wrapper.find('ul[aria-label="Telegram Accounts"] > li').classes()).toContain('bg-warning-subtle')
      expect(writes).toHaveLength(2)
      expect(writes[0]?.url).toBe('/api/v1/auth/accounts/cred_telegram_edit')
      expect(writes[0]?.body.fields).toMatchObject({ bot_token: '123456:new-bot-token' })
      expect(wrapper.html()).not.toContain('123456:new-bot-token')
    })

  it('keeps a Telegram bot authorization ready after a proxy-only edit and clears the old Test result', async () => {
    const proxyBotMethod = [{
      ...telegramBotMethod[0],
      fields: [
        ...telegramBotMethod[0]!.fields,
        { key: 'proxy_enabled', label: 'Use a proxy', type: 'boolean', required: false },
        { key: 'proxy_type', label: 'Proxy type', type: 'select', required: false, options: [{ value: 'socks5', label: 'SOCKS5' }] },
        { key: 'proxy_host', label: 'Proxy host', type: 'text', required: false },
        { key: 'proxy_port', label: 'Proxy port', type: 'number', required: false },
      ],
    }]
    const provider = authProvider('telegram', 'Telegram', 'tdlib', proxyBotMethod)
    const priorTest = {
      credential_ref: 'cred_telegram_proxy',
      provider_key: 'telegram',
      ok: false,
      status: 'disconnected',
      summary: 'Old Test feedback',
      checked_at: '2026-09-22T00:00:00Z',
      retryable: false,
      next_action: 'Old Test repair',
      metadata: {},
    }
    const account = {
      ...authConnection({
        revokedAt: null,
        status: 'disconnected',
        setupRequired: false,
        providerKey: 'telegram',
        credentialRef: 'cred_telegram_proxy',
        authType: 'tdlib-bot-token',
        authMethodKey: 'tdlib-bot-token',
        label: 'Telegram - Default',
        account: { provider_account_id: '456', display_name: '@example_bot' },
      }),
      last_test: priorTest as typeof priorTest | null,
    }
    const writes: Array<{ url: string; body: Record<string, unknown> }> = []
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.method === 'PATCH') {
        writes.push({ url, body: JSON.parse(String(init.body)) as Record<string, unknown> })
        account.last_test = null
        return json({ data: account })
      }
      if (url === '/api/v1/auth/accounts') {
        return json({ project_id: null, provider_key: null, providers: [provider], accounts: [account] })
      }
      if (url === '/api/v1/projects?limit=50') {
        return json({ items: [], next_cursor: null, total_estimate: 0 })
      }
      if (url === '/api/v1/auth/accounts/cred_telegram_proxy') {
        return json({
          account,
          values: {
            api_id: 12345,
            proxy_enabled: true,
            proxy_type: 'socks5',
            proxy_host: 'old.proxy.example',
            proxy_port: 1080,
          },
          secret_present: { api_hash: true, bot_token: true },
        })
      }
      if (url === '/api/v1/auth/accounts/cred_telegram_proxy/authorization') {
        return json({
          credential_ref: 'cred_telegram_proxy',
          provider_key: 'telegram',
          status: 'disconnected',
          generation: null,
          challenge: null,
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

    await vi.waitFor(() => expect(wrapper.text()).toContain('Old Test feedback'))
    await clickButton(wrapper, 'Edit')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Telegram bot authorization saved'))
    await clickButton(wrapper, 'Edit details')
    await wrapper.find<HTMLInputElement>('#connection-field-proxy_host').setValue('new.proxy.example')
    await clickButton(wrapper, 'Save changes')

    await vi.waitFor(() => expect(wrapper.text()).toContain('Telegram bot authorization saved'))
    expect(wrapper.text()).not.toContain('Old Test feedback')
    expect(wrapper.text()).not.toContain('Old Test repair')
    expect(wrapper.text()).toContain('Authorization saved')
    expect(wrapper.find('ul[aria-label="Telegram Accounts"] > li').classes()).not.toContain('bg-warning-subtle')
    expect(writes).toHaveLength(1)
    expect(writes[0]?.body.fields).toMatchObject({ proxy_host: 'new.proxy.example' })
    expect(writes[0]?.body.fields).not.toHaveProperty('bot_token')
  })

  it('does not show the previous Telegram Account authorization when another Account status request fails', async () => {
    const provider = authProvider('telegram', 'Telegram', 'tdlib', telegramBotMethod)
    const first = authConnection({
      revokedAt: null,
      status: 'disconnected',
      setupRequired: false,
      providerKey: 'telegram',
      credentialRef: 'cred_first_bot',
      authType: 'tdlib-bot-token',
      authMethodKey: 'tdlib-bot-token',
      label: 'First Bot',
      account: { provider_account_id: '111', display_name: '@first_bot' },
    })
    const second = authConnection({
      revokedAt: null,
      status: 'pending',
      setupRequired: true,
      providerKey: 'telegram',
      credentialRef: 'cred_second_bot',
      authType: 'tdlib-bot-token',
      authMethodKey: 'tdlib-bot-token',
      label: 'Second Bot',
    })
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url === '/api/v1/auth/accounts') {
        return json({ project_id: null, provider_key: null, providers: [provider], accounts: [first, second] })
      }
      if (url === '/api/v1/projects?limit=50') {
        return json({ items: [], next_cursor: null, total_estimate: 0 })
      }
      if (url === '/api/v1/auth/accounts/cred_first_bot' || url === '/api/v1/auth/accounts/cred_second_bot') {
        return json({
          account: url.endsWith('cred_first_bot') ? first : second,
          values: { api_id: 12345 },
          secret_present: { api_hash: true, bot_token: true },
        })
      }
      if (url === '/api/v1/auth/accounts/cred_first_bot/authorization') {
        return json({
          credential_ref: 'cred_first_bot',
          provider_key: 'telegram',
          status: 'disconnected',
          generation: null,
          challenge: null,
        })
      }
      if (url === '/api/v1/auth/accounts/cred_second_bot/authorization') {
        return json({ detail: 'Authorization status is temporarily unavailable.' }, 503)
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

    await vi.waitFor(() => expect(wrapper.text()).toContain('Second Bot'))
    await clickButton(wrapper, 'Edit')
    await vi.waitFor(() => expect(wrapper.get('section[aria-label="Native authorization"] h3').text())
      .toBe('Telegram bot authorization saved'))
    await clickButton(wrapper, 'Done')
    await clickButton(wrapper, 'Review verification')
    await vi.waitFor(() => expect(wrapper.get('section[aria-label="Native authorization"] h3').text())
      .toBe('Verify Telegram bot'))
    expect(wrapper.text()).toContain('Authorization status is temporarily unavailable.')
  })

  it('saves one Telegram user Account, follows TDLib challenges, and resumes saved sign-in without connecting', async () => {
    const provider = authProvider('telegram', 'Telegram', 'tdlib', telegramUserMethod)
    const account = {
      ...authConnection({
        revokedAt: null,
        status: 'pending',
        providerKey: 'telegram',
        credentialRef: 'cred_telegram_user',
        authType: 'tdlib-user-session',
        authMethodKey: 'tdlib-user-session',
        label: 'Telegram - Default',
      }),
      account: null as Record<string, unknown> | null,
    }
    const writes: Array<{ url: string; body: Record<string, unknown> }> = []
    let created = false
    let authorization: Record<string, unknown> = {
      credential_ref: 'cred_telegram_user',
      provider_key: 'telegram',
      status: 'pending',
      generation: null,
      challenge: null,
    }
    const challenge = (kind: string, generation: number, field: string) => ({
      credential_ref: 'cred_telegram_user',
      provider_key: 'telegram',
      status: 'challenge',
      generation,
      challenge: { generation, kind, fields: [field], metadata: {} },
    })

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null
      if (body) writes.push({ url, body })
      if (url === '/api/v1/operations/account.application.status/call') {
        return json({ configured: false })
      }
      if (url === '/api/v1/auth/accounts') {
        return json({
          project_id: null,
          provider_key: null,
          providers: [provider],
          accounts: created ? [account] : [],
        })
      }
      if (url === '/api/v1/projects?limit=50') {
        return json({ items: [], next_cursor: null, total_estimate: 0 })
      }
      if (url === '/api/v1/auth/accounts/telegram') {
        created = true
        return json({ data: account }, 201)
      }
      if (url === '/api/v1/auth/accounts/telegram/start') {
        authorization = challenge('phone_number', 1, 'phone_number')
        return json({ data: authorization })
      }
      if (url === '/api/v1/auth/accounts/cred_telegram_user/authorization') {
        if (init?.method === 'POST') {
          if (authorization.generation === 1) authorization = challenge('code', 2, 'code')
          else if (authorization.generation === 2) authorization = challenge('password', 3, 'password')
          else {
            account.status = 'disconnected'
            account.account = { provider_account_id: '123456', display_name: '@operator' }
            authorization = {
              credential_ref: 'cred_telegram_user',
              provider_key: 'telegram',
              status: 'disconnected',
              generation: null,
              challenge: null,
            }
          }
          return json({ data: authorization })
        }
        return json(authorization)
      }
      if (url === '/api/v1/auth/accounts/cred_telegram_user') {
        return json({ account, values: {}, secret_present: {} })
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
    expect(wrapper.text()).toContain('Account details')
    expect(wrapper.text()).not.toContain('Enter your phone number')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Telegram application · one-time setup'))
    await wrapper.find<HTMLInputElement>('#connection-field-api_id').setValue('12345')
    await wrapper.find<HTMLInputElement>('#connection-field-api_hash').setValue('api-hash-secret')
    await clickButton(wrapper, 'Save and continue')

    await vi.waitFor(() => expect(wrapper.text()).toContain('Enter your phone number'))
    expect(wrapper.findAll('button').map((button) => button.text())).toContain('Sign in')
    expect(writes.filter(({ url }) => url === '/api/v1/auth/accounts/telegram')).toEqual([
      {
        url: '/api/v1/auth/accounts/telegram',
        body: {
          auth_method_key: 'tdlib-user-session',
          display_name: 'Telegram - Default',
          fields: { api_id: 12345, api_hash: 'api-hash-secret' },
          attach_project_id: null,
        },
      },
    ])
    expect(writes.find(({ url }) => url === '/api/v1/auth/accounts/telegram/start')?.body)
      .toMatchObject({
        auth_method_key: 'tdlib-user-session',
        credential_ref: 'cred_telegram_user',
        authorization_mode: 'phone',
      })
    expect(wrapper.find('#account-credential-form').exists()).toBe(false)

    await wrapper.find<HTMLInputElement>('input[aria-label="Phone number"]').setValue('+15551234567')
    await wrapper.find('section[aria-label="Native authorization"] form').trigger('submit')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Enter the Telegram code'))
    await wrapper.find<HTMLInputElement>('input[aria-label="Telegram code"]').setValue('12345')
    await wrapper.find('section[aria-label="Native authorization"] form').trigger('submit')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Enter your two-step verification password'))
    await wrapper.find<HTMLInputElement>('input[aria-label="Password"]').setValue('two-step-secret')
    await wrapper.find('section[aria-label="Native authorization"] form').trigger('submit')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Telegram sign-in saved'))
    expect(writes.filter(({ url }) => url.endsWith('/authorization')).map(({ body }) => body)).toEqual([
      { generation: 1, answer: { phone_number: '+15551234567' } },
      { generation: 2, answer: { code: '12345' } },
      { generation: 3, answer: { password: 'two-step-secret' } },
    ])
    expect(writes.some(({ url }) => url.endsWith('/session/connect'))).toBe(false)
    expect(wrapper.html()).not.toContain('two-step-secret')

    await clickButton(wrapper, 'Done')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Sign-in saved'))
    expect(wrapper.find('ul[aria-label="Telegram Accounts"] > li').classes()).not.toContain(
      'bg-warning-subtle',
    )
    expect(wrapper.text()).not.toContain('Account saved. Start local authorization')
    expect(wrapper.findAll('button').some((button) => button.text() === 'Authorize')).toBe(false)
    expect(wrapper.findAll('button').some((button) => button.text() === 'Sign in')).toBe(false)
    await clickButton(wrapper, 'Manage sign-in')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Telegram sign-in saved'))
    expect(writes.filter(({ url }) => url === '/api/v1/auth/accounts/telegram')).toHaveLength(1)
    expect(writes.filter(({ url }) => url === '/api/v1/auth/accounts/telegram/start')).toHaveLength(1)
    expect(writes.some(({ url }) => url.endsWith('/session/connect'))).toBe(false)
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

  it('shows a saved Telegram sign-in while offline and only one persisted Test failure', async () => {
    const provider = authProvider('telegram', 'Telegram', 'tdlib', telegramUserMethod)
    const account = {
      ...authConnection({
        revokedAt: null,
        status: 'disconnected',
        providerKey: 'telegram',
        credentialRef: 'cred_telegram_user',
        authType: 'tdlib-user-session',
        authMethodKey: 'tdlib-user-session',
        label: 'Telegram - Default',
        account: { provider_account_id: '123456', display_name: '@operator' },
      }),
      last_test: {
        credential_ref: 'cred_telegram_user',
        provider_key: 'telegram',
        ok: false,
        status: 'pending',
        summary: 'Telegram Account authorization is incomplete.',
        checked_at: '2026-09-22T00:00:00Z',
        retryable: false,
        next_action: 'Start or resume Telegram authorization in local Accounts.',
        metadata: {},
      },
    }
    let testCount = 0
    globalThis.fetch = vi.fn(async (input) => {
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
      if (url === '/api/v1/auth/accounts/cred_telegram_user/test') {
        testCount += 1
        if (testCount === 2) {
          account.last_test = {
            ...account.last_test,
            ok: true,
            status: 'ok',
            summary: 'Telegram saved session is authorized and responsive.',
            next_action: '',
          }
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

    await vi.waitFor(() => expect(wrapper.text()).toContain('Sign-in saved'))
    expect(wrapper.text()).toContain('Disconnected')
    expect(wrapper.findAll('button').map((button) => button.text())).toContain('Manage sign-in')
    expect(wrapper.findAll('button').map((button) => button.text())).not.toContain('Authorize')
    await clickButton(wrapper, 'Test')
    await vi.waitFor(() => expect(testCount).toBe(1))
    await vi.waitFor(() =>
      expect(
        wrapper.findAll('button').find((button) => button.text() === 'Test')?.attributes('disabled'),
      ).toBeUndefined(),
    )
    expect(wrapper.text().split('Telegram Account authorization is incomplete.').length - 1).toBe(1)
    expect(wrapper.text()).toContain('Verification failed')

    await clickButton(wrapper, 'Test')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Telegram saved session is authorized and responsive.'))
    expect(wrapper.text()).not.toContain('Verification failed')
    expect(wrapper.text()).not.toContain('Telegram Account authorization is incomplete.')
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
