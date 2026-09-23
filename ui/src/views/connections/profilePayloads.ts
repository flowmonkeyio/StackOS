import {
  slackFacet,
  slackFacetFromConnection,
  slackProfileCredentialRef,
} from './formatters'
import type { CommunicationProfile, ConnectionRow } from './types'

export interface SlackProfilePayloadInput {
  projectId: number
  existing: CommunicationProfile | null
  selectedConnection: ConnectionRow | null
  key: string
  credentialRef: string
  displayName: string
  identityPurpose: string
  identityVoice: string
  agentDefaultInstructions: string
  agentBoundaries: string
  agentEscalation: string
  allowedUserRefs: string[]
  allowedSurfaceRefs: string[]
  mentionPatterns: string[]
  ingressEnabled: boolean
}

export function slackProfileNeedsTestedConnection(
  existing: CommunicationProfile | null,
  credentialRef: string,
): boolean {
  return !existing || slackProfileCredentialRef(existing) !== credentialRef
}

export function buildSlackProfilePayload(input: SlackProfilePayloadInput): Record<string, unknown> {
  const useSelectedConnection = slackProfileNeedsTestedConnection(
    input.existing,
    input.credentialRef,
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
      'slack-bot': {
        ...baseFacet,
        credential_ref: input.credentialRef,
        ingress_enabled: input.ingressEnabled,
      },
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
