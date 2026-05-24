import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import ConnectionsView from './ConnectionsView.vue'

const ORIG_FETCH = globalThis.fetch

describe('ConnectionsView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('stores, tests, and revokes provider credentials without rendering secrets', async () => {
    let connected = false
    let revoked = false
    const postedBodies: unknown[] = []

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) postedBodies.push(JSON.parse(String(init.body)))
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse

      if (url === '/api/v1/auth/providers') {
        return json([
          authProvider('firecrawl', 'Firecrawl', 'api-key', apiKeyMethod('fc-...')),
          authProvider('local-files', 'Local Files', 'local', [
            {
              key: 'local',
              label: 'Local daemon',
              auth_type: 'local',
              description: '',
              interactive: false,
              payload_format: 'none',
              payload_field: null,
              fields: [],
              config: null,
            },
          ]),
        ])
      }
      if (url === '/api/v1/projects/1/auth/status') {
        const connections = connected
          ? [authConnection({ revokedAt: revoked ? '2026-05-22T00:02:00Z' : null })]
          : []
        return json({
          project_id: 1,
          provider_key: null,
          providers: [],
          connections,
        })
      }
      if (url === '/api/v1/projects/1/auth/firecrawl/credentials') {
        connected = true
        return json({ data: authConnection({ revokedAt: null }) }, 201)
      }
      if (url === '/api/v1/projects/1/auth/test') {
        return json({
          data: {
            credential_ref: 'cred_firecrawl',
            provider_key: 'firecrawl',
            ok: true,
            status: 'ok',
            summary: 'Firecrawl credentials are reachable',
            checked_at: '2026-05-22T00:01:00Z',
            retryable: false,
            next_action: null,
            metadata: {},
          },
        })
      }
      if (url === '/api/v1/projects/1/auth/revoke') {
        revoked = true
        return json({
          data: {
            credential_ref: 'cred_firecrawl',
            provider_key: 'firecrawl',
            project_id: 1,
            revoked_at: '2026-05-22T00:02:00Z',
            status: 'revoked',
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

    const wrapper = mount(ConnectionsView, { global: { plugins: [router] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected.'))
    await clickButton(wrapper, 'Add connection')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Firecrawl'))

    expect(wrapper.find('[aria-label="Reveal value"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="Copy value"]').exists()).toBe(false)

    const secretInput = wrapper.find<HTMLInputElement>('input[placeholder="fc-..."]')
    await secretInput.setValue('fc-secret')
    await wrapper.find('input[placeholder="Primary account"]').setValue('Primary')
    await clickButton(wrapper, 'Save connection')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Primary Firecrawl'))
    await flushPromises()

    expect(wrapper.text()).not.toContain('fc-secret')
    expect(wrapper.html()).not.toContain('fc-secret')
    expect(postedBodies).toContainEqual({
      auth_method_key: 'api_key',
      profile_key: 'default',
      label: 'Primary',
      fields: { api_key: 'fc-secret' },
    })

    await clickButton(wrapper, 'Test')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Firecrawl credentials are reachable'))
    expect(postedBodies).toContainEqual({ credential_ref: 'cred_firecrawl' })

    await clickButton(wrapper, 'Revoke')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Revoked cred_firecrawl.'))
    expect(JSON.stringify(postedBodies.filter((body) => !hasCredentialFields(body)))).not.toContain(
      'fc-secret',
    )
  })

  it('stores safe auth method fields with the credential payload', async () => {
    const postedBodies: unknown[] = []
    let connected = false

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) postedBodies.push(JSON.parse(String(init.body)))
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse

      if (url === '/api/v1/auth/providers') {
        return json([
          authProvider('wordpress', 'WordPress', 'application-password', [
            {
              key: 'application_password',
              label: 'Application password',
              auth_type: 'application-password',
              description: '',
              interactive: false,
              payload_format: 'json',
              payload_field: null,
              config: null,
              fields: [
                {
                  key: 'username',
                  label: 'Username',
                  type: 'secret',
                  secret: true,
                  required: true,
                  placeholder: 'editor',
                },
                {
                  key: 'application_password',
                  label: 'Application Password',
                  type: 'secret',
                  secret: true,
                  required: true,
                  placeholder: 'xxxx xxxx',
                },
                {
                  key: 'wp_url',
                  label: 'Site URL',
                  type: 'url',
                  secret: false,
                  required: true,
                  placeholder: 'https://example.com',
                },
              ],
            },
          ]),
        ])
      }
      if (url === '/api/v1/projects/1/auth/status') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [],
          connections: connected
            ? [
                {
                  ...authConnection({ revokedAt: null }),
                  credential_ref: 'cred_wordpress',
                  provider_key: 'wordpress',
                  auth_type: 'application-password',
                  auth_method_key: 'application_password',
                  label: 'Editorial',
                },
              ]
            : [],
        })
      }
      if (url === '/api/v1/projects/1/auth/wordpress/credentials') {
        connected = true
        return json(
          {
            data: {
              ...authConnection({ revokedAt: null }),
              credential_ref: 'cred_wordpress',
              provider_key: 'wordpress',
            },
          },
          201,
        )
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/connections', component: ConnectionsView }],
    })
    await router.push('/projects/1/connections')
    await router.isReady()

    const wrapper = mount(ConnectionsView, { global: { plugins: [router] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected.'))
    await clickButton(wrapper, 'Add connection')
    await vi.waitFor(() => expect(wrapper.text()).toContain('WordPress'))

    await wrapper.find<HTMLInputElement>('input[placeholder="editor"]').setValue('editor')
    await wrapper.find<HTMLInputElement>('input[placeholder="xxxx xxxx"]').setValue('app pass')
    await wrapper.find('input[placeholder="Primary account"]').setValue('Editorial')
    await wrapper.find('input[placeholder="https://example.com"]').setValue('https://wp.example')
    await clickButton(wrapper, 'Save connection')

    await vi.waitFor(() => expect(wrapper.text()).toContain('cred_wordpress'))
    expect(postedBodies).toContainEqual({
      auth_method_key: 'application_password',
      profile_key: 'default',
      label: 'Editorial',
      fields: {
        username: 'editor',
        application_password: 'app pass',
        wp_url: 'https://wp.example',
      },
    })
    expect(wrapper.text()).not.toContain('app pass')
  })

  it('creates Telegram bot profiles without posting bot tokens to the profile operation', async () => {
    let connected = false
    let telegramTested = false
    const postedBodies: unknown[] = []
    const botProfiles: unknown[] = []

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) postedBodies.push(JSON.parse(String(init.body)))
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse

      if (url === '/api/v1/auth/providers') {
        return json([
          authProvider('telegram-bot', 'Telegram Bot', 'bot-token', telegramBotMethod()),
        ])
      }
      if (url === '/api/v1/projects/1/auth/status') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [],
          connections: connected
            ? [
                authConnection({
                  revokedAt: null,
                  providerKey: 'telegram-bot',
                  credentialRef: 'cred_telegram_unidentified',
                  authType: 'bot-token',
                  authMethodKey: 'bot-token',
                  profileKey: 'default',
                  label: 'Untested Bot',
                }),
                authConnection({
                  revokedAt: null,
                  providerKey: 'telegram-bot',
                  credentialRef: 'cred_telegram',
                  authType: 'bot-token',
                  authMethodKey: 'bot-token',
                  profileKey: 'support',
                  label: 'Support Bot',
                  account: telegramTested
                    ? {
                        provider_account_id: '123456',
                        display_name: '@support_bot',
                        metadata_json: { username: 'support_bot', bot_id: 123456 },
                      }
                    : null,
                }),
              ]
            : [],
        })
      }
      if (url === '/api/v1/projects/1/auth/telegram-bot/credentials') {
        connected = true
        return json(
          {
            data: authConnection({
              revokedAt: null,
              providerKey: 'telegram-bot',
              credentialRef: 'cred_telegram',
              authType: 'bot-token',
              authMethodKey: 'bot-token',
              profileKey: 'support',
              label: 'Support Bot',
            }),
          },
          201,
        )
      }
      if (url === '/api/v1/projects/1/auth/test') {
        telegramTested = true
        return json({
          data: {
            credential_ref: 'cred_telegram',
            provider_key: 'telegram-bot',
            ok: true,
            status: 'ok',
            summary: 'telegram-bot credentials are reachable',
            checked_at: '2026-05-23T00:00:00Z',
            retryable: false,
            next_action: null,
            metadata: { username: 'support_bot', bot_id: 123456, is_bot: true },
          },
          run_id: null,
          project_id: 1,
        })
      }
      if (url === '/api/v1/operations/communicationBotProfile.list/call') {
        return json({ items: botProfiles, next_cursor: null, total_estimate: botProfiles.length })
      }
      if (url === '/api/v1/operations/communicationBotProfile.upsert/call') {
        const body = JSON.parse(String(init?.body))
        const args = body.arguments
        botProfiles.splice(0, botProfiles.length, {
          record_id: 1,
          project_id: 1,
          external_id: `telegram-bot-profile:${args.key}`,
          key: args.key,
          provider_key: 'telegram-bot',
          auth_profile_key: args.auth_profile_key,
          enabled: true,
          bot_username: args.bot_username,
          ingress_mode: 'webhook',
          allowed_updates: ['message', 'callback_query'],
          identity: args.identity,
          agent_guidance: args.agent_guidance,
          access_policy: args.access_policy,
          trigger_policy: args.trigger_policy,
          visibility_policy: args.visibility_policy,
          context_policy: { include_last_messages: 50 },
          response_policy: args.response_policy,
          refs: {},
          webhook_base_url: null,
          allowed_webhook_hosts: [],
        })
        return json({ data: botProfiles[0], run_id: null, project_id: 1 })
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/connections', component: ConnectionsView }],
    })
    await router.push('/projects/1/connections')
    await router.isReady()

    const wrapper = mount(ConnectionsView, { global: { plugins: [router] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected.'))
    await clickButton(wrapper, 'Add connection')
    await vi.waitFor(() =>
      expect(wrapper.find('input[placeholder="123456:ABC..."]').exists()).toBe(true),
    )

    await wrapper
      .find<HTMLInputElement>('input[placeholder="123456:ABC..."]')
      .setValue('123456:ABC')
    await wrapper
      .find<HTMLInputElement>('input[placeholder="Primary account"]')
      .setValue('Support Bot')
    await wrapper.find<HTMLInputElement>('input[placeholder="default"]').setValue('support')
    expect(wrapper.text()).toContain('Advanced connection settings')
    expect(wrapper.text()).not.toContain('Bot API Base URL')
    await clickButton(wrapper, 'Save connection')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Support Bot'))

    await clickButton(wrapper, 'Add bot profile')
    expect(wrapper.find<HTMLInputElement>('input[placeholder="@support_bot"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Telegram identity: @support_bot')
    await wrapper
      .find<HTMLTextAreaElement>('textarea[placeholder^="Handle support"]')
      .setValue('Handle support requests from approved Telegram users.')
    await wrapper
      .find<HTMLInputElement>('input[placeholder="telegram-chat:999"]')
      .setValue('telegram-chat:999')
    await wrapper
      .find<HTMLInputElement>('input[placeholder="telegram-user:555"]')
      .setValue('telegram-user:555')
    await clickButton(wrapper, 'Save bot profile')

    await vi.waitFor(() => expect(wrapper.text()).toContain('support-bot'))
    const profileCalls = postedBodies.filter(
      (body) =>
        typeof body === 'object' &&
        body !== null &&
        'arguments' in body &&
        (body as { arguments?: { key?: string } }).arguments?.key === 'support-bot',
    )
    expect(profileCalls).toHaveLength(1)
    expect(profileCalls[0]).toMatchObject({
      arguments: {
        project_id: 1,
        key: 'support-bot',
        auth_profile_key: 'support',
        bot_username: 'support_bot',
        identity: {
          display_name: 'Support Bot',
          purpose: 'Handle support requests from approved Telegram users.',
          voice: 'Clear, concise, and operational.',
        },
        access_policy: {
          dm_mode: 'all',
          group_mode: 'all',
          user_mode: 'allowlist',
          allowed_chat_refs: ['telegram-chat:999'],
          allowed_user_refs: ['telegram-user:555'],
        },
        trigger_policy: {
          commands: [
            expect.objectContaining({
              command: '/support',
              guidance: expect.stringContaining('Triage the request'),
            }),
          ],
        },
      },
    })
    expect(JSON.stringify(profileCalls)).not.toContain('123456:ABC')
    expect(wrapper.text()).not.toContain('123456:ABC')
  })

  it('stores Slack bot credentials with discovery and no deferred setup fields', async () => {
    let connected = false
    let slackTested = false
    let slackTestCount = 0
    const postedBodies: unknown[] = []

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) postedBodies.push(JSON.parse(String(init.body)))
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse

      if (url === '/api/v1/auth/providers') {
        return json([authProvider('slack-bot', 'Slack Bot', 'bot-token', slackBotMethod())])
      }
      if (url === '/api/v1/projects/1/auth/status') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [],
          connections: connected
            ? [
                authConnection({
                  revokedAt: null,
                  providerKey: 'slack-bot',
                  credentialRef: 'cred_slack',
                  authType: 'bot-token',
                  authMethodKey: 'bot-token',
                  profileKey: 'support',
                  label: 'Support Slack',
                  account: slackTested
                    ? {
                        provider_account_id: 'T123',
                        display_name: 'Acme',
                        metadata_json: {
                          team_id: 'T123',
                          team: 'Acme',
                          user_id: 'U_BOT',
                          user: 'stackos',
                          bot_id: 'B123',
                        },
                      }
                    : null,
                }),
              ]
            : [],
        })
      }
      if (url === '/api/v1/projects/1/auth/slack-bot/credentials') {
        connected = true
        return json(
          {
            data: authConnection({
              revokedAt: null,
              providerKey: 'slack-bot',
              credentialRef: 'cred_slack',
              authType: 'bot-token',
              authMethodKey: 'bot-token',
              profileKey: 'support',
              label: 'Support Slack',
            }),
          },
          201,
        )
      }
      if (url === '/api/v1/projects/1/auth/test') {
        slackTested = true
        slackTestCount += 1
        return json({
          data: {
            credential_ref: 'cred_slack',
            provider_key: 'slack-bot',
            ok: true,
            status: 'ok',
            summary: 'slack-bot credentials are reachable',
            checked_at: '2026-05-23T00:00:00Z',
            retryable: false,
            next_action: null,
            metadata: {
              team_id: 'T123',
              team: 'Acme',
              user_id: 'U_BOT',
              user: 'stackos',
              bot_id: 'B123',
            },
          },
          run_id: null,
          project_id: 1,
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

    const wrapper = mount(ConnectionsView, { global: { plugins: [router] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected.'))
    await clickButton(wrapper, 'Add connection')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Slack Bot'))

    expect(wrapper.find<HTMLInputElement>('input[placeholder="xoxb-..."]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Signing Secret')
    expect(wrapper.text()).not.toContain('App-Level Token')
    expect(wrapper.text()).not.toContain('Team ID')
    expect(wrapper.text()).not.toContain('App ID')
    expect(wrapper.text()).not.toContain('Bot User ID')

    await wrapper.find<HTMLInputElement>('input[placeholder="xoxb-..."]').setValue('xoxb-secret')
    await wrapper
      .find<HTMLInputElement>('input[placeholder="Primary account"]')
      .setValue('Support Slack')
    await wrapper.find<HTMLInputElement>('input[placeholder="default"]').setValue('support')
    const signingSecretInput = wrapper
      .findAll<HTMLInputElement>('input[type="password"]')
      .find((input) => input.element.placeholder !== 'xoxb-...')
    expect(signingSecretInput).toBeDefined()
    await signingSecretInput?.setValue('signing-secret')

    await clickButton(wrapper, 'Save connection')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Acme'))

    expect(slackTestCount).toBe(1)
    expect(postedBodies).toContainEqual({
      auth_method_key: 'bot-token',
      profile_key: 'support',
      label: 'Support Slack',
      fields: {
        bot_token: 'xoxb-secret',
        signing_secret: 'signing-secret',
      },
    })
    expect(postedBodies).toContainEqual({ credential_ref: 'cred_slack' })
    expect(wrapper.text()).not.toContain('xoxb-secret')
    expect(wrapper.text()).not.toContain('signing-secret')

    await clickButton(wrapper, 'Test')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Slack bot verified for Acme.'))
    expect(slackTestCount).toBe(2)
    expect(wrapper.text()).not.toContain('Loading connections...')
  })

  it('does not report failed credentials as connected and keeps operator actions available', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse

      if (url === '/api/v1/auth/providers') {
        return json([authProvider('firecrawl', 'Firecrawl', 'api-key', apiKeyMethod('fc-...'))])
      }
      if (url === '/api/v1/projects/1/auth/status') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [],
          connections: [authConnection({ revokedAt: null, status: 'failed' })],
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

    const wrapper = mount(ConnectionsView, { global: { plugins: [router] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('Firecrawl'))

    expect(wrapper.text()).toContain('failed')
    expect(wrapper.text()).not.toContain('1 connected')
    expect(wrapper.findAll('button').map((button) => button.text().trim())).toContain('Test')
    expect(wrapper.findAll('button').map((button) => button.text().trim())).toContain('Revoke')
  })
})

function authProvider(
  key: string,
  name: string,
  authType: string,
  authMethods: unknown[] = apiKeyMethod(),
) {
  return {
    id: key === 'firecrawl' ? 1 : 2,
    plugin_id: 1,
    plugin_slug: 'utils',
    key,
    name,
    description: '',
    auth_type: authType,
    auth_methods: authMethods,
    scopes: [],
    config_json: { auth_methods: authMethods },
  }
}

function authConnection({
  revokedAt,
  status,
  providerKey = 'firecrawl',
  credentialRef = 'cred_firecrawl',
  authType = 'api-key',
  authMethodKey = 'api_key',
  profileKey = 'default',
  label = 'Primary Firecrawl',
  account = null,
}: {
  revokedAt: string | null
  status?: string
  providerKey?: string
  credentialRef?: string
  authType?: string
  authMethodKey?: string
  profileKey?: string
  label?: string
  account?: Record<string, unknown> | null
}) {
  return {
    credential_ref: credentialRef,
    project_id: 1,
    provider_key: providerKey,
    auth_type: authType,
    auth_method_key: authMethodKey,
    profile_key: profileKey,
    label,
    status: status ?? (revokedAt ? 'revoked' : 'connected'),
    expires_at: null,
    last_tested_at: null,
    revoked_at: revokedAt,
    scopes: [],
    account,
    setup_required: revokedAt !== null || status === 'failed',
  }
}

function apiKeyMethod(placeholder = 'sk-...') {
  return [
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
          placeholder,
        },
      ],
      config: null,
    },
  ]
}

function telegramBotMethod() {
  return [
    {
      key: 'bot-token',
      label: 'Bot token',
      auth_type: 'bot-token',
      description: '',
      interactive: false,
      payload_format: 'json',
      payload_field: null,
      fields: [
        {
          key: 'bot_token',
          label: 'Bot Token',
          type: 'secret',
          secret: true,
          required: true,
          placeholder: '123456:ABC...',
        },
        {
          key: 'webhook_secret_token',
          label: 'Webhook Secret Token',
          type: 'secret',
          secret: true,
          required: false,
          placeholder: '',
        },
        {
          key: 'api_base_url',
          label: 'Local Bot API URL',
          type: 'text',
          secret: false,
          required: false,
          placeholder: 'http://127.0.0.1:8081',
          description:
            "Leave blank for Telegram's hosted Bot API. Use only with the official self-hosted Telegram Bot API server.",
        },
      ],
      config: null,
    },
  ]
}

function slackBotMethod() {
  return [
    {
      key: 'bot-token',
      label: 'Bot token and signing secret',
      auth_type: 'bot-token',
      description: '',
      interactive: false,
      payload_format: 'json',
      payload_field: null,
      fields: [
        {
          key: 'bot_token',
          label: 'Bot Token',
          type: 'secret',
          secret: true,
          required: true,
          placeholder: 'xoxb-...',
        },
        {
          key: 'signing_secret',
          label: 'Signing Secret',
          type: 'secret',
          secret: true,
          required: true,
          placeholder: '',
          description: 'Used by daemon-side Slack Events API and interaction signature verification.',
        },
      ],
      config: null,
    },
  ]
}

function hasCredentialFields(body: unknown): boolean {
  return typeof body === 'object' && body !== null && 'fields' in body
}

function catalogJson(url: string): Response | null {
  const now = '2026-05-22T00:00:00Z'
  if (url === '/api/v1/plugins?project_id=1') {
    return json([
      {
        id: 1,
        slug: 'utils',
        name: 'Utils',
        version: '0.1.0',
        description: '',
        source: 'builtin',
        manifest_json: {},
        enabled_for_project: true,
        created_at: now,
        updated_at: now,
      },
    ])
  }
  if (url === '/api/v1/catalog?project_id=1') return json({ plugins: [] })
  if (
    [
      '/api/v1/capabilities?project_id=1',
      '/api/v1/providers?project_id=1',
      '/api/v1/actions?project_id=1',
      '/api/v1/resources?project_id=1',
    ].includes(url)
  ) {
    return json([])
  }
  return null
}

async function clickButton(wrapper: ReturnType<typeof mount>, label: string): Promise<void> {
  const button = wrapper.findAll('button').find((candidate) => candidate.text().trim() === label)
  expect(button, `${label} button`).toBeDefined()
  await button?.trigger('click')
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}
