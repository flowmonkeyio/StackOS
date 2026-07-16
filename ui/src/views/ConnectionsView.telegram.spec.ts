import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import ConnectionsView from './ConnectionsView.vue'
import {
  authProvider,
  authConnection,
  telegramBotMethod,
  catalogJson,
  clickButton,
  mountConnections,
  json,
} from './connections/connectionsViewTestSupport'

const ORIG_FETCH = globalThis.fetch

describe('ConnectionsView Telegram profiles', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
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
    expect(wrapper.text()).not.toContain('Advanced connection settings')
    expect(wrapper.text()).toContain('Local Bot API URL')
    await clickButton(wrapper, 'Save and verify')
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
})
