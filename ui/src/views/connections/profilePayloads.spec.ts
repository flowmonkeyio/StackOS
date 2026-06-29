import { describe, expect, it } from 'vitest'

import {
  buildSlackProfilePayload,
  buildTelegramProfilePayload,
  slackProfileNeedsTestedConnection,
} from './profilePayloads'
import type { CommunicationProfile, ConnectionRow } from './types'

const baseProfile = (partial: Partial<CommunicationProfile>): CommunicationProfile =>
  ({
    record_id: 1,
    project_id: 1,
    profile_ref: 'communication-profile:ops',
    key: 'ops',
    enabled: true,
    identity: {},
    agent_guidance: {},
    provider_facets: {},
    access_policy: {},
    trigger_policy: {},
    visibility_policy: {},
    context_policy: {},
    response_policy: {},
    send_policy: {},
    handoff_policy: {},
    approval_policy: {},
    metadata_json: {},
    ...partial,
  }) as CommunicationProfile

const slackConnection = (profileKey: string, teamId: string): ConnectionRow =>
  ({
    profile_key: profileKey,
    account: {
      metadata_json: {
        team_id: teamId,
        team: `Team ${teamId}`,
        user_id: `U_${teamId}`,
        bot_id: `B_${teamId}`,
      },
    },
  }) as unknown as ConnectionRow

describe('connection profile payloads', () => {
  it('preserves non-Telegram facets and policies while building a Telegram profile payload', () => {
    const existing = baseProfile({
      provider_facets: {
        'telegram-bot': { auth_profile_key: 'old', ingress_mode: 'polling' },
        'slack-bot': { auth_profile_key: 'slack', bot_user_id: 'U1' },
      },
      access_policy: { denied_user_refs: ['telegram-user:denied'] },
      context_policy: { include_last_messages: 10 },
      send_policy: { mode: 'explicit-targets' },
    })

    const payload = buildTelegramProfilePayload({
      projectId: 1,
      existing,
      key: 'ops',
      authProfileKey: 'telegram-primary',
      botUsername: 'ops_bot',
      identityDisplayName: 'Ops Bot',
      identityPurpose: 'Route operational requests.',
      identityVoice: 'Brief.',
      agentDefaultInstructions: 'Triage first.',
      agentBoundaries: 'No billing.',
      agentEscalation: 'Escalate incidents.',
      allowedChatRefs: ['telegram-chat:1'],
      allowedUserRefs: ['telegram-user:1'],
      commands: [{ command: '/ops', guidance: 'Triage.' }],
      mentionPatterns: ['ops'],
      storeNonTriggerMessages: true,
      originRequired: true,
      replyToSourceMessage: true,
      sameThread: true,
    })

    expect(payload).toMatchObject({
      provider_facets: {
        'telegram-bot': {
          auth_profile_key: 'telegram-primary',
          bot_username: 'ops_bot',
          ingress_mode: 'polling',
        },
        'slack-bot': { auth_profile_key: 'slack', bot_user_id: 'U1' },
      },
      access_policy: {
        denied_user_refs: ['telegram-user:denied'],
        allowed_chat_refs: ['telegram-chat:1'],
        allowed_user_refs: ['telegram-user:1'],
      },
      context_policy: { include_last_messages: 10 },
      send_policy: { mode: 'explicit-targets' },
    })
  })

  it('uses the selected tested Slack connection when an existing bot changes connections', () => {
    const existing = baseProfile({
      provider_facets: {
        'slack-bot': {
          auth_profile_key: 'old',
          team_id: 'T_OLD',
          team_name: 'Old team',
          bot_user_id: 'U_OLD',
        },
      },
      access_policy: { denied_user_refs: ['slack-user:blocked'] },
    })

    const payload = buildSlackProfilePayload({
      projectId: 1,
      existing,
      selectedConnection: slackConnection('new', 'T_NEW'),
      key: 'ops',
      authProfileKey: 'new',
      displayName: 'Ops Slack',
      identityPurpose: 'Ops requests.',
      identityVoice: 'Clear.',
      agentDefaultInstructions: 'Inspect first.',
      agentBoundaries: 'No destructive changes.',
      agentEscalation: 'Escalate incidents.',
      allowedUserRefs: ['slack-user:U1'],
      allowedSurfaceRefs: ['slack-channel:C1'],
      mentionPatterns: ['ops'],
    })

    expect(slackProfileNeedsTestedConnection(existing, 'new')).toBe(true)
    expect(payload).toMatchObject({
      provider_facets: {
        'slack-bot': {
          auth_profile_key: 'new',
          team_id: 'T_NEW',
          team_name: 'Team T_NEW',
          bot_user_id: 'U_T_NEW',
          bot_id: 'B_T_NEW',
        },
      },
      access_policy: {
        denied_user_refs: ['slack-user:blocked'],
        allowed_user_refs: ['slack-user:U1'],
        allowed_surface_refs: ['slack-channel:C1'],
      },
    })
    expect(JSON.stringify(payload)).not.toContain('T_OLD')
  })

  it('preserves an existing Slack facet when the linked connection is unchanged', () => {
    const existing = baseProfile({
      provider_facets: {
        'slack-bot': {
          auth_profile_key: 'same',
          team_id: 'T_SAME',
          ingress_path: '/api/v1/ingress/slack/1/ops',
        },
      },
    })

    const payload = buildSlackProfilePayload({
      projectId: 1,
      existing,
      selectedConnection: null,
      key: 'ops',
      authProfileKey: 'same',
      displayName: 'Ops Slack',
      identityPurpose: '',
      identityVoice: '',
      agentDefaultInstructions: '',
      agentBoundaries: '',
      agentEscalation: '',
      allowedUserRefs: ['slack-user:U1'],
      allowedSurfaceRefs: [],
      mentionPatterns: [],
    })

    expect(slackProfileNeedsTestedConnection(existing, 'same')).toBe(false)
    expect(payload).toMatchObject({
      provider_facets: {
        'slack-bot': {
          auth_profile_key: 'same',
          team_id: 'T_SAME',
          ingress_path: '/api/v1/ingress/slack/1/ops',
        },
      },
    })
  })
})
