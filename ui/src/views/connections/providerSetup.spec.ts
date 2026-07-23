import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { SchemaAuthProviderOut } from '@/api'

import ConnectionProviderSetupGuidance from './ConnectionProviderSetupGuidance.vue'
import { providerSetupGuidance, safeHttpsUrl } from './providerSetup'

const provider = {
  id: 1,
  plugin_id: 1,
  plugin_slug: 'gtm',
  key: 'oauth-provider',
  name: 'OAuth Provider',
  description: '',
  auth_type: 'oauth',
  scopes: [],
  config_json: {
    setup: {
      setup_note: 'Register an OAuth app before connecting.',
      local_setup_label: 'Connect the provider in StackOS',
      local_setup_note: 'Select optional capabilities before connecting.',
      callback_url: 'https://auth.stackos.example/api/v1/auth/oauth/callback',
      callback_note: 'The relay returns the browser to the local Connections view.',
      repair_note: 'Reconnect and verify the granted scopes.',
      console_url: 'https://provider.example/developer/apps',
      credential_url: 'https://provider.example/docs/oauth',
      docs_url: 'https://provider.example/docs/api',
      support_url: 'http://unsafe.example/scopes',
      fallback_url: 'javascript:alert(1)',
      verified_at: '2026-07-22',
      url_confidence: { console_url: 'directional', credential_url: 'verified' },
    },
  },
} as SchemaAuthProviderOut

describe('provider setup guidance', () => {
  it('reads the nested public setup contract and rejects unsafe links', () => {
    const guidance = providerSetupGuidance(provider)

    expect(guidance).toMatchObject({
      setupNote: 'Register an OAuth app before connecting.',
      callbackUrl: 'https://auth.stackos.example/api/v1/auth/oauth/callback',
    })
    expect(guidance?.links.map((link) => link.label)).toEqual([
      'Provider console',
      'OAuth and credential guide',
      'API documentation',
    ])
    expect(guidance?.links[0].directional).toBe(true)
    expect(safeHttpsUrl('https://user:secret@provider.example/app')).toBeNull()
    expect(safeHttpsUrl('http://provider.example/app')).toBeNull()
  })

  it('renders the exact callback, official keyboard-reachable links, and reconnect guidance', async () => {
    const wrapper = mount(ConnectionProviderSetupGuidance, {
      props: { provider, editing: false },
    })

    expect(wrapper.get('section').attributes('aria-label')).toBe('OAuth Provider setup guidance')
    expect(wrapper.text()).toContain('https://auth.stackos.example/api/v1/auth/oauth/callback')
    expect(wrapper.text()).toContain('closest official route')
    expect(wrapper.text()).not.toContain('unsafe.example')
    expect(wrapper.text()).not.toContain('Reconnect guidance')
    const links = wrapper.findAll('a')
    expect(links).toHaveLength(3)
    for (const link of links) {
      expect(link.attributes('href')).toMatch(/^https:\/\//)
      expect(link.attributes('target')).toBe('_blank')
      expect(link.attributes('rel')).toBe('noopener noreferrer')
    }

    await wrapper.setProps({ editing: true })
    expect(wrapper.text()).toContain('Reconnect guidance')
    expect(wrapper.text()).toContain('Reconnect and verify the granted scopes.')
  })
})
