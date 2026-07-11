import { computed, ref, type ComputedRef, type Ref } from 'vue'

import { formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'

import { connectionTitle, parseCsv, slackIdentified, slackProfileAuthKey } from './formatters'
import { buildSlackProfilePayload, slackProfileNeedsTestedConnection } from './profilePayloads'
import type {
  CommunicationProfile,
  ConnectionRow,
  MessageTone,
  SlackProfileForm,
} from './types'

interface SlackProfileEditorOptions {
  projectId: ComputedRef<number>
  profiles: Ref<CommunicationProfile[]>
  connectedConnections: ComputedRef<ConnectionRow[]>
  busyAction: Ref<string | null>
  reload: () => Promise<void>
}

export function useSlackProfileEditor(options: SlackProfileEditorOptions) {
  const panelOpen = ref(false)
  const message = ref<{ tone: MessageTone; text: string } | null>(null)
  const isNew = ref(false)
  const form = ref<SlackProfileForm>(emptyForm())

  const connections = computed(() =>
    options.connectedConnections.value.filter(
      (connection) => connection.provider_key === 'slack-bot',
    ),
  )
  const connectionOptions = computed(() =>
    connections.value.map((connection) => ({
      value: connection.profile_key,
      label: `${connectionTitle(connection)} (${connection.profile_key})`,
    })),
  )
  const teamLabel = computed(() => {
    const connection = connections.value.find(
      (item) => item.profile_key === form.value.auth_profile_key,
    )
    const meta = connection?.account?.metadata_json as Record<string, unknown> | undefined
    const team = meta?.team ?? meta?.team_id
    return typeof team === 'string' ? team : ''
  })

  function openAdd(): void {
    form.value = {
      ...emptyForm(),
      key: 'slack-bot',
      auth_profile_key: connections.value[0]?.profile_key ?? '',
      identity_display_name: 'Slack Bot',
      identity_voice: 'Clear, concise, and operational.',
    }
    isNew.value = true
    message.value = null
    panelOpen.value = true
  }

  function edit(profile: CommunicationProfile): void {
    const access = profile.access_policy
    const rawMentions = profile.trigger_policy['mention_patterns']
    form.value = {
      key: profile.key,
      auth_profile_key: slackProfileAuthKey(profile),
      identity_display_name: String(profile.identity.display_name ?? profile.key),
      identity_purpose: String(profile.identity.purpose ?? ''),
      identity_voice: String(profile.identity.voice ?? ''),
      agent_default_instructions: String(profile.agent_guidance.default_instructions ?? ''),
      agent_boundaries: String(profile.agent_guidance.boundaries ?? ''),
      agent_escalation: String(profile.agent_guidance.escalation ?? ''),
      allowed_chat_refs: (access.allowed_surface_refs ?? []).join(', '),
      allowed_user_refs: (access.allowed_user_refs ?? []).join(', '),
      mention_patterns: (Array.isArray(rawMentions) ? (rawMentions as string[]) : []).join(', '),
    }
    isNew.value = false
    message.value = null
    panelOpen.value = true
  }

  async function save(): Promise<void> {
    const values = form.value
    const key = values.key.trim()
    const authProfileKey = values.auth_profile_key.trim()
    const displayName = values.identity_display_name.trim()
    const allowedUserRefs = parseCsv(values.allowed_user_refs)
    const allowedSurfaceRefs = parseCsv(values.allowed_chat_refs)
    if (!key) return fail('Bot key is required.')
    if (!displayName) return fail('Bot display name is required.')
    if (!authProfileKey) return fail('Choose a Slack connection.')
    if (allowedUserRefs.length === 0) {
      return fail('Allowlisted users are required before the bot can trigger agents.')
    }
    const existing = options.profiles.value.find((profile) => profile.key === key) ?? null
    const connection =
      connections.value.find((item) => item.profile_key === authProfileKey) ?? null
    if (slackProfileNeedsTestedConnection(existing, authProfileKey) && !slackIdentified(connection)) {
      return fail(
        'Test the Slack connection first so StackOS can fetch the workspace identity from Slack.',
      )
    }
    options.busyAction.value = 'slack-profile:save'
    try {
      await callOperation(
        'communicationProfile.upsert',
        buildSlackProfilePayload({
          projectId: options.projectId.value,
          existing,
          selectedConnection: connection,
          key,
          authProfileKey,
          displayName,
          identityPurpose: values.identity_purpose.trim(),
          identityVoice: values.identity_voice.trim(),
          agentDefaultInstructions: values.agent_default_instructions.trim(),
          agentBoundaries: values.agent_boundaries.trim(),
          agentEscalation: values.agent_escalation.trim(),
          allowedUserRefs,
          allowedSurfaceRefs,
          mentionPatterns: parseCsv(values.mention_patterns),
        }),
      )
      message.value = { tone: 'success', text: `Saved ${key}.` }
      panelOpen.value = false
      await options.reload()
    } catch (err) {
      message.value = {
        tone: 'danger',
        text: formatApiError(err, 'failed to save Slack bot'),
      }
    } finally {
      options.busyAction.value = null
    }
  }

  function fail(text: string): void {
    message.value = { tone: 'danger', text }
  }

  function emptyForm(): SlackProfileForm {
    return {
      key: '',
      auth_profile_key: '',
      identity_display_name: '',
      identity_purpose: '',
      identity_voice: '',
      agent_default_instructions: '',
      agent_boundaries: '',
      agent_escalation: '',
      allowed_chat_refs: '',
      allowed_user_refs: '',
      mention_patterns: '',
    }
  }

  return {
    panelOpen,
    message,
    isNew,
    form,
    connections,
    connectionOptions,
    teamLabel,
    openAdd,
    edit,
    save,
  }
}
