<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'

import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import SubNav from '@/components/SubNav.vue'
import { UiButton, UiCallout, UiPageShell } from '@/components/ui'
import type { SchemaAuthProviderOut } from '@/api'
import { useConnectionForm } from '@/composables/useConnectionForm'
import { formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { useStackOsCatalogStore } from '@/stores/plugins'
import AddConnectionPanel from './connections/AddConnectionPanel.vue'
import BotsPanel from './connections/BotsPanel.vue'
import ChannelsPanel from './connections/ChannelsPanel.vue'
import ConnectedServicesPanel from './connections/ConnectedServicesPanel.vue'
import ConnectionDiagnosticsPanel from './connections/ConnectionDiagnosticsPanel.vue'
import ConnectionsOverviewPanel from './connections/ConnectionsOverviewPanel.vue'
import ConnectivityPanel from './connections/ConnectivityPanel.vue'
import ConnectivitySetupPanel from './connections/ConnectivitySetupPanel.vue'
import DestinationsPanel from './connections/DestinationsPanel.vue'
import HandoffRulesPanel from './connections/HandoffRulesPanel.vue'
import SlackBotSidePanel from './connections/SlackBotSidePanel.vue'
import TelegramProfileSidePanel from './connections/TelegramProfileSidePanel.vue'
import {
  botUsernameFromConnection,
  compareConnections,
  connectionTitle,
  credentialTestMessage,
  parseCsv,
  preferredTelegramConnection,
  providerGroupLabel,
  serviceName,
  slackFacet,
  slackFacetFromConnection,
  slackIdentified,
  slackProfileAuthKey,
  telegramConnectionForProfile,
  telegramFacet,
  telegramProfileAuthKey,
  telegramProfileIngressMode,
  telegramProfileUsername,
  toCommandDrafts,
  toCommandSpecs,
} from './connections/formatters'
import type {
  AuthMethod,
  CommunicationProfile,
  CommunicationProfileListOut,
  CommunicationRoute,
  CommunicationRouteListOut,
  CommunicationSurface,
  CommunicationSurfaceListOut,
  CommunicationTarget,
  CommunicationTargetListOut,
  ConnectionRow,
  ConnectionSection,
  IngressEndpointStatusOut,
  IngressForm,
  MessageMap,
  MessageTone,
  ServiceGroup,
  SlackProfileForm,
  TelegramCommandSpec,
  TelegramProfileForm,
} from './connections/types'

const SECTION_KEYS: ConnectionSection[] = [
  'overview',
  'services',
  'bots',
  'channels',
  'destinations',
  'handoff-rules',
  'connectivity',
  'diagnostics',
]

const route = useRoute()
const router = useRouter()
const catalogStore = useStackOsCatalogStore()
const { authProviders, authStatus, enabledPlugins, loading, error } = storeToRefs(catalogStore)

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const addPanelOpen = ref(false)
const {
  selectedProviderKey,
  authMethods,
  selectedMethodKey,
  selectedMethod,
  setSelectedMethod,
  supportsCredential,
  canAddProvider,
  inputType,
  isSecretField,
  primaryCredentialFields,
  advancedCredentialFields,
  fieldOptions,
  hasFieldOptions,
  fieldValue,
  setFieldValue,
  profileValue,
  setProfileValue,
  labelValue,
  setLabelValue,
  setSelectedProvider,
  clearForm,
} = useConnectionForm()
const busyAction = ref<string | null>(null)
const providerMessages = ref<MessageMap>({})
const connectionMessages = ref<MessageMap>({})
const activeSection = ref<ConnectionSection>('overview')
const telegramProfilePanelOpen = ref(false)
const telegramProfileMessage = ref<{ tone: MessageTone; text: string } | null>(null)
const communicationProfiles = ref<CommunicationProfile[]>([])
const communicationTargets = ref<CommunicationTarget[]>([])
const communicationSurfaces = ref<CommunicationSurface[]>([])
const communicationRoutes = ref<CommunicationRoute[]>([])
const ingressStatus = ref<IngressEndpointStatusOut | null>(null)
const communicationSetupLoading = ref(false)
const communicationSetupMessage = ref<{ tone: MessageTone; text: string } | null>(null)
const ingressSetupOpen = ref(false)
const ingressMessage = ref<{ tone: MessageTone; text: string } | null>(null)
const ingressForm = ref<IngressForm>({
  driver: 'local-tunnel',
  public_base_url: '',
  discovery_url: 'http://127.0.0.1:4040/api/endpoints',
})
const telegramProfileForm = ref<TelegramProfileForm>({
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
  ],
  mention_patterns: '',
  store_non_trigger_messages: true,
  origin_required: true,
  reply_to_source_message: true,
  same_thread: true,
})
const slackProfilePanelOpen = ref(false)
const slackProfileMessage = ref<{ tone: MessageTone; text: string } | null>(null)
const isNewSlackProfile = ref(false)
const slackProfileForm = ref<SlackProfileForm>({
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
})
const slackProfileTeamLabel = computed(() => {
  const connection = slackConnections.value.find(
    (item) => item.profile_key === slackProfileForm.value.auth_profile_key,
  )
  const meta = connection?.account?.metadata_json as Record<string, unknown> | undefined
  const team = meta?.team ?? meta?.team_id
  return typeof team === 'string' ? team : ''
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
    group: providerGroupLabel(provider),
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

const slackConnections = computed(() =>
  connectedConnections.value.filter((connection) => connection.provider_key === 'slack-bot'),
)

const slackConnectionOptions = computed(() =>
  slackConnections.value.map((connection) => ({
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

const overviewLoading = computed(() => loading.value || communicationSetupLoading.value)

const subNavGroups = computed(() => [
  {
    items: [
      { key: 'overview', label: 'Overview', icon: 'gauge' },
      { key: 'services', label: 'Services', icon: 'plug', count: connections.value.length },
    ],
  },
  {
    label: 'Messaging',
    items: [
      { key: 'bots', label: 'Bots', icon: 'chat', count: communicationProfiles.value.length },
      {
        key: 'channels',
        label: 'Channels',
        icon: 'megaphone',
        count: communicationSurfaces.value.length,
      },
      {
        key: 'destinations',
        label: 'Destinations',
        icon: 'arrow-right',
        count: communicationTargets.value.length,
      },
      {
        key: 'handoff-rules',
        label: 'Handoff rules',
        icon: 'git-branch',
        count: communicationRoutes.value.length,
      },
      {
        key: 'connectivity',
        label: 'Connectivity',
        icon: 'globe',
        count: ingressStatus.value?.routes?.length ?? 0,
      },
    ],
  },
  {
    items: [{ key: 'diagnostics', label: 'Diagnostics', icon: 'lifebuoy' }],
  },
])

function isConnectionSection(value: unknown): value is ConnectionSection {
  return typeof value === 'string' && SECTION_KEYS.includes(value as ConnectionSection)
}

function setActiveSection(value: string): void {
  if (!isConnectionSection(value) || value === activeSection.value) return
  activeSection.value = value
  void router.replace({ query: { ...route.query, section: value } })
}

function applySectionFromQuery(value: unknown): void {
  if (isConnectionSection(value)) activeSection.value = value
}

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  applySectionFromQuery(route.query.section)
  await catalogStore.refresh(projectId.value)
  await catalogStore.refreshAuth(projectId.value)
  await loadCommunicationSetup()
  applyProviderSelectionFromQuery(route.query.provider_key)
}

async function loadCommunicationSetup(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  communicationSetupLoading.value = true
  try {
    const [profiles, targets, surfaces, routes, ingress] = await Promise.all([
      callOperation<CommunicationProfileListOut>('communicationProfile.list', {
        project_id: projectId.value,
        limit: 50,
      }),
      callOperation<CommunicationTargetListOut>('communicationTarget.list', {
        project_id: projectId.value,
        limit: 50,
      }),
      callOperation<CommunicationSurfaceListOut>('communicationSurface.list', {
        project_id: projectId.value,
        limit: 50,
      }),
      callOperation<CommunicationRouteListOut>('communicationRoute.list', {
        project_id: projectId.value,
        limit: 50,
      }),
      callOperation<IngressEndpointStatusOut>('ingressEndpoint.status', {
        project_id: projectId.value,
      }),
    ])
    communicationProfiles.value = profiles.items ?? []
    communicationTargets.value = targets.items ?? []
    communicationSurfaces.value = surfaces.items ?? []
    communicationRoutes.value = routes.items ?? []
    ingressStatus.value = ingress ?? null
    communicationSetupMessage.value = null
  } catch (err) {
    communicationSetupMessage.value = {
      tone: 'danger',
      text: formatApiError(err, 'failed to load communication setup'),
    }
  } finally {
    communicationSetupLoading.value = false
  }
}

function openIngressSetup(): void {
  const endpoint = ingressStatus.value?.endpoint
  ingressForm.value = {
    driver: endpoint?.driver === 'public-url' ? 'public-url' : 'local-tunnel',
    public_base_url: endpoint?.public_base_url ?? '',
    discovery_url: ingressForm.value.discovery_url || 'http://127.0.0.1:4040/api/endpoints',
  }
  ingressMessage.value = null
  ingressSetupOpen.value = true
}

async function saveIngressSetup(): Promise<void> {
  const form = ingressForm.value
  if (form.driver === 'public-url' && !form.public_base_url.trim()) {
    ingressMessage.value = { tone: 'danger', text: 'A public address is required.' }
    return
  }
  const discoveryUrl = form.discovery_url.trim() || 'http://127.0.0.1:4040/api/endpoints'
  busyAction.value = 'ingress:configure'
  try {
    await callOperation('ingressEndpoint.configure', {
      project_id: projectId.value,
      driver: form.driver,
      enabled: true,
      ...(form.driver === 'public-url'
        ? { public_base_url: form.public_base_url.trim() }
        : { driver_config: { provider: 'ngrok', discovery_url: discoveryUrl } }),
    })
    if (form.driver === 'local-tunnel') {
      // A local tunnel only has an address once it's discovered — configure
      // alone leaves it pending, so run the discovery pass before reloading.
      await callOperation('ingressEndpoint.refresh', {
        project_id: projectId.value,
        driver_config: { provider: 'ngrok', discovery_url: discoveryUrl },
        sync_profiles: true,
      })
    }
    ingressMessage.value = { tone: 'success', text: 'Connectivity configured.' }
    ingressSetupOpen.value = false
    await loadCommunicationSetup()
  } catch (err) {
    ingressMessage.value = {
      tone: 'danger',
      text: formatApiError(err, 'failed to configure connectivity'),
    }
  } finally {
    busyAction.value = null
  }
}

async function syncIngress(): Promise<void> {
  busyAction.value = 'ingress:sync'
  try {
    await callOperation('ingressEndpoint.sync', {
      project_id: projectId.value,
      apply_provider_webhooks: true,
    })
    await loadCommunicationSetup()
    ingressMessage.value = { tone: 'success', text: 'Synced each bot’s webhook to its provider.' }
  } catch (err) {
    ingressMessage.value = { tone: 'danger', text: formatApiError(err, 'failed to sync webhooks') }
  } finally {
    busyAction.value = null
  }
}

function ensureSelectableProvider(): void {
  const providers = visibleAuthProviders.value
  if (!selectedProviderKey.value && providers[0]) {
    selectedProviderKey.value = providers[0].key
    return
  }
  if (
    selectedProviderKey.value &&
    providers.length > 0 &&
    !providers.some((provider) => provider.key === selectedProviderKey.value)
  ) {
    selectedProviderKey.value = providers[0].key
  }
}

function applyProviderSelectionFromQuery(value: unknown): void {
  ensureSelectableProvider()
  const providerKey = typeof value === 'string' ? value : ''
  if (!providerKey) return
  const provider = providerByKey.value.get(providerKey)
  if (!provider || !canAddProvider(provider)) return
  selectedProviderKey.value = providerKey
  addPanelOpen.value = true
}

function openAddConnection(providerKey?: string): void {
  if (providerKey) selectedProviderKey.value = providerKey
  if (!selectedProviderKey.value && visibleAuthProviders.value[0]) {
    selectedProviderKey.value = visibleAuthProviders.value[0].key
  }
  addPanelOpen.value = true
}

function openAddTelegramProfile(): void {
  const preferred = preferredTelegramConnection(
    identifiedTelegramConnections.value,
    telegramConnections.value,
  )
  if (!telegramProfileForm.value.auth_profile_key && preferred) {
    telegramProfileForm.value = {
      ...telegramProfileForm.value,
      auth_profile_key: preferred.profile_key,
    }
  }
  telegramProfileMessage.value = null
  telegramProfilePanelOpen.value = true
}

function editTelegramProfile(profile: CommunicationProfile): void {
  const facet = telegramFacet(profile)
  const rawCommands = profile.trigger_policy['commands']
  const rawMentionPatterns = profile.trigger_policy['mention_patterns']
  const commands = Array.isArray(rawCommands) ? (rawCommands as TelegramCommandSpec[]) : []
  const mentionPatterns = Array.isArray(rawMentionPatterns) ? (rawMentionPatterns as string[]) : []
  telegramProfileForm.value = {
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
  if (typeof facet.bot_username === 'string' && !telegramProfileForm.value.bot_username) {
    telegramProfileForm.value.bot_username = facet.bot_username.replace(/^@/, '')
  }
  telegramProfileMessage.value = null
  telegramProfilePanelOpen.value = true
}

/** Route the Bots "Configure" action to the right provider editor. */
function editBot(profile: CommunicationProfile): void {
  if (profile.provider_facets?.['telegram-bot']) {
    editTelegramProfile(profile)
  } else if (profile.provider_facets?.['slack-bot']) {
    editSlackProfile(profile)
  }
}

function editSlackProfile(profile: CommunicationProfile): void {
  const access = profile.access_policy
  const rawMentions = profile.trigger_policy['mention_patterns']
  slackProfileForm.value = {
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
  isNewSlackProfile.value = false
  slackProfileMessage.value = null
  slackProfilePanelOpen.value = true
}

function openAddBot(provider: string): void {
  if (provider === 'slack-bot') openAddSlackProfile()
  else openAddTelegramProfile()
}

function openAddSlackProfile(): void {
  const preferred = slackConnections.value[0]
  slackProfileForm.value = {
    key: 'slack-bot',
    auth_profile_key: preferred?.profile_key ?? '',
    identity_display_name: 'Slack Bot',
    identity_purpose: '',
    identity_voice: 'Clear, concise, and operational.',
    agent_default_instructions: '',
    agent_boundaries: '',
    agent_escalation: '',
    allowed_chat_refs: '',
    allowed_user_refs: '',
    mention_patterns: '',
  }
  isNewSlackProfile.value = true
  slackProfileMessage.value = null
  slackProfilePanelOpen.value = true
}

function communicationProfileByKey(key: string): CommunicationProfile | null {
  return communicationProfiles.value.find((profile) => profile.key === key) ?? null
}

function connectionActionKey(credentialRef: string, action: string): string {
  return `${credentialRef}:${action}`
}

function providerActionKey(providerKey: string, action: string): string {
  return `${providerKey}:${action}`
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

function addCommandDraft(): void {
  telegramProfileForm.value.commands = [
    ...telegramProfileForm.value.commands,
    { command: '', description: '', guidance: '', enabled: true },
  ]
}

function removeCommandDraft(index: number): void {
  telegramProfileForm.value.commands = telegramProfileForm.value.commands.filter((_, itemIndex) => {
    return itemIndex !== index
  })
  if (telegramProfileForm.value.commands.length === 0) addCommandDraft()
}

function ensureTelegramProfileDefaults(): void {
  const preferred = preferredTelegramConnection(
    identifiedTelegramConnections.value,
    telegramConnections.value,
  )
  if (!telegramProfileForm.value.auth_profile_key && preferred) {
    telegramProfileForm.value = {
      ...telegramProfileForm.value,
      auth_profile_key: preferred.profile_key,
    }
  }
}

async function saveTelegramProfile(): Promise<void> {
  ensureTelegramProfileDefaults()
  const form = telegramProfileForm.value
  const key = form.key.trim()
  const authProfileKey = form.auth_profile_key.trim()
  const allowedChatRefs = parseCsv(form.allowed_chat_refs)
  const allowedUserRefs = parseCsv(form.allowed_user_refs)
  const identityDisplayName = form.identity_display_name.trim()
  const commands = toCommandSpecs(form.commands)
  if (!key) {
    telegramProfileMessage.value = { tone: 'danger', text: 'Telegram profile key is required.' }
    return
  }
  if (!identityDisplayName) {
    telegramProfileMessage.value = { tone: 'danger', text: 'Bot display name is required.' }
    return
  }
  if (!authProfileKey) {
    telegramProfileMessage.value = { tone: 'danger', text: 'Choose a Telegram connection.' }
    return
  }
  const selectedTelegramConnection = telegramConnectionForProfile(
    authProfileKey,
    telegramConnections.value,
  )
  const botUsername = botUsernameFromConnection(selectedTelegramConnection)
  if (!botUsername) {
    telegramProfileMessage.value = {
      tone: 'danger',
      text: 'Test the Telegram connection first so StackOS can fetch the bot identity from Telegram.',
    }
    return
  }
  if (allowedUserRefs.length === 0) {
    telegramProfileMessage.value = {
      tone: 'danger',
      text: 'Allowlisted users are required before the bot can trigger agents.',
    }
    return
  }
  busyAction.value = 'telegram-profile:save'
  try {
    const existing = communicationProfileByKey(key)
    const existingFacets = existing?.provider_facets ?? {}
    const existingTelegramFacet = existing ? telegramFacet(existing) : {}
    const existingIngressMode = existing ? telegramProfileIngressMode(existing) : ''
    await callOperation('communicationProfile.upsert', {
      project_id: projectId.value,
      key,
      identity: {
        ...(existing?.identity ?? {}),
        display_name: identityDisplayName,
        purpose: form.identity_purpose.trim(),
        voice: form.identity_voice.trim(),
      },
      provider_facets: {
        ...existingFacets,
        'telegram-bot': {
          ...existingTelegramFacet,
          auth_profile_key: authProfileKey,
          bot_username: botUsername,
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
        ...(existing?.agent_guidance ?? {}),
        default_instructions: form.agent_default_instructions.trim(),
        boundaries: form.agent_boundaries.trim(),
        escalation: form.agent_escalation.trim(),
      },
      access_policy: {
        ...(existing?.access_policy ?? {}),
        dm_mode: 'all',
        group_mode: 'all',
        user_mode: 'allowlist',
        allowed_chat_refs: allowedChatRefs,
        allowed_user_refs: allowedUserRefs,
      },
      trigger_policy: {
        ...(existing?.trigger_policy ?? {}),
        dm_trigger: 'always',
        group_trigger: 'mention_or_command',
        commands,
        mention_patterns: parseCsv(form.mention_patterns),
        reply_to_bot_triggers: true,
      },
      visibility_policy: {
        ...(existing?.visibility_policy ?? {}),
        store_non_trigger_messages: form.store_non_trigger_messages,
      },
      context_policy: existing?.context_policy ?? {},
      response_policy: {
        ...(existing?.response_policy ?? {}),
        reply_in_same_chat: true,
        origin_required: form.origin_required,
        reply_to_source_message: form.reply_to_source_message,
        same_thread: form.same_thread,
      },
      send_policy: existing?.send_policy ?? { mode: 'explicit-targets' },
      handoff_policy: existing?.handoff_policy ?? { mode: 'explicit-targets' },
      approval_policy: existing?.approval_policy ?? { mode: 'none' },
      metadata_json: existing?.metadata_json ?? {},
    })
    telegramProfileMessage.value = { tone: 'success', text: `Saved ${key}.` }
    telegramProfilePanelOpen.value = false
    await loadCommunicationSetup()
  } catch (err) {
    telegramProfileMessage.value = {
      tone: 'danger',
      text: formatApiError(err, 'failed to save Telegram profile'),
    }
  } finally {
    busyAction.value = null
  }
}

async function saveSlackProfile(): Promise<void> {
  const form = slackProfileForm.value
  const key = form.key.trim()
  const authProfileKey = form.auth_profile_key.trim()
  const displayName = form.identity_display_name.trim()
  const allowedUserRefs = parseCsv(form.allowed_user_refs)
  const allowedSurfaceRefs = parseCsv(form.allowed_chat_refs)
  if (!key) {
    slackProfileMessage.value = { tone: 'danger', text: 'Bot key is required.' }
    return
  }
  if (!displayName) {
    slackProfileMessage.value = { tone: 'danger', text: 'Bot display name is required.' }
    return
  }
  if (!authProfileKey) {
    slackProfileMessage.value = { tone: 'danger', text: 'Choose a Slack connection.' }
    return
  }
  if (allowedUserRefs.length === 0) {
    slackProfileMessage.value = {
      tone: 'danger',
      text: 'Allowlisted users are required before the bot can trigger agents.',
    }
    return
  }
  const existing = communicationProfileByKey(key)
  const connection =
    slackConnections.value.find((item) => item.profile_key === authProfileKey) ?? null
  if (!existing && !slackIdentified(connection)) {
    slackProfileMessage.value = {
      tone: 'danger',
      text: 'Test the Slack connection first so StackOS can fetch the workspace identity from Slack.',
    }
    return
  }
  const baseFacet = existing ? slackFacet(existing) : slackFacetFromConnection(connection)
  const accessPolicy = existing
    ? {
        ...existing.access_policy,
        user_mode: 'allowlist',
        allowed_user_refs: allowedUserRefs,
        allowed_surface_refs: allowedSurfaceRefs,
      }
    : {
        dm_mode: 'all',
        channel_mode: 'all',
        group_mode: 'all',
        user_mode: 'allowlist',
        allowed_user_refs: allowedUserRefs,
        allowed_surface_refs: allowedSurfaceRefs,
      }
  const triggerPolicy = existing
    ? { ...existing.trigger_policy, mention_patterns: parseCsv(form.mention_patterns) }
    : {
        dm_trigger: 'always',
        channel_trigger: 'mention_or_command',
        mention_patterns: parseCsv(form.mention_patterns),
        reply_to_bot_triggers: true,
      }
  busyAction.value = 'slack-profile:save'
  try {
    await callOperation('communicationProfile.upsert', {
      project_id: projectId.value,
      key,
      identity: {
        ...(existing?.identity ?? {}),
        display_name: displayName,
        purpose: form.identity_purpose.trim(),
        voice: form.identity_voice.trim(),
      },
      provider_facets: {
        ...(existing?.provider_facets ?? {}),
        'slack-bot': { ...baseFacet, auth_profile_key: authProfileKey },
      },
      agent_guidance: {
        ...(existing?.agent_guidance ?? {}),
        default_instructions: form.agent_default_instructions.trim(),
        boundaries: form.agent_boundaries.trim(),
        escalation: form.agent_escalation.trim(),
      },
      access_policy: accessPolicy,
      trigger_policy: triggerPolicy,
      visibility_policy: existing?.visibility_policy ?? {},
      context_policy: existing?.context_policy ?? {},
      response_policy: existing?.response_policy ?? {},
      send_policy: existing?.send_policy ?? { mode: 'explicit-targets' },
      handoff_policy: existing?.handoff_policy ?? { mode: 'explicit-targets' },
      approval_policy: existing?.approval_policy ?? { mode: 'none' },
      metadata_json: existing?.metadata_json ?? {},
    })
    slackProfileMessage.value = { tone: 'success', text: `Saved ${key}.` }
    slackProfilePanelOpen.value = false
    await loadCommunicationSetup()
  } catch (err) {
    slackProfileMessage.value = {
      tone: 'danger',
      text: formatApiError(err, 'failed to save Slack bot'),
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
  const profileKey = profileValue(provider.key, method.key).trim() || 'default'
  const label = labelValue(provider.key, method.key).trim()
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
    if (provider.key === 'telegram-bot' || provider.key === 'slack-bot') {
      const testResponse = await catalogStore.testCredential(projectId.value, {
        credential_ref: response.data.credential_ref,
      })
      message = testResponse.data.ok
        ? credentialTestMessage(
            provider.key,
            testResponse.data.metadata,
            `Connected ${response.data.credential_ref}.`,
          )
        : testResponse.data.summary
      tone = testResponse.data.ok ? 'success' : 'danger'
    }
    clearForm(provider.key, method.key)
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

async function testConnection(connection: ConnectionRow): Promise<void> {
  busyAction.value = connectionActionKey(connection.credential_ref, 'test')
  try {
    const response = await catalogStore.testCredential(projectId.value, {
      credential_ref: connection.credential_ref,
    })
    setConnectionMessage(
      connection.credential_ref,
      response.data.ok ? 'success' : 'danger',
      response.data.ok
        ? credentialTestMessage(
            response.data.provider_key,
            response.data.metadata,
            response.data.summary,
          )
        : response.data.summary,
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

async function revokeConnection(connection: ConnectionRow): Promise<void> {
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
onBeforeRouteUpdate((to) => {
  applySectionFromQuery(to.query.section)
  applyProviderSelectionFromQuery(to.query.provider_key)
})
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Connections"
      description="Connect the tools and messaging channels your agents use. Secrets stay on this machine — agents only get safe references."
      :breadcrumbs="[{ label: 'Connections' }]"
    >
      <template #actions>
        <UiButton
          variant="primary"
          size="sm"
          icon-left="plus"
          @click="openAddConnection()"
        >
          Add connection
        </UiButton>
      </template>
    </ProjectPageHeader>

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <div class="flex flex-col gap-5 lg:flex-row lg:items-start">
      <SubNav
        class="lg:sticky lg:top-4 lg:w-52 lg:shrink-0"
        :groups="subNavGroups"
        :active-key="activeSection"
        aria-label="Connection sections"
        @change="setActiveSection"
      />

      <div class="min-w-0 flex-1">
        <div
          role="tabpanel"
          aria-labelledby="cs-subnav-overview"
          :hidden="activeSection !== 'overview'"
        >
          <ConnectionsOverviewPanel
            :loading="overviewLoading"
            :connected-service-count="connectedServiceCount"
            :active-connections-count="activeConnections.length"
            :attention-connections="attentionConnections"
            :service-groups-count="serviceGroups.length"
            :bots="communicationProfiles"
            :channels="communicationSurfaces"
            :destinations="communicationTargets"
            :ingress-status="ingressStatus"
            @navigate="setActiveSection"
            @add-connection="openAddConnection()"
            @add-bot="openAddTelegramProfile"
          />
        </div>

        <div
          role="tabpanel"
          aria-labelledby="cs-subnav-services"
          :hidden="activeSection !== 'services'"
        >
          <ConnectedServicesPanel
            :loading="loading"
            :service-groups="serviceGroups"
            :connections-count="connections.length"
            :connection-messages="connectionMessages"
            :busy-action="busyAction"
            :can-add-provider="canAddProvider"
            @add-connection="openAddConnection"
            @test-connection="testConnection"
            @revoke-connection="revokeConnection"
          />
        </div>

        <div
          role="tabpanel"
          aria-labelledby="cs-subnav-bots"
          :hidden="activeSection !== 'bots'"
        >
          <BotsPanel
            :bots="communicationProfiles"
            :telegram-connections="telegramConnections"
            :slack-connections="slackConnections"
            :loading="communicationSetupLoading"
            :message="telegramProfileMessage ?? slackProfileMessage"
            @add-connection="openAddConnection"
            @add-bot="openAddBot"
            @edit-bot="editBot"
          />
        </div>

        <div
          role="tabpanel"
          aria-labelledby="cs-subnav-channels"
          :hidden="activeSection !== 'channels'"
        >
          <ChannelsPanel
            :channels="communicationSurfaces"
            :loading="communicationSetupLoading"
            :message="communicationSetupMessage"
            @refresh="loadCommunicationSetup"
          />
        </div>

        <div
          role="tabpanel"
          aria-labelledby="cs-subnav-destinations"
          :hidden="activeSection !== 'destinations'"
        >
          <DestinationsPanel
            :destinations="communicationTargets"
            :loading="communicationSetupLoading"
            :message="communicationSetupMessage"
            @refresh="loadCommunicationSetup"
          />
        </div>

        <div
          role="tabpanel"
          aria-labelledby="cs-subnav-handoff-rules"
          :hidden="activeSection !== 'handoff-rules'"
        >
          <HandoffRulesPanel
            :routes="communicationRoutes"
            :loading="communicationSetupLoading"
            :message="communicationSetupMessage"
            @refresh="loadCommunicationSetup"
          />
        </div>

        <div
          role="tabpanel"
          aria-labelledby="cs-subnav-connectivity"
          :hidden="activeSection !== 'connectivity'"
        >
          <ConnectivityPanel
            :ingress-status="ingressStatus"
            :loading="communicationSetupLoading"
            :syncing="busyAction === 'ingress:sync'"
            :message="ingressMessage ?? communicationSetupMessage"
            @refresh="loadCommunicationSetup"
            @configure="openIngressSetup"
            @sync="syncIngress"
          />
        </div>

        <div
          role="tabpanel"
          aria-labelledby="cs-subnav-diagnostics"
          :hidden="activeSection !== 'diagnostics'"
        >
          <ConnectionDiagnosticsPanel :auth-status="authStatus" />
        </div>
      </div>
    </div>

    <AddConnectionPanel
      v-model="addPanelOpen"
      :selected-provider="selectedProvider"
      :visible-auth-providers="visibleAuthProviders"
      :provider-options="providerOptions"
      :provider-messages="providerMessages"
      :busy-action="busyAction"
      :auth-methods="authMethods"
      :selected-method-key="selectedMethodKey"
      :selected-method="selectedMethod"
      :supports-credential="supportsCredential"
      :input-type="inputType"
      :is-secret-field="isSecretField"
      :primary-credential-fields="primaryCredentialFields"
      :advanced-credential-fields="advancedCredentialFields"
      :has-field-options="hasFieldOptions"
      :field-options="fieldOptions"
      :profile-value="profileValue"
      :set-profile-value="setProfileValue"
      :label-value="labelValue"
      :set-label-value="setLabelValue"
      :field-value="fieldValue"
      :set-field-value="setFieldValue"
      @select-provider="setSelectedProvider"
      @select-method="setSelectedMethod"
      @start-provider="startProvider"
      @save-credential="saveCredential"
    />

    <TelegramProfileSidePanel
      v-model="telegramProfilePanelOpen"
      v-model:form="telegramProfileForm"
      :telegram-connection-options="telegramConnectionOptions"
      :telegram-connections="telegramConnections"
      :message="telegramProfileMessage"
      :busy-action="busyAction"
      @save="saveTelegramProfile"
      @add-command="addCommandDraft"
      @remove-command="removeCommandDraft"
    />

    <SlackBotSidePanel
      v-model="slackProfilePanelOpen"
      v-model:form="slackProfileForm"
      :is-new="isNewSlackProfile"
      :slack-connection-options="slackConnectionOptions"
      :team-label="slackProfileTeamLabel"
      :message="slackProfileMessage"
      :busy-action="busyAction"
      @save="saveSlackProfile"
    />

    <ConnectivitySetupPanel
      v-model="ingressSetupOpen"
      v-model:form="ingressForm"
      :busy-action="busyAction"
      :message="ingressMessage"
      @save="saveIngressSetup"
    />
  </UiPageShell>
</template>
