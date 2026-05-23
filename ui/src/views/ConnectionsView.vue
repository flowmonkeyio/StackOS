<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'

import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCheckbox,
  UiFormField,
  UiInput,
  UiJsonBlock,
  UiPageShell,
  UiPanel,
  UiSecretInput,
  UiSectionHeader,
  UiSelect,
  UiSidePanel,
  UiTextarea,
} from '@/components/ui'
import type { SchemaAuthProviderOut, SchemaCredentialConnectionOut } from '@/api'
import { formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'
import { useStackOsCatalogStore } from '@/stores/plugins'

type ConnectionRow = SchemaCredentialConnectionOut & { id: string }
type AuthMethod = NonNullable<SchemaAuthProviderOut['auth_methods']>[number]
type AuthField = NonNullable<AuthMethod['fields']>[number]
type MessageTone = 'success' | 'danger' | 'info'
type BadgeTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'accent'

interface ServiceGroup {
  provider: SchemaAuthProviderOut | null
  providerKey: string
  connections: ConnectionRow[]
}

interface TelegramBotProfile {
  record_id: number
  project_id: number
  external_id: string
  key: string
  provider_key: string
  auth_profile_key: string
  enabled: boolean
  bot_username: string | null
  ingress_mode: string
  allowed_updates: string[]
  identity: {
    display_name?: string
    purpose?: string
    voice?: string
  }
  agent_guidance: {
    default_instructions?: string
    boundaries?: string
    escalation?: string
  }
  access_policy: {
    dm_mode?: string
    group_mode?: string
    user_mode?: string
    allowed_chat_refs?: string[]
    allowed_user_refs?: string[]
    denied_chat_refs?: string[]
    denied_user_refs?: string[]
  }
  trigger_policy: {
    commands?: TelegramCommandSpec[]
    mention_patterns?: string[]
  }
  visibility_policy: Record<string, unknown>
  context_policy: Record<string, unknown>
  response_policy: Record<string, unknown>
  refs: Record<string, string>
  webhook_base_url: string | null
  allowed_webhook_hosts: string[]
}

interface TelegramCommandSpec {
  command: string
  description?: string
  guidance?: string
  enabled?: boolean
  aliases?: string[]
  arguments_schema?: Record<string, unknown>
  required_context?: string[]
  expected_outputs?: string[]
}

interface TelegramCommandDraft {
  command: string
  description: string
  guidance: string
  enabled: boolean
}

interface TelegramBotProfileListOut {
  items: TelegramBotProfile[]
  next_cursor: string | number | null
  total_estimate: number | null
}

const AUTH_TYPE_LABELS: Record<string, string> = {
  'api-key': 'API key',
  'application-password': 'Application password',
  basic: 'Username and password',
  local: 'Local',
  none: 'No auth',
  oauth: 'OAuth2',
  'oauth-client-credentials': 'OAuth2 client credentials',
}

const STATUS_ORDER: Record<string, number> = {
  connected: 0,
  pending: 1,
  expired: 2,
  failed: 3,
  revoked: 4,
}

const PLUGIN_LABELS: Record<string, string> = {
  gtm: 'GTM',
  'media-buying': 'Media Buying',
  seo: 'SEO',
}

const route = useRoute()
const catalogStore = useStackOsCatalogStore()
const { authProviders, authStatus, enabledPlugins, loading, error } = storeToRefs(catalogStore)

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const addPanelOpen = ref(false)
const selectedProviderKey = ref('')
const selectedMethodByProvider = ref<Record<string, string>>({})
const labelByForm = ref<Record<string, string>>({})
const profileByForm = ref<Record<string, string>>({})
const fieldsByForm = ref<Record<string, Record<string, string>>>({})
const busyAction = ref<string | null>(null)
const providerMessages = ref<Record<string, { tone: MessageTone; text: string }>>({})
const connectionMessages = ref<Record<string, { tone: MessageTone; text: string }>>({})
const botProfiles = ref<TelegramBotProfile[]>([])
const botProfilesLoading = ref(false)
const botProfilePanelOpen = ref(false)
const botProfileMessage = ref<{ tone: MessageTone; text: string } | null>(null)
const botProfileForm = ref({
  key: 'support-bot',
  auth_profile_key: '',
  bot_username: '',
  identity_display_name: 'Support Bot',
  identity_purpose: '',
  identity_voice: 'Clear, concise, and operational.',
  agent_default_instructions: '',
  agent_boundaries: '',
  agent_escalation: '',
  allowed_chat_refs: '',
  allowed_user_refs: '',
  commands: [
    {
      command: '/support',
      description: 'Handle support requests.',
      guidance: 'Triage the request, inspect relevant context, and reply with the next clear step.',
      enabled: true,
    },
  ] as TelegramCommandDraft[],
  mention_patterns: '',
  store_non_trigger_messages: true,
  origin_required: true,
  reply_to_source_message: true,
  same_thread: true,
})

const connections = computed<ConnectionRow[]>(() =>
  (authStatus.value?.connections ?? []).map((connection) => ({
    ...connection,
    id: connection.credential_ref,
  })),
)

const providerByKey = computed(() => {
  const rows = new Map<string, SchemaAuthProviderOut>()
  for (const provider of authProviders.value) rows.set(provider.key, provider)
  return rows
})

const visibleAuthProviders = computed(() => {
  const enabledPluginSlugs = new Set(enabledPlugins.value.map((plugin) => plugin.slug))
  if (enabledPluginSlugs.size === 0) return []
  return authProviders.value.filter(
    (provider) =>
      canAddProvider(provider) &&
      (!provider.plugin_slug || enabledPluginSlugs.has(provider.plugin_slug)),
  )
})

const providerOptions = computed(() =>
  visibleAuthProviders.value.map((provider) => ({
    value: provider.key,
    label: provider.name,
    group: pluginLabel(provider.plugin_slug),
  })),
)

const selectedProvider = computed(() => {
  if (selectedProviderKey.value) {
    const provider = providerByKey.value.get(selectedProviderKey.value)
    if (provider) return provider
  }
  return visibleAuthProviders.value[0] ?? null
})

const activeConnections = computed(() =>
  connections.value.filter((connection) => connection.revoked_at === null),
)

const connectedConnections = computed(() =>
  activeConnections.value.filter((connection) => connection.status === 'connected'),
)

const attentionConnections = computed(() =>
  activeConnections.value.filter((connection) => connection.status !== 'connected'),
)

const telegramConnections = computed(() =>
  connectedConnections.value.filter((connection) => connection.provider_key === 'telegram-bot'),
)

const identifiedTelegramConnections = computed(() =>
  telegramConnections.value.filter((connection) => botUsernameFromConnection(connection)),
)

const telegramConnectionOptions = computed(() =>
  telegramConnections.value.map((connection) => ({
    value: connection.profile_key,
    label: `${connectionTitle(connection)} (${connection.profile_key})`,
  })),
)

const serviceGroups = computed<ServiceGroup[]>(() => {
  const grouped = new Map<string, ConnectionRow[]>()
  for (const connection of connections.value) {
    const rows = grouped.get(connection.provider_key) ?? []
    rows.push(connection)
    grouped.set(connection.provider_key, rows)
  }
  return Array.from(grouped.entries())
    .map(([providerKey, rows]) => ({
      providerKey,
      provider: providerByKey.value.get(providerKey) ?? null,
      connections: [...rows].sort(compareConnections),
    }))
    .sort((left, right) => serviceName(left).localeCompare(serviceName(right)))
})

const connectedServiceCount = computed(
  () => new Set(connectedConnections.value.map((connection) => connection.provider_key)).size,
)

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  await catalogStore.refresh(projectId.value)
  await catalogStore.refreshAuth(projectId.value)
  await loadBotProfiles()
  syncProviderSelectionFromQuery()
}

async function loadBotProfiles(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  botProfilesLoading.value = true
  try {
    const response = await callOperation<TelegramBotProfileListOut>(
      'communicationBotProfile.list',
      { project_id: projectId.value },
    )
    botProfiles.value = response.items ?? []
  } catch (err) {
    botProfileMessage.value = {
      tone: 'danger',
      text: formatApiError(err, 'failed to load Telegram bot profiles'),
    }
  } finally {
    botProfilesLoading.value = false
  }
}

function syncProviderSelectionFromQuery(): void {
  const providerKey = typeof route.query.provider_key === 'string' ? route.query.provider_key : ''
  if (!providerKey) return
  const provider = providerByKey.value.get(providerKey)
  if (!provider || !canAddProvider(provider)) return
  selectedProviderKey.value = providerKey
  addPanelOpen.value = true
}

function authMethods(provider: SchemaAuthProviderOut): AuthMethod[] {
  return provider.auth_methods ?? []
}

function selectedMethodKey(provider: SchemaAuthProviderOut): string {
  return selectedMethodByProvider.value[provider.key] ?? authMethods(provider)[0]?.key ?? ''
}

function selectedMethod(provider: SchemaAuthProviderOut): AuthMethod | null {
  const key = selectedMethodKey(provider)
  return (
    authMethods(provider).find((method) => method.key === key) ?? authMethods(provider)[0] ?? null
  )
}

function setSelectedMethod(providerKey: string, value: string | number | null): void {
  selectedMethodByProvider.value = {
    ...selectedMethodByProvider.value,
    [providerKey]: String(value ?? ''),
  }
}

function formKey(providerKey: string, methodKey: string): string {
  return `${providerKey}:${methodKey}`
}

function supportsCredential(provider: SchemaAuthProviderOut): boolean {
  return authMethods(provider).some(
    (method) =>
      method.payload_format !== 'none' || (method.fields ?? []).length > 0 || method.interactive,
  )
}

function canAddProvider(provider: SchemaAuthProviderOut): boolean {
  return provider.config_json?.connection_setup !== 'project-local-plugin-required'
}

function inputType(field: AuthField): 'text' | 'url' | 'number' | 'email' {
  if (field.type === 'url') return 'url'
  if (field.type === 'number') return 'number'
  if (field.type === 'email') return 'email'
  return 'text'
}

function isSecretField(field: AuthField): boolean {
  return field.secret || ['secret', 'password'].includes(field.type)
}

function isAdvancedCredentialField(provider: SchemaAuthProviderOut, field: AuthField): boolean {
  return provider.key === 'telegram-bot'
    ? ['api_base_url', 'webhook_secret_token'].includes(field.key)
    : false
}

function primaryCredentialFields(
  provider: SchemaAuthProviderOut,
  method: AuthMethod | null | undefined,
): AuthField[] {
  return (method?.fields ?? []).filter((field) => !isAdvancedCredentialField(provider, field))
}

function advancedCredentialFields(
  provider: SchemaAuthProviderOut,
  method: AuthMethod | null | undefined,
): AuthField[] {
  return (method?.fields ?? []).filter((field) => isAdvancedCredentialField(provider, field))
}

function fieldOptions(field: AuthField): Array<{ value: string; label: string }> {
  return (field.options ?? [])
    .map((option) => {
      const value = option.value ?? option.key ?? option.label
      const label = option.label ?? option.value ?? option.key
      return value && label ? { value: String(value), label: String(label) } : null
    })
    .filter((option): option is { value: string; label: string } => option !== null)
}

function hasFieldOptions(field: AuthField): boolean {
  return field.type === 'select' || fieldOptions(field).length > 0
}

function fieldValue(providerKey: string, methodKey: string, fieldKey: string): string {
  return fieldsByForm.value[formKey(providerKey, methodKey)]?.[fieldKey] ?? ''
}

function setFieldValue(
  providerKey: string,
  methodKey: string,
  fieldKey: string,
  value: string | number | null,
): void {
  const key = formKey(providerKey, methodKey)
  fieldsByForm.value = {
    ...fieldsByForm.value,
    [key]: {
      ...(fieldsByForm.value[key] ?? {}),
      [fieldKey]: value === null ? '' : String(value),
    },
  }
}

function setSelectedProvider(value: string | number | null): void {
  selectedProviderKey.value = String(value ?? '')
}

function openAddConnection(providerKey?: string): void {
  if (providerKey) selectedProviderKey.value = providerKey
  if (!selectedProviderKey.value && visibleAuthProviders.value[0]) {
    selectedProviderKey.value = visibleAuthProviders.value[0].key
  }
  addPanelOpen.value = true
}

function openAddBotProfile(): void {
  const preferred = preferredTelegramConnection()
  if (!botProfileForm.value.auth_profile_key && preferred) {
    botProfileForm.value = {
      ...botProfileForm.value,
      auth_profile_key: preferred.profile_key,
    }
  }
  botProfileMessage.value = null
  botProfilePanelOpen.value = true
}

function editBotProfile(profile: TelegramBotProfile): void {
  botProfileForm.value = {
    key: profile.key,
    auth_profile_key: profile.auth_profile_key,
    bot_username: profile.bot_username ?? '',
    identity_display_name: String(profile.identity.display_name ?? profile.key),
    identity_purpose: String(profile.identity.purpose ?? ''),
    identity_voice: String(profile.identity.voice ?? ''),
    agent_default_instructions: String(profile.agent_guidance.default_instructions ?? ''),
    agent_boundaries: String(profile.agent_guidance.boundaries ?? ''),
    agent_escalation: String(profile.agent_guidance.escalation ?? ''),
    allowed_chat_refs: (profile.access_policy.allowed_chat_refs ?? []).join(', '),
    allowed_user_refs: (profile.access_policy.allowed_user_refs ?? []).join(', '),
    commands: toCommandDrafts(profile.trigger_policy.commands ?? []),
    mention_patterns: (profile.trigger_policy.mention_patterns ?? []).join(', '),
    store_non_trigger_messages: profile.visibility_policy.store_non_trigger_messages !== false,
    origin_required: profile.response_policy.origin_required !== false,
    reply_to_source_message: profile.response_policy.reply_to_source_message === true,
    same_thread: profile.response_policy.same_thread === true,
  }
  botProfileMessage.value = null
  botProfilePanelOpen.value = true
}

function compareConnections(left: ConnectionRow, right: ConnectionRow): number {
  const statusDiff = (STATUS_ORDER[left.status] ?? 99) - (STATUS_ORDER[right.status] ?? 99)
  if (statusDiff !== 0) return statusDiff
  return connectionTitle(left).localeCompare(connectionTitle(right))
}

function serviceName(group: ServiceGroup): string {
  return group.provider?.name ?? group.providerKey
}

function pluginLabel(slug: string | null | undefined): string {
  if (!slug) return 'StackOS'
  if (PLUGIN_LABELS[slug]) return PLUGIN_LABELS[slug]
  return slug
    .split('-')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function providerSetupNote(provider: SchemaAuthProviderOut): string | null {
  const value = provider.config_json?.setup_note
  return typeof value === 'string' && value.trim() ? value : null
}

function formatAuthType(authType: string | null | undefined): string {
  if (!authType) return 'Auth'
  return AUTH_TYPE_LABELS[authType] ?? authType
}

function methodLabel(provider: SchemaAuthProviderOut, methodKey: string): string {
  return authMethods(provider).find((method) => method.key === methodKey)?.label ?? methodKey
}

function serviceStatusTone(group: ServiceGroup): BadgeTone {
  if (
    group.connections.some(
      (connection) => connection.status === 'connected' && connection.revoked_at === null,
    )
  ) {
    return 'success'
  }
  if (group.connections.some((connection) => ['failed', 'revoked'].includes(connection.status))) {
    return 'danger'
  }
  return 'warning'
}

function serviceStatusDotClass(group: ServiceGroup): string {
  const tone = serviceStatusTone(group)
  if (tone === 'success') return 'bg-success'
  if (tone === 'danger') return 'bg-danger'
  if (tone === 'warning') return 'bg-warning'
  return 'bg-neutral'
}

function serviceStatusLabel(group: ServiceGroup): string {
  const connected = group.connections.filter(
    (connection) => connection.status === 'connected' && connection.revoked_at === null,
  ).length
  if (connected > 0) return `${connected} connected`
  const first = group.connections[0]
  return first ? first.status : 'not connected'
}

function connectionCountLabel(group: ServiceGroup): string {
  const count = group.connections.length
  return `${count} connection${count === 1 ? '' : 's'}`
}

function statusTone(connection: SchemaCredentialConnectionOut): BadgeTone {
  if (connection.status === 'connected' && !connection.setup_required) return 'success'
  if (connection.status === 'failed' || connection.status === 'revoked') return 'danger'
  return 'warning'
}

function statusDotClass(connection: SchemaCredentialConnectionOut): string {
  const tone = statusTone(connection)
  if (tone === 'success') return 'bg-success'
  if (tone === 'danger') return 'bg-danger'
  if (tone === 'warning') return 'bg-warning'
  return 'bg-neutral'
}

function connectionTitle(connection: SchemaCredentialConnectionOut): string {
  return String(connection.label || connection.account?.display_name || connection.profile_key)
}

function accountLabel(connection: SchemaCredentialConnectionOut): string {
  return String(
    connection.account?.display_name ??
      connection.account?.provider_account_id ??
      connection.profile_key ??
      '-',
  )
}

function telegramConnectionForProfile(profileKey: string): ConnectionRow | null {
  return (
    telegramConnections.value.find((connection) => connection.profile_key === profileKey) ?? null
  )
}

function botUsernameFromConnection(connection: SchemaCredentialConnectionOut | null): string | null {
  const metadata = connection?.account?.metadata_json
  const username =
    metadata && typeof metadata === 'object' && 'username' in metadata
      ? String((metadata as Record<string, unknown>).username ?? '').trim()
      : ''
  if (username) return username.replace(/^@/, '')
  const displayName = String(connection?.account?.display_name ?? '').trim()
  return displayName.startsWith('@') ? displayName.slice(1) : null
}

function preferredTelegramConnection(): ConnectionRow | null {
  return identifiedTelegramConnections.value[0] ?? telegramConnections.value[0] ?? null
}

function connectionActionKey(credentialRef: string, action: string): string {
  return `${credentialRef}:${action}`
}

function isConnectionBusy(credentialRef: string, action: string): boolean {
  return busyAction.value === connectionActionKey(credentialRef, action)
}

function providerActionKey(providerKey: string, action: string): string {
  return `${providerKey}:${action}`
}

function isProviderBusy(providerKey: string, action: string): boolean {
  return busyAction.value === providerActionKey(providerKey, action)
}

function setProviderMessage(providerKey: string, tone: MessageTone, text: string): void {
  providerMessages.value = {
    ...providerMessages.value,
    [providerKey]: { tone, text },
  }
}

function setConnectionMessage(credentialRef: string, tone: MessageTone, text: string): void {
  connectionMessages.value = {
    ...connectionMessages.value,
    [credentialRef]: { tone, text },
  }
}

function parseCsv(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

function normalizeCommand(value: string): string {
  const command = value.trim()
  if (!command) return ''
  return command.startsWith('/') ? command : `/${command}`
}

function toCommandDrafts(commands: TelegramCommandSpec[]): TelegramCommandDraft[] {
  const drafts = commands.map((command) => ({
    command: normalizeCommand(command.command),
    description: command.description ?? '',
    guidance: command.guidance ?? '',
    enabled: command.enabled !== false,
  }))
  return drafts.length > 0
    ? drafts
    : [
        {
          command: '',
          description: '',
          guidance: '',
          enabled: true,
        },
      ]
}

function toCommandSpecs(commands: TelegramCommandDraft[]): TelegramCommandSpec[] {
  return commands
    .map((command) => ({
      command: normalizeCommand(command.command),
      description: command.description.trim(),
      guidance: command.guidance.trim(),
      enabled: command.enabled,
    }))
    .filter((command) => command.command)
}

function addCommandDraft(): void {
  botProfileForm.value.commands = [
    ...botProfileForm.value.commands,
    { command: '', description: '', guidance: '', enabled: true },
  ]
}

function removeCommandDraft(index: number): void {
  botProfileForm.value.commands = botProfileForm.value.commands.filter((_, itemIndex) => {
    return itemIndex !== index
  })
  if (botProfileForm.value.commands.length === 0) addCommandDraft()
}

function commandSummary(commands: TelegramCommandSpec[] | undefined): string {
  const values = (commands ?? [])
    .filter((command) => command.enabled !== false)
    .map((command) => command.command)
    .filter(Boolean)
  return values.length > 0 ? values.join(', ') : '-'
}

function ensureBotFormDefaults(): void {
  const preferred = preferredTelegramConnection()
  if (!botProfileForm.value.auth_profile_key && preferred) {
    botProfileForm.value = {
      ...botProfileForm.value,
      auth_profile_key: preferred.profile_key,
    }
  }
}

async function saveBotProfile(): Promise<void> {
  ensureBotFormDefaults()
  const form = botProfileForm.value
  const key = form.key.trim()
  const authProfileKey = form.auth_profile_key.trim()
  const allowedChatRefs = parseCsv(form.allowed_chat_refs)
  const allowedUserRefs = parseCsv(form.allowed_user_refs)
  const identityDisplayName = form.identity_display_name.trim()
  const commands = toCommandSpecs(form.commands)
  if (!key) {
    botProfileMessage.value = { tone: 'danger', text: 'Bot profile key is required.' }
    return
  }
  if (!identityDisplayName) {
    botProfileMessage.value = { tone: 'danger', text: 'Bot display name is required.' }
    return
  }
  if (!authProfileKey) {
    botProfileMessage.value = { tone: 'danger', text: 'Choose a Telegram connection.' }
    return
  }
  const selectedTelegramConnection = telegramConnectionForProfile(authProfileKey)
  const botUsername = botUsernameFromConnection(selectedTelegramConnection)
  if (!botUsername) {
    botProfileMessage.value = {
      tone: 'danger',
      text: 'Test the Telegram connection first so StackOS can fetch the bot identity from Telegram.',
    }
    return
  }
  if (allowedChatRefs.length === 0 || allowedUserRefs.length === 0) {
    botProfileMessage.value = {
      tone: 'danger',
      text: 'Allowlisted chats and users are required before the bot can trigger agents.',
    }
    return
  }
  busyAction.value = 'telegram-bot-profile:save'
  try {
    await callOperation('communicationBotProfile.upsert', {
      project_id: projectId.value,
      key,
      auth_profile_key: authProfileKey,
      bot_username: botUsername,
      identity: {
        display_name: identityDisplayName,
        purpose: form.identity_purpose.trim(),
        voice: form.identity_voice.trim(),
      },
      agent_guidance: {
        default_instructions: form.agent_default_instructions.trim(),
        boundaries: form.agent_boundaries.trim(),
        escalation: form.agent_escalation.trim(),
      },
      access_policy: {
        dm_mode: 'allowlist',
        group_mode: 'allowlist',
        user_mode: 'allowlist',
        allowed_chat_refs: allowedChatRefs,
        allowed_user_refs: allowedUserRefs,
      },
      trigger_policy: {
        dm_trigger: 'always',
        group_trigger: 'mention_or_command',
        commands,
        mention_patterns: parseCsv(form.mention_patterns),
        reply_to_bot_triggers: true,
      },
      visibility_policy: {
        store_non_trigger_messages: form.store_non_trigger_messages,
      },
      response_policy: {
        reply_in_same_chat: true,
        origin_required: form.origin_required,
        reply_to_source_message: form.reply_to_source_message,
        same_thread: form.same_thread,
      },
    })
    botProfileMessage.value = { tone: 'success', text: `Saved ${key}.` }
    botProfilePanelOpen.value = false
    await loadBotProfiles()
  } catch (err) {
    botProfileMessage.value = {
      tone: 'danger',
      text: formatApiError(err, 'failed to save Telegram bot profile'),
    }
  } finally {
    busyAction.value = null
  }
}

function credentialFields(
  provider: SchemaAuthProviderOut,
  method: AuthMethod,
): Record<string, string> | null {
  const fields: Record<string, string> = {}
  for (const field of method.fields ?? []) {
    const value = fieldValue(provider.key, method.key, field.key).trim()
    if (field.required && !value) {
      setProviderMessage(provider.key, 'danger', `${field.label} is required.`)
      return null
    }
    if (value) fields[field.key] = value
  }
  return fields
}

async function saveCredential(provider: SchemaAuthProviderOut): Promise<void> {
  const method = selectedMethod(provider)
  if (!method || method.payload_format === 'none') return
  const fields = credentialFields(provider, method)
  if (fields === null) return
  const key = formKey(provider.key, method.key)
  const profileKey = (profileByForm.value[key] ?? 'default').trim() || 'default'
  const label = (labelByForm.value[key] ?? '').trim()
  if (Object.keys(fields).length === 0 && (method.fields ?? []).some((field) => field.secret)) {
    setProviderMessage(provider.key, 'danger', 'Credential fields are required.')
    return
  }
  busyAction.value = providerActionKey(provider.key, 'save')
  try {
    const response = await catalogStore.storeCredential(projectId.value, provider.key, {
      auth_method_key: method.key,
      profile_key: profileKey,
      label: label || null,
      fields,
    })
    let message = `Stored ${response.data.credential_ref}.`
    let tone: MessageTone = 'success'
    if (provider.key === 'telegram-bot') {
      const testResponse = await catalogStore.testCredential(projectId.value, {
        credential_ref: response.data.credential_ref,
      })
      const rawUsername = testResponse.data.metadata?.username
      const username = typeof rawUsername === 'string' ? rawUsername : ''
      message = testResponse.data.ok
        ? `Connected ${username ? `@${username}` : response.data.credential_ref}.`
        : testResponse.data.summary
      tone = testResponse.data.ok ? 'success' : 'danger'
    }
    fieldsByForm.value = { ...fieldsByForm.value, [key]: {} }
    profileByForm.value = { ...profileByForm.value, [key]: '' }
    labelByForm.value = { ...labelByForm.value, [key]: '' }
    setProviderMessage(provider.key, tone, message)
    addPanelOpen.value = false
  } catch (err) {
    setProviderMessage(provider.key, 'danger', formatApiError(err, 'failed to store credential'))
  } finally {
    busyAction.value = null
  }
}

async function startProvider(provider: SchemaAuthProviderOut): Promise<void> {
  const method = selectedMethod(provider)
  if (!method) return
  busyAction.value = providerActionKey(provider.key, 'start')
  try {
    const response = await catalogStore.startCredential(projectId.value, provider.key, {
      auth_method_key: method.key,
      redirect_uri: null,
    })
    const url = response.data.authorization_url ?? response.data.setup_url
    setProviderMessage(
      provider.key,
      'info',
      url ? `Setup URL ready: ${url}` : `Started ${response.data.status}.`,
    )
  } catch (err) {
    setProviderMessage(provider.key, 'danger', formatApiError(err, 'failed to start auth flow'))
  } finally {
    busyAction.value = null
  }
}

async function testConnection(connection: SchemaCredentialConnectionOut): Promise<void> {
  busyAction.value = connectionActionKey(connection.credential_ref, 'test')
  try {
    const response = await catalogStore.testCredential(projectId.value, {
      credential_ref: connection.credential_ref,
    })
    setConnectionMessage(
      connection.credential_ref,
      response.data.ok ? 'success' : 'danger',
      response.data.summary,
    )
  } catch (err) {
    setConnectionMessage(
      connection.credential_ref,
      'danger',
      formatApiError(err, 'failed to test credential'),
    )
  } finally {
    busyAction.value = null
  }
}

async function revokeConnection(connection: SchemaCredentialConnectionOut): Promise<void> {
  busyAction.value = connectionActionKey(connection.credential_ref, 'revoke')
  try {
    await catalogStore.revokeCredential(projectId.value, {
      credential_ref: connection.credential_ref,
    })
    setConnectionMessage(connection.credential_ref, 'info', `Revoked ${connection.credential_ref}.`)
  } catch (err) {
    setConnectionMessage(
      connection.credential_ref,
      'danger',
      formatApiError(err, 'failed to revoke credential'),
    )
  } finally {
    busyAction.value = null
  }
}

onMounted(load)
watch(projectId, load)
watch(authProviders, (providers) => {
  if (!selectedProviderKey.value && providers[0]) {
    selectedProviderKey.value = visibleAuthProviders.value[0]?.key ?? providers[0].key
  }
  syncProviderSelectionFromQuery()
})
watch(visibleAuthProviders, (providers) => {
  if (!selectedProviderKey.value && providers[0]) selectedProviderKey.value = providers[0].key
  if (
    selectedProviderKey.value &&
    providers.length > 0 &&
    !providers.some((provider) => provider.key === selectedProviderKey.value)
  ) {
    selectedProviderKey.value = providers[0].key
  }
})
watch(() => route.query.provider_key, syncProviderSelectionFromQuery)
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Connections"
      description="Add provider accounts once, keep secrets daemon-side, and give agents only safe credential refs."
      :breadcrumbs="[{ label: 'Connections' }]"
    >
      <template #actions>
        <UiButton variant="primary" icon-left="plus" @click="openAddConnection()">
          Add connection
        </UiButton>
      </template>
    </ProjectPageHeader>

    <UiCallout v-if="error" tone="danger">
      {{ error }}
    </UiCallout>

    <div class="grid gap-3 md:grid-cols-3">
      <UiPanel class="p-3">
        <p class="text-xs text-fg-muted">Connected services</p>
        <p class="mt-1 text-2xl font-semibold text-fg-strong">{{ connectedServiceCount }}</p>
      </UiPanel>
      <UiPanel class="p-3">
        <p class="text-xs text-fg-muted">Active connections</p>
        <p class="mt-1 text-2xl font-semibold text-fg-strong">{{ activeConnections.length }}</p>
      </UiPanel>
      <UiPanel class="p-3">
        <p class="text-xs text-fg-muted">Needs attention</p>
        <p class="mt-1 text-2xl font-semibold text-fg-strong">{{ attentionConnections.length }}</p>
      </UiPanel>
    </div>

    <UiPanel class="p-4">
      <UiSectionHeader
        title="Connected Services"
        description="Each service can have multiple named connections for different accounts, workspaces, or client profiles."
      >
        <template #actions>
          <UiBadge>{{ connections.length }}</UiBadge>
        </template>
      </UiSectionHeader>

      <div
        v-if="loading"
        class="rounded-md border border-subtle bg-bg-surface p-4 text-sm text-fg-muted"
      >
        Loading connections...
      </div>

      <div
        v-else-if="serviceGroups.length === 0"
        class="rounded-md border border-dashed border-default bg-bg-surface p-6 text-center"
      >
        <p class="font-medium text-fg-strong">No services connected.</p>
        <p class="mx-auto mt-1 max-w-xl text-sm text-fg-muted">
          Add the first connection for a provider account or internal tool. The daemon stores the
          secret and exposes only status, labels, and credential refs.
        </p>
        <UiButton class="mt-4" variant="primary" icon-left="plus" @click="openAddConnection()">
          Add connection
        </UiButton>
      </div>

      <ul v-else class="grid gap-3">
        <li
          v-for="group in serviceGroups"
          :key="group.providerKey"
          class="overflow-hidden rounded-md border border-subtle bg-bg-surface shadow-xs"
        >
          <div class="border-b border-subtle bg-bg-surface-alt px-4 py-4 sm:px-5">
            <div class="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
              <div class="flex min-w-0 gap-3">
                <span
                  :class="['mt-1 h-2.5 w-2.5 shrink-0 rounded-full', serviceStatusDotClass(group)]"
                  aria-hidden="true"
                />
                <div class="min-w-0">
                  <div class="flex flex-wrap items-center gap-2">
                    <h3 class="text-base font-semibold leading-6 text-fg-strong">
                      {{ serviceName(group) }}
                    </h3>
                    <UiBadge v-if="group.provider" tone="accent">
                      {{ pluginLabel(group.provider.plugin_slug) }}
                    </UiBadge>
                    <UiBadge :tone="serviceStatusTone(group)">
                      {{ serviceStatusLabel(group) }}
                    </UiBadge>
                  </div>
                  <p
                    v-if="group.provider?.description"
                    class="mt-1 max-w-3xl text-sm leading-5 text-fg-muted"
                  >
                    {{ group.provider.description }}
                  </p>
                  <dl class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
                    <div class="flex min-w-0 items-center gap-1.5">
                      <dt class="shrink-0 text-fg-muted">Provider</dt>
                      <dd class="truncate font-mono text-fg-default">{{ group.providerKey }}</dd>
                    </div>
                    <div v-if="group.provider" class="flex items-center gap-1.5">
                      <dt class="text-fg-muted">Auth</dt>
                      <dd class="text-fg-default">
                        {{ formatAuthType(group.provider.auth_type) }}
                      </dd>
                    </div>
                    <div class="flex items-center gap-1.5">
                      <dt class="text-fg-muted">Saved</dt>
                      <dd class="text-fg-default">{{ connectionCountLabel(group) }}</dd>
                    </div>
                  </dl>
                </div>
              </div>
              <UiButton
                v-if="group.provider && canAddProvider(group.provider)"
                class="shrink-0"
                size="sm"
                icon-left="plus"
                @click="openAddConnection(group.provider.key)"
              >
                Add another
              </UiButton>
            </div>
          </div>

          <div class="divide-y divide-subtle">
            <article
              v-for="connection in group.connections"
              :key="connection.credential_ref"
              class="grid gap-3 px-4 py-4 sm:px-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(26rem,1.5fr)_auto] xl:items-center"
            >
              <div class="flex min-w-0 gap-3">
                <span
                  :class="['mt-2 h-2 w-2 shrink-0 rounded-full', statusDotClass(connection)]"
                  aria-hidden="true"
                />
                <div class="min-w-0">
                  <div class="flex flex-wrap items-center gap-2">
                    <h4 class="truncate text-sm font-semibold leading-5 text-fg-strong">
                      {{ connectionTitle(connection) }}
                    </h4>
                    <UiBadge :tone="statusTone(connection)">
                      {{ connection.status }}
                    </UiBadge>
                  </div>
                  <div
                    class="mt-1 flex min-w-0 flex-wrap items-center gap-1.5 text-xs text-fg-muted"
                  >
                    <span class="truncate font-mono">{{ connection.credential_ref }}</span>
                    <span aria-hidden="true">&middot;</span>
                    <span>{{ formatAuthType(connection.auth_type) }}</span>
                    <template
                      v-if="
                        group.provider &&
                        methodLabel(group.provider, connection.auth_method_key) !==
                          formatAuthType(connection.auth_type)
                      "
                    >
                      <span aria-hidden="true">&middot;</span>
                      <span>{{ methodLabel(group.provider, connection.auth_method_key) }}</span>
                    </template>
                  </div>
                </div>
              </div>

              <dl class="grid gap-3 text-sm sm:grid-cols-2 2xl:grid-cols-4">
                <div class="min-w-0">
                  <dt class="text-2xs font-medium uppercase text-fg-muted">Connection name</dt>
                  <dd class="mt-0.5 truncate font-mono text-xs text-fg-default">
                    {{ connection.profile_key }}
                  </dd>
                </div>
                <div class="min-w-0">
                  <dt class="text-2xs font-medium uppercase text-fg-muted">Account</dt>
                  <dd class="mt-0.5 truncate text-fg-default">{{ accountLabel(connection) }}</dd>
                </div>
                <div class="min-w-0">
                  <dt class="text-2xs font-medium uppercase text-fg-muted">Expires</dt>
                  <dd class="mt-0.5 truncate text-fg-default">
                    {{ formatDateTime(connection.expires_at) }}
                  </dd>
                </div>
                <div class="min-w-0">
                  <dt class="text-2xs font-medium uppercase text-fg-muted">Last tested</dt>
                  <dd class="mt-0.5 truncate text-fg-default">
                    {{ formatDateTime(connection.last_tested_at) }}
                  </dd>
                </div>
              </dl>

              <div class="flex shrink-0 flex-wrap gap-2 xl:justify-end">
                <UiButton
                  size="sm"
                  icon-left="plug-zap"
                  :loading="isConnectionBusy(connection.credential_ref, 'test')"
                  :disabled="connection.revoked_at !== null"
                  @click="testConnection(connection)"
                >
                  Test
                </UiButton>
                <UiButton
                  size="sm"
                  variant="danger"
                  icon-left="ban"
                  :loading="isConnectionBusy(connection.credential_ref, 'revoke')"
                  :disabled="connection.revoked_at !== null"
                  @click="revokeConnection(connection)"
                >
                  Revoke
                </UiButton>
              </div>

              <UiCallout
                v-if="connectionMessages[connection.credential_ref]"
                :tone="connectionMessages[connection.credential_ref].tone"
                class="xl:col-span-3"
              >
                {{ connectionMessages[connection.credential_ref].text }}
              </UiCallout>
            </article>
          </div>
        </li>
      </ul>
    </UiPanel>

    <UiPanel class="p-4">
      <UiSectionHeader
        title="Telegram Bot Profiles"
        description="Bind a Telegram connection to project-scoped identity, agent guidance, access, trigger, context, and response policy."
      >
        <template #actions>
          <div class="flex flex-wrap items-center gap-2">
            <UiBadge>{{ botProfiles.length }}</UiBadge>
            <UiButton
              size="sm"
              icon-left="plus"
              :disabled="telegramConnections.length === 0"
              @click="openAddBotProfile"
            >
              Add bot profile
            </UiButton>
          </div>
        </template>
      </UiSectionHeader>

      <UiCallout v-if="telegramConnections.length === 0" tone="info">
        Store a Telegram Bot connection before creating a bot profile.
        <UiButton
          class="mt-3"
          size="sm"
          icon-left="plus"
          @click="openAddConnection('telegram-bot')"
        >
          Add Telegram connection
        </UiButton>
      </UiCallout>

      <UiCallout v-else-if="botProfileMessage" :tone="botProfileMessage.tone">
        {{ botProfileMessage.text }}
      </UiCallout>

      <div
        v-if="botProfilesLoading"
        class="rounded-md border border-subtle bg-bg-surface p-4 text-sm text-fg-muted"
      >
        Loading Telegram bot profiles...
      </div>

      <div
        v-else-if="telegramConnections.length > 0 && botProfiles.length === 0"
        class="rounded-md border border-dashed border-default bg-bg-surface p-5"
      >
        <p class="font-medium text-fg-strong">No bot profiles configured.</p>
        <p class="mt-1 max-w-3xl text-sm text-fg-muted">
          Create a profile for each Telegram bot identity or access boundary. Profiles are static
          setup; agents still decide which work to run after a trigger arrives.
        </p>
      </div>

      <ul v-else class="grid gap-3">
        <li
          v-for="profile in botProfiles"
          :key="profile.external_id"
          class="rounded-md border border-subtle bg-bg-surface px-4 py-4"
        >
          <div
            class="grid gap-3 lg:grid-cols-[minmax(0,1.2fr)_minmax(20rem,1fr)_auto] lg:items-center"
          >
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <h3 class="text-sm font-semibold text-fg-strong">
                  {{ profile.identity.display_name || profile.key }}
                </h3>
                <UiBadge :tone="profile.enabled ? 'success' : 'warning'">
                  {{ profile.enabled ? 'enabled' : 'disabled' }}
                </UiBadge>
                <UiBadge>{{ profile.ingress_mode }}</UiBadge>
              </div>
              <p class="mt-1 truncate text-xs text-fg-muted">
                <span class="font-mono">{{ profile.key }}</span>
                <span aria-hidden="true"> · </span>Connection
                <span class="font-mono">{{ profile.auth_profile_key }}</span>
                <template v-if="profile.bot_username">
                  <span aria-hidden="true"> · </span>@{{ profile.bot_username }}
                </template>
              </p>
            </div>
            <dl class="grid gap-3 text-sm sm:grid-cols-3">
              <div>
                <dt class="text-2xs font-medium uppercase text-fg-muted">Chats</dt>
                <dd class="mt-0.5 font-mono text-xs text-fg-default">
                  {{ profile.access_policy.allowed_chat_refs?.length ?? 0 }}
                </dd>
              </div>
              <div>
                <dt class="text-2xs font-medium uppercase text-fg-muted">Users</dt>
                <dd class="mt-0.5 font-mono text-xs text-fg-default">
                  {{ profile.access_policy.allowed_user_refs?.length ?? 0 }}
                </dd>
              </div>
              <div>
                <dt class="text-2xs font-medium uppercase text-fg-muted">Commands</dt>
                <dd class="mt-0.5 truncate font-mono text-xs text-fg-default">
                  {{ commandSummary(profile.trigger_policy.commands) }}
                </dd>
              </div>
            </dl>
            <div class="flex justify-start lg:justify-end">
              <UiButton size="sm" icon-left="settings" @click="editBotProfile(profile)">
                Configure
              </UiButton>
            </div>
          </div>
        </li>
      </ul>
    </UiPanel>

    <details v-if="authStatus" class="rounded-md border border-default bg-bg-surface shadow-xs">
      <summary class="cursor-pointer px-4 py-3 text-sm font-semibold text-fg-strong focus-ring">
        Diagnostics
      </summary>
      <div class="border-t border-subtle p-3">
        <UiJsonBlock
          :data="sanitizeForDisplay(authStatus)"
          density="compact"
          max-height="18rem"
          wrap
        />
      </div>
    </details>

    <UiSidePanel
      v-model="addPanelOpen"
      title="Add connection"
      description="Choose a service and store the credential in the local daemon."
      size="lg"
    >
      <div v-if="selectedProvider" class="grid gap-4">
        <UiCallout v-if="visibleAuthProviders.length === 0" tone="info">
          Enable a plugin before adding provider connections.
        </UiCallout>

        <UiFormField label="Service">
          <template #default="{ id, describedBy, invalid }">
            <UiSelect
              :id="id"
              :model-value="selectedProvider.key"
              :options="providerOptions"
              :aria-describedby="describedBy"
              :invalid="invalid"
              @update:model-value="setSelectedProvider"
            />
          </template>
        </UiFormField>

        <div class="rounded-md border border-subtle bg-bg-surface-alt p-3">
          <div class="flex flex-wrap items-center gap-2">
            <h3 class="text-sm font-semibold text-fg-strong">{{ selectedProvider.name }}</h3>
            <UiBadge tone="accent">{{ pluginLabel(selectedProvider.plugin_slug) }}</UiBadge>
            <UiBadge>{{ formatAuthType(selectedProvider.auth_type) }}</UiBadge>
          </div>
          <p v-if="selectedProvider.description" class="mt-1 text-sm text-fg-muted">
            {{ selectedProvider.description }}
          </p>
        </div>

        <UiCallout v-if="providerSetupNote(selectedProvider)" tone="info">
          {{ providerSetupNote(selectedProvider) }}
        </UiCallout>

        <template v-if="supportsCredential(selectedProvider) && selectedMethod(selectedProvider)">
          <UiFormField v-if="authMethods(selectedProvider).length > 1" label="Auth method">
            <template #default="{ id, describedBy, invalid }">
              <UiSelect
                :id="id"
                :model-value="selectedMethodKey(selectedProvider)"
                :options="
                  authMethods(selectedProvider).map((method) => ({
                    value: method.key,
                    label: method.label,
                  }))
                "
                :aria-describedby="describedBy"
                :invalid="invalid"
                @update:model-value="setSelectedMethod(selectedProvider.key, $event)"
              />
            </template>
          </UiFormField>

          <UiFormField
            label="Connection name"
            help="Leave blank for the default account. Use a short name like client-a or sandbox when this service has more than one account."
          >
            <template #default="{ id, describedBy, invalid }">
              <UiInput
                :id="id"
                v-model="
                  profileByForm[
                    formKey(selectedProvider.key, selectedMethod(selectedProvider)?.key ?? '')
                  ]
                "
                :aria-describedby="describedBy"
                :invalid="invalid"
                placeholder="default"
              />
            </template>
          </UiFormField>

          <UiFormField label="Display label" help="Shown to operators and agents as safe metadata.">
            <template #default="{ id, describedBy, invalid }">
              <UiInput
                :id="id"
                v-model="
                  labelByForm[
                    formKey(selectedProvider.key, selectedMethod(selectedProvider)?.key ?? '')
                  ]
                "
                :aria-describedby="describedBy"
                :invalid="invalid"
                placeholder="Primary account"
              />
            </template>
          </UiFormField>

          <UiFormField
            v-for="field in primaryCredentialFields(selectedProvider, selectedMethod(selectedProvider))"
            :key="field.key"
            :label="field.label"
            :help="field.description ?? undefined"
            :required="field.required"
          >
            <template #default="{ id, describedBy, invalid }">
              <UiSelect
                v-if="hasFieldOptions(field)"
                :id="id"
                :model-value="
                  fieldValue(
                    selectedProvider.key,
                    selectedMethod(selectedProvider)?.key ?? '',
                    field.key,
                  )
                "
                :options="fieldOptions(field)"
                :aria-describedby="describedBy"
                :invalid="invalid"
                :placeholder="field.placeholder ?? 'Select'"
                @update:model-value="
                  setFieldValue(
                    selectedProvider.key,
                    selectedMethod(selectedProvider)?.key ?? '',
                    field.key,
                    $event,
                  )
                "
              />
              <UiSecretInput
                v-else-if="isSecretField(field)"
                :id="id"
                :model-value="
                  fieldValue(
                    selectedProvider.key,
                    selectedMethod(selectedProvider)?.key ?? '',
                    field.key,
                  )
                "
                :aria-describedby="describedBy"
                :invalid="invalid"
                no-copy
                no-reveal
                :placeholder="field.placeholder ?? ''"
                @update:model-value="
                  setFieldValue(
                    selectedProvider.key,
                    selectedMethod(selectedProvider)?.key ?? '',
                    field.key,
                    $event,
                  )
                "
              />
              <UiInput
                v-else
                :id="id"
                :model-value="
                  fieldValue(
                    selectedProvider.key,
                    selectedMethod(selectedProvider)?.key ?? '',
                    field.key,
                  )
                "
                :type="inputType(field)"
                :aria-describedby="describedBy"
                :invalid="invalid"
                :placeholder="field.placeholder ?? undefined"
                @update:model-value="
                  setFieldValue(
                    selectedProvider.key,
                    selectedMethod(selectedProvider)?.key ?? '',
                    field.key,
                    $event,
                  )
                "
              />
            </template>
          </UiFormField>

          <details
            v-if="
              advancedCredentialFields(selectedProvider, selectedMethod(selectedProvider)).length > 0
            "
            class="rounded-md border border-subtle bg-bg-surface-alt"
          >
            <summary
              class="cursor-pointer px-3 py-2 text-sm font-medium text-fg-default focus-ring"
            >
              Advanced connection settings
              <span class="ml-2 text-xs font-normal text-fg-muted">
                self-hosted Bot API and webhook secret overrides
              </span>
            </summary>
            <div class="grid gap-4 border-t border-subtle p-3">
              <UiFormField
                v-for="field in advancedCredentialFields(
                  selectedProvider,
                  selectedMethod(selectedProvider),
                )"
                :key="field.key"
                :label="field.label"
                :help="field.description ?? undefined"
                :required="field.required"
              >
                <template #default="{ id, describedBy, invalid }">
                  <UiSelect
                    v-if="hasFieldOptions(field)"
                    :id="id"
                    :model-value="
                      fieldValue(
                        selectedProvider.key,
                        selectedMethod(selectedProvider)?.key ?? '',
                        field.key,
                      )
                    "
                    :options="fieldOptions(field)"
                    :aria-describedby="describedBy"
                    :invalid="invalid"
                    :placeholder="field.placeholder ?? 'Select'"
                    @update:model-value="
                      setFieldValue(
                        selectedProvider.key,
                        selectedMethod(selectedProvider)?.key ?? '',
                        field.key,
                        $event,
                      )
                    "
                  />
                  <UiSecretInput
                    v-else-if="isSecretField(field)"
                    :id="id"
                    :model-value="
                      fieldValue(
                        selectedProvider.key,
                        selectedMethod(selectedProvider)?.key ?? '',
                        field.key,
                      )
                    "
                    :aria-describedby="describedBy"
                    :invalid="invalid"
                    no-copy
                    no-reveal
                    :placeholder="field.placeholder ?? ''"
                    @update:model-value="
                      setFieldValue(
                        selectedProvider.key,
                        selectedMethod(selectedProvider)?.key ?? '',
                        field.key,
                        $event,
                      )
                    "
                  />
                  <UiInput
                    v-else
                    :id="id"
                    :model-value="
                      fieldValue(
                        selectedProvider.key,
                        selectedMethod(selectedProvider)?.key ?? '',
                        field.key,
                      )
                    "
                    :type="inputType(field)"
                    :aria-describedby="describedBy"
                    :invalid="invalid"
                    :placeholder="field.placeholder ?? undefined"
                    @update:model-value="
                      setFieldValue(
                        selectedProvider.key,
                        selectedMethod(selectedProvider)?.key ?? '',
                        field.key,
                        $event,
                      )
                    "
                  />
                </template>
              </UiFormField>
            </div>
          </details>

          <UiCallout v-if="selectedMethod(selectedProvider)?.description" tone="info">
            {{ selectedMethod(selectedProvider)?.description }}
          </UiCallout>

          <UiCallout
            v-if="providerMessages[selectedProvider.key]"
            :tone="providerMessages[selectedProvider.key].tone"
          >
            {{ providerMessages[selectedProvider.key].text }}
          </UiCallout>
        </template>

        <UiCallout v-else tone="info"> No credential required. </UiCallout>
      </div>

      <UiCallout v-else tone="info">
        Enable a plugin before adding provider connections.
      </UiCallout>

      <template #footer>
        <UiButton variant="ghost" @click="addPanelOpen = false"> Cancel </UiButton>
        <UiButton
          v-if="selectedProvider && selectedMethod(selectedProvider)?.interactive"
          variant="secondary"
          icon-left="external-link"
          :loading="isProviderBusy(selectedProvider.key, 'start')"
          @click="startProvider(selectedProvider)"
        >
          Start setup
        </UiButton>
        <UiButton
          v-if="selectedProvider"
          variant="primary"
          icon-left="save"
          :loading="isProviderBusy(selectedProvider.key, 'save')"
          :disabled="selectedMethod(selectedProvider)?.payload_format === 'none'"
          @click="saveCredential(selectedProvider)"
        >
          Save connection
        </UiButton>
      </template>
    </UiSidePanel>

    <UiSidePanel
      v-model="botProfilePanelOpen"
      title="Telegram bot profile"
      description="Configure static bot policy. Secrets stay in the selected connection."
      size="lg"
    >
      <div class="grid gap-4">
        <UiCallout v-if="botProfileMessage" :tone="botProfileMessage.tone">
          {{ botProfileMessage.text }}
        </UiCallout>

        <UiFormField
          label="Bot profile key"
          help="Project-scoped key used by webhook paths and agent-readable setup."
          required
        >
          <template #default="{ id, describedBy, invalid }">
            <UiInput
              :id="id"
              v-model="botProfileForm.key"
              :aria-describedby="describedBy"
              :invalid="invalid"
              placeholder="support-bot"
            />
          </template>
        </UiFormField>

        <UiFormField
          label="Telegram connection"
          help="Only the profile key is exposed here; the token stays daemon-side."
          required
        >
          <template #default="{ id, describedBy, invalid }">
            <UiSelect
              :id="id"
              v-model="botProfileForm.auth_profile_key"
              :options="telegramConnectionOptions"
              :aria-describedby="describedBy"
              :invalid="invalid"
              placeholder="Select connection"
            />
          </template>
        </UiFormField>

        <UiCallout v-if="botProfileForm.auth_profile_key" tone="info">
          Telegram identity:
          {{
            botUsernameFromConnection(telegramConnectionForProfile(botProfileForm.auth_profile_key))
              ? `@${botUsernameFromConnection(telegramConnectionForProfile(botProfileForm.auth_profile_key))}`
              : 'test the selected connection to fetch it from Telegram'
          }}
        </UiCallout>

        <UiFormField label="Display name" required>
          <template #default="{ id, describedBy, invalid }">
            <UiInput
              :id="id"
              v-model="botProfileForm.identity_display_name"
              :aria-describedby="describedBy"
              :invalid="invalid"
              placeholder="Support Bot"
            />
          </template>
        </UiFormField>

        <UiFormField label="Purpose">
          <template #default="{ id, describedBy, invalid }">
            <UiTextarea
              :id="id"
              v-model="botProfileForm.identity_purpose"
              :aria-describedby="describedBy"
              :invalid="invalid"
              :rows="3"
              placeholder="Handle support requests from approved Telegram users."
            />
          </template>
        </UiFormField>

        <UiFormField label="Voice">
          <template #default="{ id, describedBy, invalid }">
            <UiTextarea
              :id="id"
              v-model="botProfileForm.identity_voice"
              :aria-describedby="describedBy"
              :invalid="invalid"
              :rows="2"
              placeholder="Clear, concise, and operational."
            />
          </template>
        </UiFormField>

        <UiFormField
          label="Agent instructions"
          help="Static guidance attached to every agent request created by this bot."
        >
          <template #default="{ id, describedBy, invalid }">
            <UiTextarea
              :id="id"
              v-model="botProfileForm.agent_default_instructions"
              :aria-describedby="describedBy"
              :invalid="invalid"
              :rows="4"
              placeholder="Triage the request, inspect relevant project context, and reply only when the next action is clear."
            />
          </template>
        </UiFormField>

        <div class="grid gap-4 sm:grid-cols-2">
          <UiFormField label="Boundaries">
            <template #default="{ id, describedBy, invalid }">
              <UiTextarea
                :id="id"
                v-model="botProfileForm.agent_boundaries"
                :aria-describedby="describedBy"
                :invalid="invalid"
                :rows="3"
                placeholder="Do not change accounts, spend budget, or promise outcomes without explicit approval."
              />
            </template>
          </UiFormField>

          <UiFormField label="Escalation">
            <template #default="{ id, describedBy, invalid }">
              <UiTextarea
                :id="id"
                v-model="botProfileForm.agent_escalation"
                :aria-describedby="describedBy"
                :invalid="invalid"
                :rows="3"
                placeholder="Escalate billing, legal, or destructive actions before executing."
              />
            </template>
          </UiFormField>
        </div>

        <div class="grid gap-4 sm:grid-cols-2">
          <UiFormField
            label="Allowed chats"
            help="Comma-separated StackOS refs, for example telegram-chat:999."
            required
          >
            <template #default="{ id, describedBy, invalid }">
              <UiInput
                :id="id"
                v-model="botProfileForm.allowed_chat_refs"
                :aria-describedby="describedBy"
                :invalid="invalid"
                placeholder="telegram-chat:999"
              />
            </template>
          </UiFormField>

          <UiFormField
            label="Allowed users"
            help="Comma-separated StackOS refs, for example telegram-user:555."
            required
          >
            <template #default="{ id, describedBy, invalid }">
              <UiInput
                :id="id"
                v-model="botProfileForm.allowed_user_refs"
                :aria-describedby="describedBy"
                :invalid="invalid"
                placeholder="telegram-user:555"
              />
            </template>
          </UiFormField>
        </div>

        <div class="grid gap-4">
          <UiFormField label="Mentions">
            <template #default="{ id, describedBy, invalid }">
              <UiInput
                :id="id"
                v-model="botProfileForm.mention_patterns"
                :aria-describedby="describedBy"
                :invalid="invalid"
                placeholder="support, ops"
              />
            </template>
          </UiFormField>
        </div>

        <div class="grid gap-3 rounded-md border border-subtle bg-bg-surface-alt p-3">
          <div class="flex items-center justify-between gap-3">
            <div>
              <h3 class="text-sm font-semibold text-fg-strong">Command intents</h3>
              <p class="mt-0.5 text-xs text-fg-muted">
                Optional triggers with guidance passed to the operating agent.
              </p>
            </div>
            <UiButton size="sm" variant="secondary" icon-left="plus" @click="addCommandDraft">
              Add command
            </UiButton>
          </div>

          <div
            v-for="(command, index) in botProfileForm.commands"
            :key="index"
            class="grid gap-3 rounded-md border border-subtle bg-bg-surface p-3"
          >
            <div class="grid gap-3 sm:grid-cols-[minmax(8rem,12rem)_1fr_auto] sm:items-start">
              <UiFormField label="Command">
                <template #default="{ id, describedBy, invalid }">
                  <UiInput
                    :id="id"
                    v-model="command.command"
                    :aria-describedby="describedBy"
                    :invalid="invalid"
                    placeholder="/support"
                  />
                </template>
              </UiFormField>

              <UiFormField label="Description">
                <template #default="{ id, describedBy, invalid }">
                  <UiInput
                    :id="id"
                    v-model="command.description"
                    :aria-describedby="describedBy"
                    :invalid="invalid"
                    placeholder="Handle support requests"
                  />
                </template>
              </UiFormField>

              <div class="pt-6">
                <UiButton
                  size="sm"
                  variant="ghost"
                  icon-left="trash"
                  @click="removeCommandDraft(index)"
                >
                  Remove
                </UiButton>
              </div>
            </div>

            <UiFormField label="Command guidance">
              <template #default="{ id, describedBy, invalid }">
                <UiTextarea
                  :id="id"
                  v-model="command.guidance"
                  :aria-describedby="describedBy"
                  :invalid="invalid"
                  :rows="3"
                  placeholder="Explain what the agent should gather, decide, and return for this command."
                />
              </template>
            </UiFormField>

            <UiCheckbox v-model="command.enabled" label="Command enabled" />
          </div>
        </div>

        <details class="rounded-md border border-subtle bg-bg-surface-alt">
          <summary class="cursor-pointer px-3 py-2 text-sm font-medium text-fg-default">
            Advanced delivery behavior
          </summary>
          <div class="grid gap-3 border-t border-subtle p-3">
            <UiCheckbox
              v-model="botProfileForm.store_non_trigger_messages"
              label="Store non-trigger messages"
            />
            <UiCheckbox
              v-model="botProfileForm.origin_required"
              label="Require origin-bound replies"
            />
            <UiCheckbox
              v-model="botProfileForm.reply_to_source_message"
              label="Reply to source message"
            />
            <UiCheckbox
              v-model="botProfileForm.same_thread"
              label="Use same thread when available"
            />
          </div>
        </details>
      </div>

      <template #footer>
        <UiButton variant="ghost" @click="botProfilePanelOpen = false"> Cancel </UiButton>
        <UiButton
          variant="primary"
          icon-left="save"
          :loading="busyAction === 'telegram-bot-profile:save'"
          @click="saveBotProfile"
        >
          Save bot profile
        </UiButton>
      </template>
    </UiSidePanel>
  </UiPageShell>
</template>
