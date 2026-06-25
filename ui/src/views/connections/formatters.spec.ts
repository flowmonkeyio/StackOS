import { describe, expect, it } from 'vitest'

import {
  channelKindLabel,
  commandSummary,
  connectionAttentionTone,
  connectionNeedsAttention,
  connectionStatusKey,
  parseCsv,
  pluginLabel,
  profileAudienceMeta,
  profilePrimaryProvider,
  providerLabel,
  routeFieldSummary,
  routePurpose,
  sensitivityMeta,
  slackFacetFromConnection,
  slackIdentified,
  surfaceAudienceLabel,
  targetPolicySummary,
  toCommandSpecs,
} from './formatters'
import type {
  CommunicationProfile,
  CommunicationRoute,
  CommunicationSurface,
  CommunicationTarget,
  ConnectionRow,
} from './types'

const surface = (partial: Partial<CommunicationSurface>): CommunicationSurface =>
  ({ kind: 'slack-channel', audience: '', data_scope: {}, ...partial }) as CommunicationSurface

describe('connections formatters', () => {
  it('humanizes provider keys', () => {
    expect(providerLabel('telegram-bot')).toBe('Telegram')
    expect(providerLabel('slack-bot')).toBe('Slack')
    expect(providerLabel('smtp')).toBe('Email (SMTP)')
    expect(providerLabel(null)).toBe('Unknown')
    expect(providerLabel('mystery-bot')).toBe('Mystery')
  })

  it('humanizes plugin slugs', () => {
    expect(pluginLabel('seo')).toBe('SEO')
    expect(pluginLabel('media-buying')).toBe('Media Buying')
    expect(pluginLabel(null)).toBe('StackOS')
  })

  it('humanizes channel kinds and audience', () => {
    expect(channelKindLabel(surface({ kind: 'telegram-supergroup' }))).toBe('Telegram group')
    expect(channelKindLabel(surface({ kind: 'slack-channel' }))).toBe('Slack channel')
    expect(channelKindLabel(surface({ kind: 'novel-kind' }))).toBe('Novel Kind')
    expect(surfaceAudienceLabel(surface({ audience: 'customer' }))).toBe('Customer')
    expect(surfaceAudienceLabel(surface({ audience: '' }))).toBe('Unknown audience')
  })

  it('reads data_scope.classification as plain-language sensitivity', () => {
    const confidential = sensitivityMeta(surface({ data_scope: { classification: 'customer-confidential' } }))
    expect(confidential.label).toBe('Customer-confidential')
    expect(confidential.tone).toBe('warning')
    expect(sensitivityMeta(surface({ data_scope: { classification: 'internal' } })).tone).toBe('info')
    expect(sensitivityMeta(surface({ data_scope: {} })).label).toBe('Not set')
  })

  it('derives Slack identity from a tested connection', () => {
    const tested = {
      account: { metadata_json: { team_id: 'T1', team: 'Acme', user_id: 'U1', bot_id: 'B1' } },
    } as unknown as ConnectionRow
    expect(slackIdentified(tested)).toBe(true)
    expect(slackIdentified({ account: null } as unknown as ConnectionRow)).toBe(false)
    expect(slackIdentified(null)).toBe(false)
    expect(slackFacetFromConnection(tested)).toEqual({
      team_id: 'T1',
      team_name: 'Acme',
      bot_user_id: 'U1',
      bot_id: 'B1',
    })
  })

  it('normalizes connection attention state from raw auth status', () => {
    expect(connectionStatusKey({ status: 'connected', setup_required: true })).toBe('setup-required')
    expect(connectionNeedsAttention({ status: 'connected', setup_required: true })).toBe(true)
    expect(connectionNeedsAttention({ status: 'connected', setup_required: false })).toBe(false)
    expect(connectionAttentionTone({ status: 'failed' })).toBe('danger')
    expect(connectionAttentionTone({ status: 'pending' })).toBe('warning')
  })

  it('picks the first provider facet as the primary provider', () => {
    const profile = {
      provider_facets: { 'telegram-bot': {}, 'slack-bot': {} },
    } as unknown as CommunicationProfile
    expect(profilePrimaryProvider(profile)).toBe('slack-bot') // sorted
  })

  it('uses provider-appropriate audience counts for bot cards', () => {
    const telegram = {
      provider_facets: { 'telegram-bot': {} },
      access_policy: { allowed_chat_refs: ['telegram-chat:1', 'telegram-chat:2'] },
    } as unknown as CommunicationProfile
    const slack = {
      provider_facets: { 'slack-bot': {} },
      access_policy: { allowed_surface_refs: ['slack-channel:C1'] },
    } as unknown as CommunicationProfile
    expect(profileAudienceMeta(telegram)).toEqual({ label: 'Chats', count: 2 })
    expect(profileAudienceMeta(slack)).toEqual({ label: 'Channels', count: 1 })
  })

  it('summarizes target policy guards', () => {
    const target = (sp: Record<string, unknown>): CommunicationTarget =>
      ({ send_policy: sp }) as unknown as CommunicationTarget
    expect(targetPolicySummary(target({ requires_approval: true }))).toBe('approval required')
    expect(targetPolicySummary(target({ allowed_profile_refs: ['a', 'b'] }))).toBe('2 guards')
    expect(targetPolicySummary(target({}))).toBe('unscoped')
  })

  it('summarizes handoff route field policy and purpose', () => {
    const route = (partial: Partial<CommunicationRoute>): CommunicationRoute =>
      ({ field_policy: {}, metadata_json: {}, ...partial }) as CommunicationRoute
    expect(routeFieldSummary(route({ field_policy: { forward_text: true, forward_media: true } }))).toBe(
      'Text · Media',
    )
    expect(
      routeFieldSummary(route({ field_policy: { allowed_fields: ['a'], redact_fields: ['x', 'y'] } })),
    ).toBe('1 field · 2 redacted')
    expect(routeFieldSummary(route({ field_policy: {} }))).toBe('Default fields')
    expect(routePurpose(route({ metadata_json: { purpose: ' Forward feedback ' } }))).toBe(
      'Forward feedback',
    )
    expect(routePurpose(route({ metadata_json: {} }))).toBe('')
  })

  it('parses CSV refs and normalizes commands', () => {
    expect(parseCsv('a, b ,, c')).toEqual(['a', 'b', 'c'])
    const specs = toCommandSpecs([
      { command: 'support', description: 'd', guidance: 'g', enabled: true },
      { command: '', description: '', guidance: '', enabled: true },
    ])
    expect(specs).toEqual([{ command: '/support', description: 'd', guidance: 'g', enabled: true }])
    expect(commandSummary(specs)).toBe('/support')
  })
})
