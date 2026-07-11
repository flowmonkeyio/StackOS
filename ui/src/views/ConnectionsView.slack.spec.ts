import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import ConnectionsView from './ConnectionsView.vue'
import {
  authProvider,
  authConnection,
  slackBotMethod,
  catalogJson,
  clickButton,
  mountConnections,
  json,
} from './connections/connectionsViewTestSupport'

const ORIG_FETCH = globalThis.fetch

describe('ConnectionsView Slack profiles', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
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

    await clickButton(wrapper, 'Save and verify')
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
})
