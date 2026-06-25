import {
  slackFacet,
  slackFacetFromConnection,
  slackProfileAuthKey,
  telegramFacet,
  telegramProfileIngressMode,
} from './formatters'
import type {
  CommunicationProfile,
  ConnectionRow,
  TelegramCommandSpec,
} from './types'

export interface TelegramProfilePayloadInput {
  projectId: number
  existing: CommunicationProfile | null
  key: string
  authProfileKey: string
  botUsername: string
  identityDisplayName: string
  identityPurpose: string
  identityVoice: string
  agentDefaultInstructions: string
  agentBoundaries: string
  agentEscalation: string
  allowedChatRefs: string[]
  allowedUserRefs: string[]
  commands: TelegramCommandSpec[]
  mentionPatterns: string[]
  storeNonTriggerMessages: boolean
  originRequired: boolean
  replyToSourceMessage: boolean
  sameThread: boolean
}

export interface SlackProfilePayloadInput {
  projectId: number
  existing: CommunicationProfile | null
  selectedConnection: ConnectionRow | null
  key: string
  authProfileKey: string
  displayName: string
  identityPurpose: string
  identityVoice: string
  agentDefaultInstructions: string
  agentBoundaries: string
  agentEscalation: string
  allowedUserRefs: string[]
  allowedSurfaceRefs: string[]
  mentionPatterns: string[]
}

export function buildTelegramProfilePayload(input: TelegramProfilePayloadInput): Record<string, unknown> {
  const existingFacets = input.existing?.provider_facets ?? {}
  const existingTelegramFacet = input.existing ? telegramFacet(input.existing) : {}
  const existingIngressMode = input.existing ? telegramProfileIngressMode(input.existing) : ''

  return {
    project_id: input.projectId,
    key: input.key,
    identity: {
      ...(input.existing?.identity ?? {}),
      display_name: input.identityDisplayName,
      purpose: input.identityPurpose,
      voice: input.identityVoice,
    },
    provider_facets: {
      ...existingFacets,
      'telegram-bot': {
        ...existingTelegramFacet,
        auth_profile_key: input.authProfileKey,
        bot_username: input.botUsername,
        ingress_mode:
          existingIngressMode && existingIngressMode !== 'not configured'
            ? existingIngressMode
            : 'webhook',
        allowed_updates: Array.isArray(existingTelegramFacet.allowed_updates)
          ? existingTelegramFacet.allowed_updates
          : ['message', 'callback_query'],
      },
    },
    agent_guidance: {
      ...(input.existing?.agent_guidance ?? {}),
      default_instructions: input.agentDefaultInstructions,
      boundaries: input.agentBoundaries,
      escalation: input.agentEscalation,
    },
    access_policy: {
      ...(input.existing?.access_policy ?? {}),
      dm_mode: 'all',
      group_mode: 'all',
      user_mode: 'allowlist',
      allowed_chat_refs: input.allowedChatRefs,
      allowed_user_refs: input.allowedUserRefs,
    },
    trigger_policy: {
      ...(input.existing?.trigger_policy ?? {}),
      dm_trigger: 'always',
      group_trigger: 'mention_or_command',
      commands: input.commands,
      mention_patterns: input.mentionPatterns,
      reply_to_bot_triggers: true,
    },
    visibility_policy: {
      ...(input.existing?.visibility_policy ?? {}),
      store_non_trigger_messages: input.storeNonTriggerMessages,
    },
    context_policy: input.existing?.context_policy ?? {},
    response_policy: {
      ...(input.existing?.response_policy ?? {}),
      reply_in_same_chat: true,
      origin_required: input.originRequired,
      reply_to_source_message: input.replyToSourceMessage,
      same_thread: input.sameThread,
    },
    send_policy: input.existing?.send_policy ?? { mode: 'explicit-targets' },
    handoff_policy: input.existing?.handoff_policy ?? { mode: 'explicit-targets' },
    approval_policy: input.existing?.approval_policy ?? { mode: 'none' },
    metadata_json: input.existing?.metadata_json ?? {},
  }
}

export function slackProfileNeedsTestedConnection(
  existing: CommunicationProfile | null,
  authProfileKey: string,
): boolean {
  return !existing || slackProfileAuthKey(existing) !== authProfileKey
}

export function buildSlackProfilePayload(input: SlackProfilePayloadInput): Record<string, unknown> {
  const useSelectedConnection = slackProfileNeedsTestedConnection(
    input.existing,
    input.authProfileKey,
  )
  const baseFacet = useSelectedConnection
    ? slackFacetFromConnection(input.selectedConnection)
    : input.existing
      ? slackFacet(input.existing)
      : {}
  const accessPolicy = input.existing
    ? {
        ...input.existing.access_policy,
        user_mode: 'allowlist',
        allowed_user_refs: input.allowedUserRefs,
        allowed_surface_refs: input.allowedSurfaceRefs,
      }
    : {
        dm_mode: 'all',
        channel_mode: 'all',
        group_mode: 'all',
        user_mode: 'allowlist',
        allowed_user_refs: input.allowedUserRefs,
        allowed_surface_refs: input.allowedSurfaceRefs,
      }
  const triggerPolicy = input.existing
    ? { ...input.existing.trigger_policy, mention_patterns: input.mentionPatterns }
    : {
        dm_trigger: 'always',
        channel_trigger: 'mention_or_command',
        mention_patterns: input.mentionPatterns,
        reply_to_bot_triggers: true,
      }

  return {
    project_id: input.projectId,
    key: input.key,
    identity: {
      ...(input.existing?.identity ?? {}),
      display_name: input.displayName,
      purpose: input.identityPurpose,
      voice: input.identityVoice,
    },
    provider_facets: {
      ...(input.existing?.provider_facets ?? {}),
      'slack-bot': { ...baseFacet, auth_profile_key: input.authProfileKey },
    },
    agent_guidance: {
      ...(input.existing?.agent_guidance ?? {}),
      default_instructions: input.agentDefaultInstructions,
      boundaries: input.agentBoundaries,
      escalation: input.agentEscalation,
    },
    access_policy: accessPolicy,
    trigger_policy: triggerPolicy,
    visibility_policy: input.existing?.visibility_policy ?? {},
    context_policy: input.existing?.context_policy ?? {},
    response_policy: input.existing?.response_policy ?? {},
    send_policy: input.existing?.send_policy ?? { mode: 'explicit-targets' },
    handoff_policy: input.existing?.handoff_policy ?? { mode: 'explicit-targets' },
    approval_policy: input.existing?.approval_policy ?? { mode: 'none' },
    metadata_json: input.existing?.metadata_json ?? {},
  }
}
