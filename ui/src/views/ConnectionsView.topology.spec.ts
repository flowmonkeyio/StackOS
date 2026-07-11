import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import ConnectionsView from './ConnectionsView.vue'
import {
  authProvider,
  authConnection,
  telegramBotMethod,
  slackBotMethod,
  catalogJson,
  clickButton,
  mountConnections,
  json,
} from './connections/connectionsViewTestSupport'

const ORIG_FETCH = globalThis.fetch

describe('ConnectionsView ingress and topology', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
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
              action_required: true,
              next_action: {
                kind: 'manual-provider-update',
                label: 'Copy webhook URL',
                title: 'Update Slack webhook for workspace-slack',
                instructions:
                  'Copy this URL into the Slack app Event Subscriptions Request URL and Interactivity Request URL fields.',
                url: 'https://example.ngrok-free.app/api/v1/ingress/slack/1/workspace-slack',
                provider_fields: ['Event Subscriptions Request URL', 'Interactivity Request URL'],
              },
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
    // Overview — reachable ingress can still need an operator action for one provider route.
    expect(wrapper.text()).toContain('Slack webhook needs manual update')
    expect(wrapper.text()).toContain('workspace-slack needs its webhook URL copied')
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

  it('does not offer a manual provider copy action for local-only ingress routes', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse

      if (url === '/api/v1/auth/providers') {
        return json([])
      }
      if (url === '/api/v1/projects/1/auth/status') {
        return json({ project_id: 1, provider_key: null, providers: [], connections: [] })
      }
      if (
        [
          '/api/v1/operations/communicationProfile.list/call',
          '/api/v1/operations/communicationTarget.list/call',
          '/api/v1/operations/communicationSurface.list/call',
          '/api/v1/operations/communicationRoute.list/call',
        ].includes(url)
      ) {
        return json({ items: [], next_cursor: null, total_estimate: 0 })
      }
      if (url === '/api/v1/operations/ingressEndpoint.status/call') {
        return json({
          configured: true,
          ready: false,
          endpoint: {
            driver: 'local-tunnel',
            status: 'running',
            public_base_url: null,
            local_base_url: 'http://127.0.0.1:5180',
          },
          routes: [
            {
              provider_key: 'slack-bot',
              profile_key: 'slack-bot',
              local_url: 'http://127.0.0.1:5180/api/v1/ingress/slack/1/slack-bot',
              remote_status: 'manual_provider_update_required',
              action_required: true,
              next_action: {
                kind: 'manual-provider-update',
                label: 'Copy webhook URL',
                title: 'Update Slack webhook for slack-bot',
                instructions:
                  'Copy this URL into the Slack app Event Subscriptions Request URL and Interactivity Request URL fields.',
                url: null,
                provider_fields: ['Event Subscriptions Request URL', 'Interactivity Request URL'],
              },
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
    await router.push('/projects/1/connections?section=connectivity')
    await router.isReady()

    const wrapper = mountConnections(router)

    await vi.waitFor(() => expect(wrapper.text()).toContain('Manual update needed'))
    expect(wrapper.text()).toContain(
      'Configure a public address before updating Slack in the provider console.',
    )
    expect(wrapper.findAll('button').map((button) => button.text().trim())).not.toContain(
      'Copy webhook URL',
    )
  })
})
