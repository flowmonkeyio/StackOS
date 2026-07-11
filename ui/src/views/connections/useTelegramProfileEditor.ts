import { computed, ref, type ComputedRef, type Ref } from 'vue'

import { formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'

import {
  botUsernameFromConnection,
  connectionTitle,
  parseCsv,
  preferredTelegramConnection,
  telegramConnectionForProfile,
  telegramFacet,
  telegramProfileAuthKey,
  telegramProfileUsername,
  toCommandDrafts,
  toCommandSpecs,
} from './formatters'
import { buildTelegramProfilePayload } from './profilePayloads'
import type {
  CommunicationProfile,
  ConnectionRow,
  MessageTone,
  TelegramCommandSpec,
  TelegramProfileForm,
} from './types'

interface TelegramProfileEditorOptions {
  projectId: ComputedRef<number>
  profiles: Ref<CommunicationProfile[]>
  connectedConnections: ComputedRef<ConnectionRow[]>
  busyAction: Ref<string | null>
  reload: () => Promise<void>
}

export function useTelegramProfileEditor(options: TelegramProfileEditorOptions) {
  const panelOpen = ref(false)
  const message = ref<{ tone: MessageTone; text: string } | null>(null)
  const form = ref<TelegramProfileForm>({
    key: 'ops-bot',
    auth_profile_key: '',
    bot_username: '',
    identity_display_name: 'Ops Bot',
    identity_purpose: '',
    identity_voice: 'Clear, concise, and operational.',
    agent_default_instructions: '',
    agent_boundaries: '',
    agent_escalation: '',
    allowed_chat_refs: '',
    allowed_user_refs: '',
    commands: [
      {
        command: '/ops',
        description: 'Handle approved operational requests.',
        guidance: 'Triage the request, inspect relevant context, and reply with the next clear step.',
        enabled: true,
      },
    ],
    mention_patterns: '',
    store_non_trigger_messages: true,
    origin_required: true,
    reply_to_source_message: true,
    same_thread: true,
  })

  const connections = computed(() =>
    options.connectedConnections.value.filter(
      (connection) => connection.provider_key === 'telegram-bot',
    ),
  )
  const identifiedConnections = computed(() =>
    connections.value.filter((connection) => botUsernameFromConnection(connection)),
  )
  const connectionOptions = computed(() =>
    connections.value.map((connection) => ({
      value: connection.profile_key,
      label: `${connectionTitle(connection)} (${connection.profile_key})`,
    })),
  )

  function openAdd(): void {
    const preferred = preferredTelegramConnection(
      identifiedConnections.value,
      connections.value,
    )
    if (!form.value.auth_profile_key && preferred) {
      form.value = { ...form.value, auth_profile_key: preferred.profile_key }
    }
    message.value = null
    panelOpen.value = true
  }

  function edit(profile: CommunicationProfile): void {
    const facet = telegramFacet(profile)
    const rawCommands = profile.trigger_policy['commands']
    const rawMentionPatterns = profile.trigger_policy['mention_patterns']
    const commands = Array.isArray(rawCommands) ? (rawCommands as TelegramCommandSpec[]) : []
    const mentionPatterns = Array.isArray(rawMentionPatterns)
      ? (rawMentionPatterns as string[])
      : []
    form.value = {
      key: profile.key,
      auth_profile_key: telegramProfileAuthKey(profile),
      bot_username: telegramProfileUsername(profile),
      identity_display_name: String(profile.identity.display_name ?? profile.key),
      identity_purpose: String(profile.identity.purpose ?? ''),
      identity_voice: String(profile.identity.voice ?? ''),
      agent_default_instructions: String(profile.agent_guidance.default_instructions ?? ''),
      agent_boundaries: String(profile.agent_guidance.boundaries ?? ''),
      agent_escalation: String(profile.agent_guidance.escalation ?? ''),
      allowed_chat_refs: (profile.access_policy.allowed_chat_refs ?? []).join(', '),
      allowed_user_refs: (profile.access_policy.allowed_user_refs ?? []).join(', '),
      commands: toCommandDrafts(commands),
      mention_patterns: mentionPatterns.join(', '),
      store_non_trigger_messages: profile.visibility_policy.store_non_trigger_messages !== false,
      origin_required: profile.response_policy.origin_required !== false,
      reply_to_source_message: profile.response_policy.reply_to_source_message === true,
      same_thread: profile.response_policy.same_thread === true,
    }
    if (typeof facet.bot_username === 'string' && !form.value.bot_username) {
      form.value.bot_username = facet.bot_username.replace(/^@/, '')
    }
    message.value = null
    panelOpen.value = true
  }

  function addCommand(): void {
    form.value.commands = [
      ...form.value.commands,
      { command: '', description: '', guidance: '', enabled: true },
    ]
  }

  function removeCommand(index: number): void {
    form.value.commands = form.value.commands.filter((_, itemIndex) => itemIndex !== index)
    if (form.value.commands.length === 0) addCommand()
  }

  async function save(): Promise<void> {
    ensureDefaults()
    const values = form.value
    const key = values.key.trim()
    const authProfileKey = values.auth_profile_key.trim()
    const allowedChatRefs = parseCsv(values.allowed_chat_refs)
    const allowedUserRefs = parseCsv(values.allowed_user_refs)
    const identityDisplayName = values.identity_display_name.trim()
    const commands = toCommandSpecs(values.commands)
    if (!key) return fail('Telegram profile key is required.')
    if (!identityDisplayName) return fail('Bot display name is required.')
    if (!authProfileKey) return fail('Choose a Telegram connection.')
    const selectedConnection = telegramConnectionForProfile(authProfileKey, connections.value)
    const botUsername = botUsernameFromConnection(selectedConnection)
    if (!botUsername) {
      return fail(
        'Test the Telegram connection first so StackOS can fetch the bot identity from Telegram.',
      )
    }
    if (allowedUserRefs.length === 0) {
      return fail('Allowlisted users are required before the bot can trigger agents.')
    }
    options.busyAction.value = 'telegram-profile:save'
    try {
      const existing = options.profiles.value.find((profile) => profile.key === key) ?? null
      await callOperation(
        'communicationProfile.upsert',
        buildTelegramProfilePayload({
          projectId: options.projectId.value,
          existing,
          key,
          authProfileKey,
          botUsername,
          identityDisplayName,
          identityPurpose: values.identity_purpose.trim(),
          identityVoice: values.identity_voice.trim(),
          agentDefaultInstructions: values.agent_default_instructions.trim(),
          agentBoundaries: values.agent_boundaries.trim(),
          agentEscalation: values.agent_escalation.trim(),
          allowedChatRefs,
          allowedUserRefs,
          commands,
          mentionPatterns: parseCsv(values.mention_patterns),
          storeNonTriggerMessages: values.store_non_trigger_messages,
          originRequired: values.origin_required,
          replyToSourceMessage: values.reply_to_source_message,
          sameThread: values.same_thread,
        }),
      )
      message.value = { tone: 'success', text: `Saved ${key}.` }
      panelOpen.value = false
      await options.reload()
    } catch (err) {
      message.value = {
        tone: 'danger',
        text: formatApiError(err, 'failed to save Telegram profile'),
      }
    } finally {
      options.busyAction.value = null
    }
  }

  function ensureDefaults(): void {
    const preferred = preferredTelegramConnection(
      identifiedConnections.value,
      connections.value,
    )
    if (!form.value.auth_profile_key && preferred) {
      form.value = { ...form.value, auth_profile_key: preferred.profile_key }
    }
  }

  function fail(text: string): void {
    message.value = { tone: 'danger', text }
  }

  return {
    panelOpen,
    message,
    form,
    connections,
    identifiedConnections,
    connectionOptions,
    openAdd,
    edit,
    addCommand,
    removeCommand,
    save,
  }
}
