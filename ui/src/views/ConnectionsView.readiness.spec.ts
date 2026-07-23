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

const coreScopes = ['crm.contacts.read', 'crm.contacts.write']
const providerConfig = {
  scope_bundles: {
    sales: { optional_scopes: ['crm.leads.read', 'crm.leads.write'] },
    marketing: { optional_scopes: ['forms'] },
    bulk: { optional_scopes: ['crm.export'] },
    webhooks: { optional_scopes: [] },
    automation: { optional_scopes: ['automation'] },
    transactional: { optional_scopes: ['transactional-email'] },
  },
  readiness_groups: {
    'crm-core': { label: 'CRM Core', required_scopes: coreScopes },
    sales: { label: 'Sales', source_bundle: 'sales' },
    marketing: { label: 'Marketing', source_bundle: 'marketing' },
    bulk: { label: 'Bulk', source_bundle: 'bulk' },
    webhooks: {
      label: 'Webhooks',
      source_bundle: 'webhooks',
      prerequisites: ['oauth-app', 'public-ingress', 'signature-validation'],
    },
    automation: {
      label: 'Custom Workflow Automation',
      source_bundle: 'automation',
      prerequisites: ['oauth-app', 'public-ingress', 'signature-validation'],
    },
    transactional: {
      label: 'Transactional Communications',
      source_bundle: 'transactional',
      prerequisites: ['transactional-email-addon', 'consent-policy'],
    },
  },
  setup: {
    setup_note: 'Register the OAuth app and grant the selected capability bundles.',
    local_setup_label: 'Connect CRM in StackOS',
    local_setup_note: 'Reconnect after selecting more optional capabilities.',
    console_url: 'https://provider.example/developer/apps',
    credential_url: 'https://provider.example/docs/oauth',
    callback_url: 'https://auth.stackos.example/api/v1/auth/oauth/callback',
    callback_note: 'The HTTPS relay returns the browser to the local Connections view.',
    repair_note: 'Reconnect for missing scopes and verify provider entitlements.',
  },
}

describe('ConnectionsView provider readiness', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('renders manifest-driven capabilities and setup guidance without provider-specific UI', async () => {
    const provider = authProvider(
      'crm-provider',
      'CRM Provider',
      'oauth',
      interactiveMethod(),
      providerConfig,
    )
    provider.plugin_slug = 'gtm'

    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url === '/api/v1/plugins?project_id=1&compact=true') {
        return json([
          {
            id: 1,
            slug: 'gtm',
            name: 'GTM',
            version: '0.1.0',
            description: '',
            source: 'builtin',
            manifest_json: {},
            enabled_for_project: true,
            created_at: '2026-07-22T00:00:00Z',
            updated_at: '2026-07-22T00:00:00Z',
          },
        ])
      }
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse
      if (url === '/api/v1/auth/providers') return json([provider])
      if (url === '/api/v1/projects/1/auth/credentials/cred_crm_provider') {
        return json({
          connection: authConnection({
            revokedAt: null,
            providerKey: 'crm-provider',
            credentialRef: 'cred_crm_provider',
            authType: 'oauth',
            authMethodKey: 'oauth2',
            label: 'Primary CRM',
            scopes: [...coreScopes, 'crm.leads.read', 'automation'],
          }),
          values: {},
          secret_present: { client_id: true, client_secret: true },
        })
      }
      if (url === '/api/v1/projects/1/auth/status') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [provider],
          connections: [
            authConnection({
              revokedAt: null,
              providerKey: 'crm-provider',
              credentialRef: 'cred_crm_provider',
              authType: 'oauth',
              authMethodKey: 'oauth2',
              label: 'Primary CRM',
              scopes: [...coreScopes, 'crm.leads.read', 'automation'],
            }),
          ],
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

    for (const label of [
      'CRM Core',
      'Sales',
      'Marketing',
      'Bulk',
      'Webhooks',
      'Custom Workflow Automation',
      'Transactional Communications',
    ]) {
      expect(wrapper.text()).toContain(label)
    }
    const states = wrapper.findAll('[data-kind="readiness"]').map((badge) => badge.text().trim())
    expect(states).toEqual([
      'Ready',
      'Missing scopes',
      'Not enabled',
      'Not enabled',
      'Operator checklist',
      'Operator checklist',
      'Not enabled',
    ])
    expect(wrapper.text()).toContain('OAuth App, Public Ingress, Signature Validation')

    await clickButton(wrapper, 'Add connection')
    await vi.waitFor(() =>
      expect(wrapper.text()).toContain('https://auth.stackos.example/api/v1/auth/oauth/callback'),
    )
    expect(
      wrapper.get('a[href="https://provider.example/developer/apps"]').attributes('target'),
    ).toBe('_blank')
    expect(wrapper.text()).toContain('Reconnect after selecting more optional capabilities.')

    await clickButton(wrapper, 'Cancel')
    await clickButton(wrapper, 'Edit')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Reconnect guidance'))
    expect(wrapper.text()).toContain(
      'Reconnect for missing scopes and verify provider entitlements.',
    )
    expect(wrapper.findAll('button').some((button) => button.text().trim() === 'Reconnect')).toBe(
      true,
    )
  })
})
