import { describe, expect, it } from 'vitest'

import type { SchemaAuthProviderOut, SchemaCredentialConnectionOut } from '@/api'

import { humanizeIdentifier, providerCapabilityReadiness } from './providerReadiness'

const coreScopes = ['crm.contacts.read', 'crm.contacts.write']

const provider = {
  id: 1,
  plugin_id: 1,
  plugin_slug: 'gtm',
  key: 'crm-provider',
  name: 'CRM Provider',
  description: '',
  auth_type: 'oauth',
  scopes: coreScopes,
  config_json: {
    scope_bundles: {
      sales: { optional_scopes: ['crm.leads.read', 'crm.leads.write'] },
      marketing: { optional_scopes: ['forms', 'campaigns.read'] },
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
        prerequisites: ['oauth-app', 'public-ingress'],
      },
      automation: {
        label: 'Custom Workflow Automation',
        source_bundle: 'automation',
        prerequisites: ['oauth-app', 'public-ingress'],
      },
      transactional: {
        label: 'Transactional Communications',
        source_bundle: 'transactional',
        prerequisites: ['transactional-email-addon', 'consent-policy'],
      },
    },
  },
} as SchemaAuthProviderOut

function connection(
  scopes: string[],
  overrides: Partial<SchemaCredentialConnectionOut> = {},
): SchemaCredentialConnectionOut {
  return {
    credential_ref: 'cred_crm',
    project_id: 1,
    provider_key: 'crm-provider',
    auth_type: 'oauth',
    auth_method_key: 'oauth2',
    profile_key: 'default',
    label: 'Primary CRM',
    status: 'connected',
    expires_at: null,
    last_tested_at: null,
    revoked_at: null,
    scopes,
    account: null,
    setup_required: false,
    ...overrides,
  }
}

describe('provider capability readiness', () => {
  it('derives all declared groups from actual granted scopes', () => {
    const rows = providerCapabilityReadiness(provider, connection(coreScopes))

    expect(rows.map((row) => row.label)).toEqual([
      'CRM Core',
      'Sales',
      'Marketing',
      'Bulk',
      'Webhooks',
      'Custom Workflow Automation',
      'Transactional Communications',
    ])
    expect(rows[0]).toMatchObject({ state: 'ready', missingScopes: [] })
    expect(rows.find((row) => row.key === 'webhooks')).toMatchObject({
      state: 'operator-checklist',
      missingScopes: [],
    })
    expect(
      rows
        .filter((row) => !['crm-core', 'webhooks'].includes(row.key))
        .every((row) => row.state === 'not-enabled'),
    ).toBe(true)
    expect(rows.find((row) => row.key === 'bulk')?.missingScopes).toEqual(['crm.export'])
  })

  it('distinguishes partial scopes from an optional bundle that was never enabled', () => {
    const rows = providerCapabilityReadiness(
      provider,
      connection([...coreScopes, 'crm.leads.read']),
    )
    const sales = rows.find((row) => row.key === 'sales')

    expect(sales).toMatchObject({ state: 'missing-scopes' })
    expect(sales?.missingScopes).toEqual(['crm.leads.write'])
    expect(sales?.summary).toContain('1 of 2')
  })

  it('requires explicit non-scope prerequisites after scopes are granted', () => {
    const rows = providerCapabilityReadiness(
      provider,
      connection([...coreScopes, 'automation', 'transactional-email']),
    )

    expect(rows.find((row) => row.key === 'webhooks')).toMatchObject({
      state: 'operator-checklist',
      prerequisites: ['oauth-app', 'public-ingress'],
    })
    expect(rows.find((row) => row.key === 'automation')).toMatchObject({
      state: 'operator-checklist',
      prerequisites: ['oauth-app', 'public-ingress'],
    })
    expect(rows.find((row) => row.key === 'transactional')).toMatchObject({
      state: 'operator-checklist',
    })
  })

  it('lets connection repair and pending authorization override scope conclusions', () => {
    const allScopes = [
      ...coreScopes,
      'crm.leads.read',
      'crm.leads.write',
      'forms',
      'campaigns.read',
      'crm.export',
      'automation',
      'transactional-email',
    ]
    const failed = providerCapabilityReadiness(
      provider,
      connection(allScopes, { status: 'failed', setup_required: true }),
    )
    const pending = providerCapabilityReadiness(
      provider,
      connection(allScopes, { status: 'pending' }),
    )

    expect(failed.every((row) => row.state === 'connection-repair')).toBe(true)
    expect(pending.every((row) => row.state === 'pending')).toBe(true)
  })

  it('uses readable prerequisite labels and stays absent without a manifest contract', () => {
    expect(humanizeIdentifier('transactional-email-addon')).toBe('Transactional Email Addon')
    expect(
      providerCapabilityReadiness({ ...provider, config_json: {} }, connection(coreScopes)),
    ).toEqual([])
  })
})
