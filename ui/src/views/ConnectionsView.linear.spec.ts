import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import ConnectionsView from './ConnectionsView.vue'
import {
  authConnection,
  authProvider,
  catalogJson,
  clickButton,
  interactiveMethod,
  json,
  mountConnections,
} from './connections/connectionsViewTestSupport'

const ORIG_FETCH = globalThis.fetch
const CALLBACK_URL = 'https://auth.stackos.flowmonkey.io/api/v1/auth/oauth/callback'

const linearConfig = {
  scopes: ['read', 'write'],
  readiness_groups: {
    'issue-work': {
      label: 'Issue Work',
      required_scopes: ['read', 'write'],
    },
  },
  connection_category: 'Project Management',
  setup: {
    setup_note: 'Create a Linear OAuth application and register the exact callback URL.',
    local_setup_label: 'Connect Linear in StackOS',
    local_setup_note:
      'Complete the user authorization, then run Test separately so StackOS can bind the safe workspace and account identity.',
    console_url: 'https://linear.app/settings/api/applications',
    docs_url: 'https://linear.app/developers/oauth-2-0-authentication',
    callback_url: CALLBACK_URL,
    callback_note:
      'The public HTTPS relay forwards the unchanged OAuth callback to the local daemon.',
    repair_note: 'Reconnect if authorization is expired or missing scope, then run Test again.',
  },
}

function linearProvider() {
  const method = interactiveMethod()
  method[0].key = 'oauth2_authorization_code'
  method[0].label = 'Connect with Linear'
  method[0].auth_type = 'oauth'
  const provider = authProvider('linear', 'Linear', 'oauth', method, linearConfig)
  provider.plugin_slug = 'linear'
  provider.description = 'Linear GraphQL provider using daemon-owned OAuth.'
  return provider
}

function linearConnection() {
  return authConnection({
    revokedAt: null,
    providerKey: 'linear',
    credentialRef: 'cred_linear',
    authType: 'oauth',
    authMethodKey: 'oauth2_authorization_code',
    label: 'Primary Linear',
    account: {
      provider_account_id: 'safe-account-ref',
      display_name: 'Acme Workspace',
      metadata_json: { organization_ref: 'provider-object:organization-safe' },
    },
    scopes: ['read', 'write'],
    expiresAt: '2026-08-23T00:00:00Z',
    lastTestedAt: '2026-07-23T00:00:00Z',
  })
}

describe('ConnectionsView Linear presentation', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('uses the generic OAuth connection flow, readiness, account binding, and repair controls', async () => {
    const provider = linearProvider()
    const connection = linearConnection()
    const postedBodies: unknown[] = []

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) postedBodies.push(JSON.parse(String(init.body)))
      if (url === '/api/v1/plugins?project_id=1&compact=true') {
        return json([
          {
            id: 47,
            slug: 'linear',
            name: 'Linear',
            version: '0.1.0',
            description: '',
            source: 'builtin',
            manifest_json: {},
            enabled_for_project: true,
            created_at: '2026-07-23T00:00:00Z',
            updated_at: '2026-07-23T00:00:00Z',
          },
        ])
      }
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse
      if (url === '/api/v1/auth/providers') return json([provider])
      if (url === '/api/v1/projects/1/auth/status') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [provider],
          connections: [connection],
        })
      }
      if (url === '/api/v1/projects/1/auth/credentials/cred_linear') {
        return json({
          connection,
          values: {},
          secret_present: { client_id: true, client_secret: true },
        })
      }
      if (url === '/api/v1/projects/1/auth/test') {
        return json({
          data: {
            credential_ref: 'cred_linear',
            provider_key: 'linear',
            ok: true,
            status: 'ok',
            summary: 'Linear workspace verified as Acme Workspace.',
            checked_at: '2026-07-23T00:00:00Z',
            retryable: false,
            next_action: null,
            metadata: {
              organization_ref: 'provider-object:organization-safe',
              organization_name: 'Acme Workspace',
            },
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
    await vi.waitFor(() => expect(wrapper.text()).toContain('Capability readiness'))

    expect(
      wrapper.get('img[src="/images/integrations/linear-logo-white.png"]').attributes('src'),
    ).toBe('/images/integrations/linear-logo-white.png')
    expect(wrapper.text()).toContain('Acme Workspace')
    expect(wrapper.text()).toContain('Issue Work')
    expect(wrapper.find('[data-kind="readiness"]').text()).toContain('Ready')
    expect(wrapper.text()).toContain('expires')
    expect(wrapper.text()).toContain('Connect with Linear')

    await clickButton(wrapper, 'Test')
    await vi.waitFor(() =>
      expect(wrapper.text()).toContain('Linear workspace verified as Acme Workspace.'),
    )
    expect(postedBodies).toContainEqual({ credential_ref: 'cred_linear' })

    await clickButton(wrapper, 'Edit')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Reconnect guidance'))
    expect(wrapper.text()).toContain('Reconnect if authorization is expired or missing scope')
    expect(wrapper.text()).toContain(CALLBACK_URL)
    expect(wrapper.findAll('button').some((button) => button.text().trim() === 'Reconnect')).toBe(
      true,
    )
    expect(wrapper.findAll('button').some((button) => button.text().trim() === 'Revoke')).toBe(true)
  })
})
