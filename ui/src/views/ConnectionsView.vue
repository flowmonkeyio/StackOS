<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'

import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import {
  UiButton,
  UiCallout,
  UiMetricCard,
  UiPageShell,
  UiPanel,
  UiSegmentedControl,
} from '@/components/ui'
import type { SchemaAuthProviderOut } from '@/api'
import { useConnectionForm } from '@/composables/useConnectionForm'
import { formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { useStackOsCatalogStore } from '@/stores/plugins'
import AddConnectionPanel from './connections/AddConnectionPanel.vue'
import CommunicationSetupPanel from './connections/CommunicationSetupPanel.vue'
import ConnectedServicesPanel from './connections/ConnectedServicesPanel.vue'
import ConnectionDiagnosticsPanel from './connections/ConnectionDiagnosticsPanel.vue'
import TelegramProfileSidePanel from './connections/TelegramProfileSidePanel.vue'
import TelegramProfilesPanel from './connections/TelegramProfilesPanel.vue'
import {
  botUsernameFromConnection,
  compareConnections,
  connectionTitle,
  credentialTestMessage,
  parseCsv,
  preferredTelegramConnection,
  providerGroupLabel,
  serviceName,
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
  CommunicationSurface,
  CommunicationSurfaceListOut,
  CommunicationTarget,
  CommunicationTargetListOut,
  ConnectionRow,
  ConnectionSection,
  IngressEndpointStatusOut,
  MessageMap,
  MessageTone,
  ServiceGroup,
  TelegramCommandDraft,
  TelegramCommandSpec,
  TelegramProfileForm,
} from './connections/types'

const route = useRoute()
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
const activeSection = ref<ConnectionSection>('services')
const telegramProfilePanelOpen = ref(false)
const telegramProfileMessage = ref<{ tone: MessageTone; text: string } | null>(null)
const communicationProfiles = ref<CommunicationProfile[]>([])
const communicationTargets = ref<CommunicationTarget[]>([])
const communicationSurfaces = ref<CommunicationSurface[]>([])
const ingressStatus = ref<IngressEndpointStatusOut | null>(null)
const communicationSetupLoading = ref(false)
const communicationSetupMessage = ref<{ tone: MessageTone; text: string } | null>(null)
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

const telegramProfiles = computed(() =>
  communicationProfiles.value.filter((profile) =>
    Boolean(profile.provider_facets?.['telegram-bot']),
  ),
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

const connectionSectionOptions = computed(() => [
  { key: 'services', label: `Services ${connections.value.length}` },
  {
    key: 'communications',
    label: `Comms ${
      communicationProfiles.value.length +
      communicationSurfaces.value.length +
      communicationTargets.value.length +
      (ingressStatus.value?.routes?.length ?? 0)
    }`,
  },
  { key: 'telegram', label: `Telegram ${telegramProfiles.value.length}` },
  { key: 'diagnostics', label: 'Diagnostics' },
])

function setActiveSection(value: string | number): void {
  activeSection.value = String(value) as ConnectionSection
}

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  await catalogStore.refresh(projectId.value)
  await catalogStore.refreshAuth(projectId.value)
  await loadCommunicationSetup()
  syncProviderSelectionFromQuery()
}

async function loadCommunicationSetup(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  communicationSetupLoading.value = true
  try {
    const [profiles, targets, surfaces, ingress] = await Promise.all([
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
      callOperation<IngressEndpointStatusOut>('ingressEndpoint.status', {
        project_id: projectId.value,
      }),
    ])
    communicationProfiles.value = profiles.items ?? []
    communicationTargets.value = targets.items ?? []
    communicationSurfaces.value = surfaces.items ?? []
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

function syncProviderSelectionFromQuery(): void {
  const providerKey = typeof route.query.provider_key === 'string' ? route.query.provider_key : ''
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
      <UiMetricCard label="Connected services" :value="connectedServiceCount" density="compact" />
      <UiMetricCard
        label="Active connections"
        :value="activeConnections.length"
        density="compact"
      />
      <UiMetricCard
        label="Needs attention"
        :value="attentionConnections.length"
        density="compact"
      />
    </div>

    <UiPanel class="p-3">
      <div class="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <UiSegmentedControl
          :model-value="activeSection"
          :options="connectionSectionOptions"
          label="Connection page sections"
          size="md"
          @select="setActiveSection"
        />
        <p class="max-w-3xl text-sm text-fg-muted">
          Use this page for local-admin setup and read-only inspection. Agents receive safe refs,
          not secrets.
        </p>
      </div>
    </UiPanel>

    <ConnectedServicesPanel
      v-show="activeSection === 'services'"
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

    <CommunicationSetupPanel
      v-show="activeSection === 'communications'"
      :profiles="communicationProfiles"
      :targets="communicationTargets"
      :surfaces="communicationSurfaces"
      :ingress-status="ingressStatus"
      :loading="communicationSetupLoading"
      :message="communicationSetupMessage"
      @refresh="loadCommunicationSetup"
    />

    <TelegramProfilesPanel
      v-show="activeSection === 'telegram'"
      :telegram-connections="telegramConnections"
      :telegram-profiles="telegramProfiles"
      :loading="communicationSetupLoading"
      :message="telegramProfileMessage"
      @add-connection="openAddConnection"
      @add-profile="openAddTelegramProfile"
      @edit-profile="editTelegramProfile"
    />

    <ConnectionDiagnosticsPanel
      v-show="activeSection === 'diagnostics'"
      :auth-status="authStatus"
    />

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
  </UiPageShell>
</template>
