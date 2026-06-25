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

    const wrapper = mountConnections(router)
    await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected'))
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

    const wrapper = mountConnections(router)
    await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected'))
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

  it('creates Telegram profiles without posting bot tokens to the profile operation', async () => {
    let connected = false
    let telegramTested = false
    const postedBodies: unknown[] = []
    const telegramProfiles: unknown[] = []

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
      if (url === '/api/v1/operations/communicationProfile.list/call') {
        return json({
          items: telegramProfiles,
          next_cursor: null,
          total_estimate: telegramProfiles.length,
        })
      }
      if (url === '/api/v1/operations/communicationProfile.upsert/call') {
        const body = JSON.parse(String(init?.body))
        const args = body.arguments
        telegramProfiles.splice(0, telegramProfiles.length, {
          record_id: 1,
          project_id: 1,
          profile_ref: `communication-profile:${args.key}`,
          key: args.key,
          enabled: true,
          identity: args.identity,
          provider_facets: args.provider_facets,
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
        return json({ data: telegramProfiles[0], run_id: null, project_id: 1 })
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

    await clickButton(wrapper, 'Add bot')
    await clickButton(wrapper, 'Telegram bot')
    expect(wrapper.find<HTMLInputElement>('input[placeholder="@support_bot"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Telegram identity: @support_bot')
    await wrapper
      .find<HTMLTextAreaElement>('textarea[placeholder^="Handle approved"]')
      .setValue('Handle support requests from approved Telegram users.')
    await wrapper
      .find<HTMLInputElement>('input[placeholder="telegram-chat:999"]')
      .setValue('telegram-chat:999')
    await wrapper
      .find<HTMLInputElement>('input[placeholder="telegram-user:555"]')
      .setValue('telegram-user:555')
    await clickButton(wrapper, 'Save Telegram profile')

    await vi.waitFor(() => expect(wrapper.text()).toContain('ops-bot'))
    const profileCalls = postedBodies.filter(
      (body) =>
        typeof body === 'object' &&
        body !== null &&
        'arguments' in body &&
        (body as { arguments?: { key?: string } }).arguments?.key === 'ops-bot',
    )
    expect(profileCalls).toHaveLength(1)
    expect(profileCalls[0]).toMatchObject({
      arguments: {
        project_id: 1,
        key: 'ops-bot',
        provider_facets: {
          'telegram-bot': expect.objectContaining({
            auth_profile_key: 'support',
            bot_username: 'support_bot',
          }),
        },
        identity: {
          display_name: 'Ops Bot',
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
              command: '/ops',
              guidance: expect.stringContaining('Triage the request'),
            }),
          ],
        },
      },
    })
    expect(JSON.stringify(profileCalls)).not.toContain('123456:ABC')
    expect(wrapper.text()).not.toContain('123456:ABC')
  })

  it('preserves non-Telegram profile facets and policies when editing Telegram setup', async () => {
    const postedCalls: Array<{ url: string; body: Record<string, unknown> }> = []
    const profile = {
      record_id: 1,
      project_id: 1,
      profile_ref: 'communication-profile:support',
      key: 'support',
      enabled: true,
      identity: {
        display_name: 'Support Bot',
        purpose: 'Handle support.',
        voice: 'Calm.',
      },
      provider_facets: {
        'telegram-bot': {
          auth_profile_key: 'support',
          bot_username: 'support_bot',
          ingress_mode: 'webhook',
          allowed_updates: ['message', 'callback_query'],
        },
        'slack-bot': {
          auth_profile_key: 'ops-slack',
          bot_user_id: 'U123',
        },
      },
      agent_guidance: {
        default_instructions: 'Triage first.',
        escalation: 'Escalate billing.',
      },
      access_policy: {
        dm_mode: 'all',
        group_mode: 'all',
        user_mode: 'allowlist',
        allowed_user_refs: ['telegram-user:555'],
        denied_user_refs: ['telegram-user:999'],
      },
      trigger_policy: {
        dm_trigger: 'always',
        group_trigger: 'mention_or_command',
        commands: [{ command: '/support', guidance: 'Triage the request.' }],
      },
      visibility_policy: { store_non_trigger_messages: true, keep_context: true },
      context_policy: { include_last_messages: 12 },
      response_policy: { origin_required: true, custom_response_flag: true },
      send_policy: {
        mode: 'explicit-targets',
        allowed_target_refs: ['communication-target:roadmap'],
      },
      handoff_policy: { mode: 'explicit-targets', allowed_route_refs: ['route:one'] },
      approval_policy: { mode: 'manual' },
      metadata_json: { owner: 'ops' },
    }

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) postedCalls.push({ url, body: JSON.parse(String(init.body)) })
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
          connections: [
            authConnection({
              revokedAt: null,
              providerKey: 'telegram-bot',
              credentialRef: 'cred_telegram',
              authType: 'bot-token',
              authMethodKey: 'bot-token',
              profileKey: 'support',
              label: 'Support Bot',
              account: {
                provider_account_id: '123456',
                display_name: '@support_bot',
                metadata_json: { username: 'support_bot', bot_id: 123456 },
              },
            }),
          ],
        })
      }
      if (url === '/api/v1/operations/communicationProfile.list/call') {
        return json({ items: [profile], next_cursor: null, total_estimate: 1 })
      }
      if (url === '/api/v1/operations/communicationProfile.upsert/call') {
        const body = JSON.parse(String(init?.body))
        return json({ data: { ...profile, ...body.arguments }, run_id: null, project_id: 1 })
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
    await vi.waitFor(() => expect(wrapper.text()).toContain('Support Bot'))
    await clickButton(wrapper, 'Configure')
    await clickButton(wrapper, 'Save Telegram profile')

    const saveCall = postedCalls.find(
      (call) =>
        call.url === '/api/v1/operations/communicationProfile.upsert/call' &&
        (call.body as { arguments?: { key?: string } }).arguments?.key === 'support',
    )
    expect(saveCall).toBeDefined()
    expect(saveCall?.body).toMatchObject({
      arguments: {
        provider_facets: {
          'telegram-bot': expect.objectContaining({
            auth_profile_key: 'support',
            bot_username: 'support_bot',
          }),
          'slack-bot': { auth_profile_key: 'ops-slack', bot_user_id: 'U123' },
        },
        access_policy: expect.objectContaining({
          denied_user_refs: ['telegram-user:999'],
        }),
        context_policy: { include_last_messages: 12 },
        response_policy: expect.objectContaining({ custom_response_flag: true }),
        send_policy: {
          mode: 'explicit-targets',
          allowed_target_refs: ['communication-target:roadmap'],
        },
        handoff_policy: { mode: 'explicit-targets', allowed_route_refs: ['route:one'] },
        approval_policy: { mode: 'manual' },
        metadata_json: { owner: 'ops' },
      },
    })
  })

  it('edits a Slack bot, preserving its facet and exposing no secrets', async () => {
    const postedCalls: Array<{ url: string; body: Record<string, unknown> }> = []
    const slackProfile = {
      record_id: 2,
      project_id: 1,
      profile_ref: 'communication-profile:ops-slack',
      key: 'ops-slack',
      enabled: true,
      identity: { display_name: 'Ops Slack Bot', purpose: 'Handle ops.', voice: 'Concise.' },
      provider_facets: {
        'slack-bot': {
          auth_profile_key: 'default',
          team_id: 'T123',
          team_name: 'Acme',
          bot_user_id: 'U_BOT',
          bot_username: 'acme_bot',
          ingress_path: '/api/v1/ingress/slack/1/ops-slack',
          ingress_url: 'https://example/ingress/slack/1/ops-slack',
        },
      },
      agent_guidance: { default_instructions: 'Triage first.', escalation: 'Escalate billing.' },
      access_policy: {
        user_mode: 'allowlist',
        allowed_user_refs: ['slack-user:U111'],
        allowed_surface_refs: ['slack-channel:C1'],
        denied_user_refs: ['slack-user:U999'],
      },
      trigger_policy: { dm_trigger: 'always', mention_patterns: ['ops'], commands: [{ command: '/ops' }] },
      visibility_policy: { store_non_trigger_messages: true },
      context_policy: { include_last_messages: 10 },
      response_policy: { origin_required: true },
      send_policy: { mode: 'explicit-targets' },
      handoff_policy: { mode: 'explicit-targets' },
      approval_policy: { mode: 'none' },
      metadata_json: { owner: 'ops' },
    }

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) postedCalls.push({ url, body: JSON.parse(String(init.body)) })
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
          connections: [
            authConnection({
              revokedAt: null,
              providerKey: 'slack-bot',
              credentialRef: 'cred_slack',
              authType: 'bot-token',
              authMethodKey: 'bot-token',
              profileKey: 'default',
              label: 'Acme Slack',
              account: {
                provider_account_id: 'T123',
                display_name: 'Acme',
                metadata_json: { team_id: 'T123', team: 'Acme', user_id: 'U_BOT' },
              },
            }),
          ],
        })
      }
      if (url === '/api/v1/operations/communicationProfile.list/call') {
        return json({ items: [slackProfile], next_cursor: null, total_estimate: 1 })
      }
      if (url === '/api/v1/operations/communicationProfile.upsert/call') {
        const body = JSON.parse(String(init?.body))
        return json({ data: { ...slackProfile, ...body.arguments }, run_id: null, project_id: 1 })
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
    await vi.waitFor(() => expect(wrapper.text()).toContain('Ops Slack Bot'))
    await clickButton(wrapper, 'Configure')
    await wrapper.find<HTMLInputElement>('input[placeholder="Ops Bot"]').setValue('Ops Slack Bot v2')
    await clickButton(wrapper, 'Save Slack bot')

    const saveCall = postedCalls.find(
      (call) =>
        call.url === '/api/v1/operations/communicationProfile.upsert/call' &&
        (call.body as { arguments?: { key?: string } }).arguments?.key === 'ops-slack',
    )
    expect(saveCall).toBeDefined()
    expect(saveCall?.body).toMatchObject({
      arguments: {
        key: 'ops-slack',
        identity: expect.objectContaining({ display_name: 'Ops Slack Bot v2' }),
        provider_facets: {
          'slack-bot': expect.objectContaining({
            auth_profile_key: 'default',
            team_id: 'T123',
            bot_user_id: 'U_BOT',
            ingress_path: '/api/v1/ingress/slack/1/ops-slack',
          }),
        },
        access_policy: expect.objectContaining({
          user_mode: 'allowlist',
          allowed_user_refs: ['slack-user:U111'],
          allowed_surface_refs: ['slack-channel:C1'],
          denied_user_refs: ['slack-user:U999'],
        }),
        metadata_json: { owner: 'ops' },
      },
    })
    // Secrets never leave the connection.
    expect(JSON.stringify(saveCall)).not.toContain('xoxb')
    expect(JSON.stringify(saveCall)).not.toContain('signing')
  })

  it('rebuilds Slack bot identity from the selected connection when editing', async () => {
    const postedCalls: Array<{ url: string; body: Record<string, unknown> }> = []
    const slackProfile = {
      record_id: 2,
      project_id: 1,
      profile_ref: 'communication-profile:ops-slack',
      key: 'ops-slack',
      enabled: true,
      identity: { display_name: 'Ops Slack Bot', purpose: 'Handle ops.', voice: 'Concise.' },
      provider_facets: {
        'slack-bot': {
          auth_profile_key: 'default',
          team_id: 'T123',
          team_name: 'Acme',
          bot_user_id: 'U_OLD',
          ingress_path: '/api/v1/ingress/slack/1/ops-slack',
        },
      },
      agent_guidance: {},
      access_policy: {
        user_mode: 'allowlist',
        allowed_user_refs: ['slack-user:U111'],
        allowed_surface_refs: ['slack-channel:C1'],
      },
      trigger_policy: { dm_trigger: 'always', mention_patterns: ['ops'] },
      visibility_policy: {},
      context_policy: {},
      response_policy: {},
      send_policy: { mode: 'explicit-targets' },
      handoff_policy: { mode: 'explicit-targets' },
      approval_policy: { mode: 'none' },
      metadata_json: {},
    }

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) postedCalls.push({ url, body: JSON.parse(String(init.body)) })
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
          connections: [
            authConnection({
              revokedAt: null,
              providerKey: 'slack-bot',
              credentialRef: 'cred_slack_default',
              authType: 'bot-token',
              authMethodKey: 'bot-token',
              profileKey: 'default',
              label: 'Acme Slack',
              account: {
                provider_account_id: 'T123',
                display_name: 'Acme',
                metadata_json: { team_id: 'T123', team: 'Acme', user_id: 'U_OLD' },
              },
            }),
            authConnection({
              revokedAt: null,
              providerKey: 'slack-bot',
              credentialRef: 'cred_slack_beta',
              authType: 'bot-token',
              authMethodKey: 'bot-token',
              profileKey: 'beta',
              label: 'Beta Slack',
              account: {
                provider_account_id: 'T456',
                display_name: 'Beta',
                metadata_json: {
                  team_id: 'T456',
                  team: 'Beta',
                  user_id: 'U_NEW',
                  bot_id: 'B_NEW',
                },
              },
            }),
          ],
        })
      }
      if (url === '/api/v1/operations/communicationProfile.list/call') {
        return json({ items: [slackProfile], next_cursor: null, total_estimate: 1 })
      }
      if (url === '/api/v1/operations/communicationProfile.upsert/call') {
        const body = JSON.parse(String(init?.body))
        return json({ data: { ...slackProfile, ...body.arguments }, run_id: null, project_id: 1 })
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
    await vi.waitFor(() => expect(wrapper.text()).toContain('Ops Slack Bot'))
    await clickButton(wrapper, 'Configure')

    const connectionSelect = wrapper.find('[role="combobox"]')
    await connectionSelect.trigger('click')
    const betaOption = wrapper
      .findAll('[role="option"]')
      .find((option) => option.text().includes('Beta Slack'))
    expect(betaOption, 'Beta Slack option').toBeDefined()
    await betaOption?.trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Slack workspace: Beta'))
    await clickButton(wrapper, 'Save Slack bot')

    const saveCall = postedCalls.find(
      (call) =>
        call.url === '/api/v1/operations/communicationProfile.upsert/call' &&
        (call.body as { arguments?: { key?: string } }).arguments?.key === 'ops-slack',
    )
    expect(saveCall).toBeDefined()
    expect(saveCall?.body).toMatchObject({
      arguments: {
        provider_facets: {
          'slack-bot': {
            auth_profile_key: 'beta',
            team_id: 'T456',
            team_name: 'Beta',
            bot_user_id: 'U_NEW',
            bot_id: 'B_NEW',
          },
        },
      },
    })
    expect(JSON.stringify(saveCall)).not.toContain('T123')
    expect(JSON.stringify(saveCall)).not.toContain('U_OLD')
    expect(JSON.stringify(saveCall)).not.toContain('/api/v1/ingress/slack/1/ops-slack')
  })

  it('creates a Slack bot from a tested connection without exposing secrets', async () => {
    const postedBodies: unknown[] = []
    let profiles: unknown[] = []

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
          connections: [
            authConnection({
              revokedAt: null,
              providerKey: 'slack-bot',
              credentialRef: 'cred_slack',
              authType: 'bot-token',
              authMethodKey: 'bot-token',
              profileKey: 'default',
              label: 'Acme Slack',
              account: {
                provider_account_id: 'T123',
                display_name: 'Acme',
                metadata_json: { team_id: 'T123', team: 'Acme', user_id: 'U_BOT', bot_id: 'B1' },
              },
            }),
          ],
        })
      }
      if (url === '/api/v1/operations/communicationProfile.list/call') {
        return json({ items: profiles, next_cursor: null, total_estimate: profiles.length })
      }
      if (url === '/api/v1/operations/communicationProfile.upsert/call') {
        const args = JSON.parse(String(init?.body)).arguments
        profiles = [
          {
            record_id: 9,
            project_id: 1,
            profile_ref: `communication-profile:${args.key}`,
            key: args.key,
            enabled: true,
            identity: args.identity,
            provider_facets: args.provider_facets,
            agent_guidance: args.agent_guidance,
            access_policy: args.access_policy,
            trigger_policy: args.trigger_policy,
            visibility_policy: {},
            context_policy: {},
            response_policy: {},
            send_policy: {},
            handoff_policy: {},
            approval_policy: {},
            metadata_json: {},
          },
        ]
        return json({ data: profiles[0], run_id: null, project_id: 1 })
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
    await vi.waitFor(() => expect(wrapper.text()).toContain('Acme Slack'))
    await clickButton(wrapper, 'Add bot')
    await clickButton(wrapper, 'Slack bot')
    await wrapper.find<HTMLInputElement>('input[placeholder="ops-bot"]').setValue('ops-bot')
    await wrapper
      .find<HTMLInputElement>('input[placeholder="slack-user:U123"]')
      .setValue('slack-user:U111')
    await clickButton(wrapper, 'Save Slack bot')

    const saveCall = postedBodies.find(
      (body) =>
        typeof body === 'object' &&
        body !== null &&
        'arguments' in body &&
        (body as { arguments?: { key?: string } }).arguments?.key === 'ops-bot',
    )
    expect(saveCall).toBeDefined()
    expect(saveCall).toMatchObject({
      arguments: {
        key: 'ops-bot',
        identity: expect.objectContaining({ display_name: 'Slack Bot' }),
        provider_facets: {
          'slack-bot': expect.objectContaining({
            auth_profile_key: 'default',
            team_id: 'T123',
            bot_user_id: 'U_BOT',
          }),
        },
        access_policy: expect.objectContaining({
          user_mode: 'allowlist',
          allowed_user_refs: ['slack-user:U111'],
        }),
      },
    })
    expect(JSON.stringify(saveCall)).not.toContain('xoxb')
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

    const wrapper = mountConnections(router)
    await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected'))
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

  it('shows Trackbooth under Affiliation in the searchable service selector', async () => {
    const trackboothMethods = [
      {
        key: 'api-key',
        label: 'Trackbooth API key',
        auth_type: 'api-key',
        description: '',
        interactive: false,
        payload_format: 'json',
        payload_field: null,
        fields: [
          {
            key: 'api_key',
            label: 'API Key',
            type: 'secret',
            secret: true,
            required: true,
            placeholder: '',
          },
          {
            key: 'api_base_url',
            label: 'API URL',
            type: 'text',
            secret: false,
            required: false,
            placeholder: 'https://apis.trackbooth.com',
            description: 'Defaults to production.',
          },
        ],
        config: null,
      },
    ]

    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const now = '2026-05-22T00:00:00Z'
      if (url === '/api/v1/plugins?project_id=1') {
        return json([
          {
            id: 1,
            slug: 'media-buying',
            name: 'Media Buying',
            version: '0.1.0',
            description: '',
            source: 'builtin',
            manifest_json: {},
            enabled_for_project: true,
            created_at: now,
            updated_at: now,
          },
          {
            id: 2,
            slug: 'trackbooth',
            name: 'Trackbooth',
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
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse

      if (url === '/api/v1/auth/providers') {
        return json([
          {
            id: 1,
            plugin_id: 1,
            plugin_slug: 'media-buying',
            key: 'meta-ads',
            name: 'Meta Ads',
            description: '',
            auth_type: 'oauth',
            auth_methods: apiKeyMethod(),
            scopes: [],
            config_json: {},
          },
          {
            id: 2,
            plugin_id: 2,
            plugin_slug: 'trackbooth',
            key: 'trackbooth',
            name: 'Trackbooth',
            description: 'Trackbooth Agent API provider.',
            auth_type: 'api-key',
            auth_methods: trackboothMethods,
            scopes: [],
            config_json: { connection_category: 'Affiliation' },
          },
        ])
      }
      if (url === '/api/v1/projects/1/auth/status') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [],
          connections: [],
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
    await vi.waitFor(() => expect(wrapper.text()).not.toContain('Loading connections...'))
    await clickButton(wrapper, 'Add connection')
    await vi.waitFor(() => expect(wrapper.find('[role="combobox"]').exists()).toBe(true))

    await wrapper.get('[role="combobox"]').trigger('click')
    await wrapper.get<HTMLInputElement>('input[aria-label="Search options"]').setValue('track')

    expect(wrapper.findAll('[role="option"]').map((option) => option.text())).toEqual([
      'Trackbooth',
    ])
    expect(wrapper.text()).toContain('Affiliation')

    await wrapper.get('[role="option"]').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Trackbooth Agent API provider.'))
    expect(wrapper.text()).toContain('API Key')
    expect(wrapper.find('input[placeholder="https://apis.trackbooth.com"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('renders bots, channels, destinations, and connectivity in plain language', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
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
          connections: [
            authConnection({
              revokedAt: null,
              providerKey: 'slack-bot',
              credentialRef: 'cred_slack',
              authType: 'bot-token',
              authMethodKey: 'bot-token',
              profileKey: 'default',
              label: 'Demo Workspace',
              account: {
                provider_account_id: 'T123',
                display_name: 'Demo Workspace',
                metadata_json: { team: 'Demo Workspace', bot_id: 'B123', user_id: 'U_BOT' },
              },
            }),
          ],
        })
      }
      if (url === '/api/v1/operations/communicationProfile.list/call') {
        return json({
          items: [
            {
              record_id: 20,
              project_id: 1,
              profile_ref: 'communication-profile:workspace-slack',
              key: 'workspace-slack',
              enabled: true,
              identity: { display_name: 'Workspace Slack Bot' },
              agent_guidance: {},
              provider_facets: { 'slack-bot': { bot_user_id: 'U_BOT' } },
              access_policy: {
                user_mode: 'allowlist',
                allowed_user_refs: ['slack-user:U111'],
              },
              trigger_policy: {},
              response_policy: {},
              metadata_json: {},
            },
          ],
          next_cursor: null,
          total_estimate: 1,
        })
      }
      if (url === '/api/v1/operations/communicationTarget.list/call') {
        return json({
          items: [
            {
              record_id: 60,
              project_id: 1,
              target_ref: 'communication-target:slack-roadmap',
              key: 'slack-roadmap',
              display_name: 'Slack #roadmap',
              provider_key: 'slack-bot',
              surface_ref: 'slack-channel:C123',
              profile_ref: 'communication-profile:workspace-slack',
              thread_ref: null,
              enabled: true,
              action_ref: 'communications.slack-bot.message.send',
              action_input_defaults: { surface_ref: 'slack-channel:C123' },
              send_policy: {
                mode: 'explicit-target',
                allowed_profile_refs: ['communication-profile:workspace-slack'],
                allowed_invoker_refs: ['slack-user:U111'],
              },
              metadata_json: {},
            },
          ],
          next_cursor: null,
          total_estimate: 1,
        })
      }
      if (url === '/api/v1/operations/communicationSurface.list/call') {
        return json({
          items: [
            {
              record_id: 50,
              project_id: 1,
              surface_ref: 'slack-channel:C123',
              channel_ref: 'slack-channel:C123',
              provider_key: 'slack-bot',
              kind: 'slack-channel',
              display_name: 'Roadmap channel',
              ingest_enabled: true,
              send_enabled: true,
              capabilities: { can_read: true, can_write: true, can_thread: true },
              audience: 'internal',
              intent: {
                category: 'roadmap-planning',
                summary: 'Internal roadmap planning and critical architecture alignment.',
              },
              agent_guidance: {
                default_instructions: 'Keep sensitive customer data out of this channel.',
              },
              data_scope: { classification: 'internal' },
              external_context: {},
              metadata_json: {},
            },
          ],
          next_cursor: null,
          total_estimate: 1,
        })
      }
      if (url === '/api/v1/operations/ingressEndpoint.status/call') {
        return json({
          configured: true,
          ready: true,
          endpoint: {
            driver: 'local-tunnel',
            status: 'running',
            public_base_url: 'https://example.ngrok-free.app',
          },
          routes: [
            {
              provider_key: 'slack-bot',
              profile_key: 'workspace-slack',
              ingress_url: 'https://example.ngrok-free.app/api/v1/ingress/slack/1/workspace-slack',
              remote_status: 'manual_provider_update_required',
            },
          ],
          notes: [],
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
    await vi.waitFor(() => expect(wrapper.text()).toContain('Workspace Slack Bot'))

    // Bots — provider-neutral identity, labelled by provider.
    expect(wrapper.text()).toContain('Slack')
    // Channels — surface with audience and plain-language sensitivity.
    expect(wrapper.text()).toContain('Roadmap channel')
    expect(wrapper.text()).toContain('Internal roadmap planning and critical architecture alignment.')
    expect(wrapper.text()).toContain('Internal')
    // Destinations — named target resolving to a channel.
    expect(wrapper.text()).toContain('Slack #roadmap')
    expect(wrapper.text()).toContain('slack-channel:C123')
    // Connectivity — per-bot route status, humanized.
    expect(wrapper.text()).toContain('Manual update needed')
  })

  it('configures local-tunnel connectivity and runs a discovery pass', async () => {
    const posted: Array<{ url: string; body: Record<string, unknown> }> = []
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) posted.push({ url, body: JSON.parse(String(init.body)) })
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse

      if (url === '/api/v1/auth/providers') {
        return json([authProvider('telegram-bot', 'Telegram Bot', 'bot-token', telegramBotMethod())])
      }
      if (url === '/api/v1/projects/1/auth/status') {
        return json({ project_id: 1, provider_key: null, providers: [], connections: [] })
      }
      if (url === '/api/v1/operations/ingressEndpoint.status/call') {
        return json({ configured: false, ready: false, endpoint: null, routes: [], notes: [] })
      }
      if (url === '/api/v1/operations/ingressEndpoint.configure/call') {
        return json({ data: {}, run_id: null, project_id: 1 })
      }
      if (url === '/api/v1/operations/ingressEndpoint.refresh/call') {
        return json({ data: {}, run_id: null, project_id: 1 })
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
    await clickButton(wrapper, 'Set up')
    await clickButton(wrapper, 'Save')

    // configure resolves first, then the discovery refresh — wait for both.
    await vi.waitFor(() =>
      expect(
        posted.some((call) => call.url.endsWith('/operations/ingressEndpoint.refresh/call')),
      ).toBe(true),
    )
    const configure = posted.find((call) =>
      call.url.endsWith('/operations/ingressEndpoint.configure/call'),
    )
    expect(configure?.body).toMatchObject({
      arguments: {
        driver: 'local-tunnel',
        driver_config: expect.objectContaining({ provider: 'ngrok' }),
      },
    })
  })

  it('does not report local-tunnel connectivity as configured when discovery fails', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse

      if (url === '/api/v1/auth/providers') {
        return json([authProvider('telegram-bot', 'Telegram Bot', 'bot-token', telegramBotMethod())])
      }
      if (url === '/api/v1/projects/1/auth/status') {
        return json({ project_id: 1, provider_key: null, providers: [], connections: [] })
      }
      if (url === '/api/v1/operations/ingressEndpoint.status/call') {
        return json({ configured: false, ready: false, endpoint: null, routes: [], notes: [] })
      }
      if (url === '/api/v1/operations/ingressEndpoint.configure/call') {
        return json({ data: {}, run_id: null, project_id: 1 })
      }
      if (url === '/api/v1/operations/ingressEndpoint.refresh/call') {
        return json({
          data: {
            endpoint: {
              driver: 'local-tunnel',
              status: 'failed',
              public_base_url: null,
              local_base_url: 'http://127.0.0.1:5180',
            },
            routes: [],
            provider_results: [],
            updated_profile_refs: [],
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

    const wrapper = mountConnections(router)
    await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected'))
    await clickButton(wrapper, 'Set up')
    await clickButton(wrapper, 'Save')

    await vi.waitFor(() => expect(wrapper.text()).toContain('No public address was discovered'))
    expect(wrapper.text()).not.toContain('Connectivity configured.')
  })

  it('applies provider webhook syncs and reports mixed provider results truthfully', async () => {
    const posted: Array<{ url: string; body: Record<string, unknown> }> = []
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) posted.push({ url, body: JSON.parse(String(init.body)) })
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse

      if (url === '/api/v1/auth/providers') {
        return json([authProvider('telegram-bot', 'Telegram Bot', 'bot-token', telegramBotMethod())])
      }
      if (url === '/api/v1/projects/1/auth/status') {
        return json({ project_id: 1, provider_key: null, providers: [], connections: [] })
      }
      if (url === '/api/v1/operations/communicationProfile.list/call') {
        return json({ items: [], next_cursor: null, total_estimate: 0 })
      }
      if (url === '/api/v1/operations/communicationTarget.list/call') {
        return json({ items: [], next_cursor: null, total_estimate: 0 })
      }
      if (url === '/api/v1/operations/communicationSurface.list/call') {
        return json({ items: [], next_cursor: null, total_estimate: 0 })
      }
      if (url === '/api/v1/operations/communicationRoute.list/call') {
        return json({ items: [], next_cursor: null, total_estimate: 0 })
      }
      if (url === '/api/v1/operations/ingressEndpoint.status/call') {
        return json({
          configured: true,
          ready: true,
          endpoint: {
            driver: 'public-url',
            status: 'running',
            public_base_url: 'https://stackos.example.com',
          },
          routes: [
            {
              provider_key: 'telegram-bot',
              profile_key: 'telegram-bot',
              ingress_url: 'https://stackos.example.com/api/v1/ingress/telegram/1/telegram-bot',
              remote_status: 'provider_webhook_not_checked',
            },
            {
              provider_key: 'slack-bot',
              profile_key: 'slack-bot',
              ingress_url: 'https://stackos.example.com/api/v1/ingress/slack/1/slack-bot',
              remote_status: 'manual_provider_update_required',
            },
          ],
          notes: [],
        })
      }
      if (url === '/api/v1/operations/ingressEndpoint.sync/call') {
        return json({
          data: {
            endpoint: {
              driver: 'public-url',
              status: 'running',
              public_base_url: 'https://stackos.example.com',
              local_base_url: 'http://127.0.0.1:5180',
            },
            routes: [],
            provider_results: [
              {
                provider_key: 'telegram-bot',
                profile_key: 'telegram-bot',
                status: 'remote_webhook_updated',
                webhook_url: 'https://stackos.example.com/api/v1/ingress/telegram/1/telegram-bot',
              },
              {
                provider_key: 'slack-bot',
                profile_key: 'slack-bot',
                status: 'manual_provider_update_required',
              },
            ],
            updated_profile_refs: ['communication-profile:telegram-bot'],
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
    await router.push('/projects/1/connections?section=connectivity')
    await router.isReady()

    const wrapper = mountConnections(router)
    await vi.waitFor(() =>
      expect(
        posted.some((call) => call.url.endsWith('/operations/ingressEndpoint.status/call')),
      ).toBe(true),
    )
    await vi.waitFor(() =>
      expect(
        wrapper.findAll('button').some((button) => button.text().trim() === 'Sync to providers'),
      ).toBe(true),
    )
    await clickButton(wrapper, 'Sync to providers')

    await vi.waitFor(() => expect(wrapper.text()).toContain('Synced 1 provider webhook.'))
    expect(wrapper.text()).toContain('Slack (slack-bot) needs manual webhook update.')
    expect(wrapper.text()).toContain('Webhook ready')
    expect(wrapper.text()).toContain('Slack requires manual webhook update.')
    expect(wrapper.text()).toContain('Copy webhook URL')
    const sync = posted.find((call) => call.url.endsWith('/operations/ingressEndpoint.sync/call'))
    expect(sync?.body).toMatchObject({
      arguments: {
        apply_provider_webhooks: true,
        dry_run_provider_webhooks: false,
      },
    })
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

    const wrapper = mountConnections(router)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Firecrawl'))

    expect(wrapper.text()).toContain('Failed')
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
  if (url === '/api/v1/catalog?project_id=1') {
    throw new Error('unexpected aggregate catalog request')
  }
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

function mountConnections(router: ReturnType<typeof createRouter>): ReturnType<typeof mount> {
  return mount(
    { template: '<RouterView />' },
    {
      global: {
        plugins: [router],
        stubs: { teleport: true },
      },
    },
  )
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}
