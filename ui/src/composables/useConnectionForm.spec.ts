import { describe, expect, it } from 'vitest'

import type { SchemaAuthProviderOut } from '@/api'

import { useConnectionForm } from './useConnectionForm'

function provider(methods: NonNullable<SchemaAuthProviderOut['auth_methods']>): SchemaAuthProviderOut {
  return {
    id: 1,
    plugin_id: 1,
    plugin_slug: 'test',
    key: 'provider',
    name: 'Provider',
    description: '',
    auth_type: 'oauth',
    auth_methods: methods,
    scopes: [],
    config_json: {},
  }
}

describe('useConnectionForm', () => {
  it('auto-selects a single method but requires an explicit choice when several exist', () => {
    const form = useConnectionForm()
    const apiKey = {
      key: 'api_key',
      label: 'API key',
      auth_type: 'api-key',
      description: '',
      interactive: false,
      payload_format: 'json',
    }
    const oauth = {
      key: 'oauth2',
      label: 'OAuth',
      auth_type: 'oauth',
      description: '',
      interactive: true,
      payload_format: 'json',
    }

    expect(form.selectedMethodKey(provider([apiKey]))).toBe('api_key')
    expect(form.selectedMethod(provider([apiKey]))?.key).toBe('api_key')
    expect(form.selectedMethodKey(provider([apiKey, oauth]))).toBe('')
    expect(form.selectedMethod(provider([apiKey, oauth]))).toBeNull()
  })
})
