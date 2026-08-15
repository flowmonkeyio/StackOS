<script setup lang="ts">
import { computed, ref } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'

import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import SubNav from '@/components/SubNav.vue'
import { UiButton, UiCallout, UiConfirmDialog, UiPageShell, UiSkeleton } from '@/components/ui'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { useProjectScopedLoader } from '@/composables/useProjectScopedLoader'
import AddAccountPanel from './accounts/AddAccountPanel.vue'
import { useAccountCredentials } from './accounts/useAccountCredentials'
import AttachAccountPanel from './connections/AttachAccountPanel.vue'
import BotsPanel from './connections/BotsPanel.vue'
import ChannelsPanel from './connections/ChannelsPanel.vue'
import ConnectedServicesPanel from './connections/ConnectedServicesPanel.vue'
import ConnectionDiagnosticsPanel from './connections/ConnectionDiagnosticsPanel.vue'
import ConnectivityPanel from './connections/ConnectivityPanel.vue'
import ConnectivitySetupPanel from './connections/ConnectivitySetupPanel.vue'
import DestinationsPanel from './connections/DestinationsPanel.vue'
import HandoffRulesPanel from './connections/HandoffRulesPanel.vue'
import SlackBotSidePanel from './connections/SlackBotSidePanel.vue'
import TelegramProfileSidePanel from './connections/TelegramProfileSidePanel.vue'
import { useCommunicationTopology } from './connections/useCommunicationTopology'
import { useConnectionCredentials } from './connections/useConnectionCredentials'
import { useIngressEndpointEditor } from './connections/useIngressEndpointEditor'
import { useSlackProfileEditor } from './connections/useSlackProfileEditor'
import { useTelegramProfileEditor } from './connections/useTelegramProfileEditor'
import type {
  CommunicationProfile,
  ConnectionSection,
  OAuthReturnStatus,
} from './connections/types'

const SECTION_KEYS: ConnectionSection[] = [
  'services',
  'bots',
  'channels',
  'destinations',
  'handoff-rules',
  'connectivity',
  'diagnostics',
]
const OAUTH_RETURN_STATUSES = new Set<OAuthReturnStatus>([
  'connected',
  'denied',
  'expired',
  'repair-required',
  'error',
])

const route = useRoute()
const router = useRouter()

const { projectId, changesProjectScope } = useProjectRouteScope(route)
const accountAttachProjectId = computed<number | null>(() => projectId.value)
const initialLoadComplete = ref(false)
const {
  authStatus,
  loading,
  error,
  attachPanelOpen,
  selectedAccountRef,
  providerFilter,
  busyAction,
  connectionMessages,
  pendingDetach,
  accountOptions,
  activeConnections,
  connectedConnections,
  attentionConnections,
  serviceGroups,
  connectedServiceCount,
  load: loadCredentials,
  openAddConnection,
  closeAttachPanel,
  selectAccount,
  attachSelectedAccount,
  requestDetach,
  confirmDetach,
} = useConnectionCredentials(projectId)
const {
  panelOpen: accountPanelOpen,
  busyAction: accountBusyAction,
  providerMessages,
  accountMessages,
  oauthReturnMessage,
  fieldErrors,
  editing,
  editingSecretPresent,
  authMethods,
  selectedMethodKey,
  selectedMethod,
  setSelectedMethod,
  supportsCredential,
  inputType,
  isSecretField,
  methodFields,
  fieldOptions,
  hasFieldOptions,
  fieldValue,
  setFieldValue,
  displayNameValue,
  setDisplayNameValue,
  setSelectedProvider,
  visibleAuthProviders,
  providerOptions,
  selectedProvider,
  openAddAccount,
  saveAccount: saveAccountAction,
  startProvider: startProviderAction,
  applyOAuthReturn,
} = useAccountCredentials(accountAttachProjectId)
const activeSection = ref<ConnectionSection>('services')
const {
  profiles: communicationProfiles,
  targets: communicationTargets,
  surfaces: communicationSurfaces,
  routes: communicationRoutes,
  loading: communicationTopologyLoading,
  message: communicationTopologyMessage,
  load: loadCommunicationTopology,
} = useCommunicationTopology(projectId)
const {
  status: ingressStatus,
  loading: ingressLoading,
  loadMessage: ingressLoadMessage,
  panelOpen: ingressSetupOpen,
  message: ingressMessage,
  form: ingressForm,
  load: loadIngressStatus,
  openPanel: openIngressSetup,
  save: saveIngressSetup,
  sync: syncIngress,
} = useIngressEndpointEditor({
  projectId,
  busyAction,
  onTopologyChanged: loadCommunicationTopology,
})
const communicationSetupLoading = computed(
  () => communicationTopologyLoading.value || ingressLoading.value,
)
const communicationSetupMessage = computed(
  () => communicationTopologyMessage.value ?? ingressLoadMessage.value,
)
const {
  panelOpen: telegramProfilePanelOpen,
  message: telegramProfileMessage,
  form: telegramProfileForm,
  connections: telegramConnections,
  connectionOptions: telegramConnectionOptions,
  openAdd: openAddTelegramProfile,
  edit: editTelegramProfile,
  addCommand: addCommandDraft,
  removeCommand: removeCommandDraft,
  save: saveTelegramProfile,
} = useTelegramProfileEditor({
  projectId,
  profiles: communicationProfiles,
  connectedConnections,
  busyAction,
  reload: loadCommunicationSetup,
})

const {
  panelOpen: slackProfilePanelOpen,
  message: slackProfileMessage,
  isNew: isNewSlackProfile,
  form: slackProfileForm,
  connections: slackConnections,
  connectionOptions: slackConnectionOptions,
  teamLabel: slackProfileTeamLabel,
  openAdd: openAddSlackProfile,
  edit: editSlackProfile,
  save: saveSlackProfile,
} = useSlackProfileEditor({
  projectId,
  profiles: communicationProfiles,
  connectedConnections,
  busyAction,
  reload: loadCommunicationSetup,
})

const subNavGroups = computed(() => [
  {
    label: 'Service setup',
    items: [
      { key: 'services', label: 'Services', icon: 'plug', count: activeConnections.value.length },
    ],
  },
  {
    label: 'Messaging setup',
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
    ],
  },
  {
    label: 'Inbound',
    items: [
      {
        key: 'connectivity',
        label: 'Connectivity',
        icon: 'globe',
        count: ingressStatus.value?.routes?.length ?? 0,
      },
    ],
  },
  {
    label: 'Advanced',
    items: [{ key: 'diagnostics', label: 'Technical diagnostics', icon: 'lifebuoy' }],
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
  initialLoadComplete.value = false
  try {
    applySectionFromQuery(route.query.section)
    await loadCredentials()
    await loadCommunicationSetup()
    if (!handleOAuthReturnQuery(route.query.oauth_status, route.query.provider_key)) {
      const providerKey =
        typeof route.query.provider_key === 'string' ? route.query.provider_key : undefined
      if (providerKey) openAddConnection(providerKey)
    }
  } finally {
    initialLoadComplete.value = true
  }
}

async function loadCommunicationSetup(): Promise<void> {
  await Promise.all([loadCommunicationTopology(), loadIngressStatus()])
}

/** Route the Bots "Configure" action to the right provider editor. */
function editBot(profile: CommunicationProfile): void {
  if (profile.provider_facets?.['telegram-bot']) {
    editTelegramProfile(profile)
  } else if (profile.provider_facets?.['slack-bot']) {
    editSlackProfile(profile)
  }
}

function openAddBot(provider: string): void {
  if (provider === 'slack-bot') openAddSlackProfile()
  else openAddTelegramProfile()
}

function clearProviderQuery(): void {
  if (!route.query.provider_key) return
  const nextQuery = { ...route.query }
  delete nextQuery.provider_key
  void router.replace({ query: nextQuery })
}

function oauthReturnStatus(value: unknown): OAuthReturnStatus | null {
  if (value === 'stale-attempt') return 'expired'
  return typeof value === 'string' && OAUTH_RETURN_STATUSES.has(value as OAuthReturnStatus)
    ? (value as OAuthReturnStatus)
    : null
}

function handleOAuthReturnQuery(statusValue: unknown, providerValue: unknown): boolean {
  const status = oauthReturnStatus(statusValue)
  if (!status) return false
  applyOAuthReturn(status, typeof providerValue === 'string' ? providerValue : null)
  clearOAuthReturnQuery()
  return true
}

function clearOAuthReturnQuery(): void {
  const nextQuery = { ...router.currentRoute.value.query }
  delete nextQuery.oauth_status
  delete nextQuery.provider_key
  void router.replace({ query: nextQuery })
}

function setAccountPanelOpen(open: boolean): void {
  accountPanelOpen.value = open
  if (!open) clearProviderQuery()
}

function openCreateAccount(): void {
  openAddAccount(providerFilter.value || undefined)
}

async function saveProjectAccount(...args: Parameters<typeof saveAccountAction>): Promise<void> {
  const credentialRef = await saveAccountAction(...args)
  if (credentialRef) {
    const accountMessage = accountMessages.value[credentialRef]
    if (accountMessage) {
      connectionMessages.value = {
        ...connectionMessages.value,
        [credentialRef]: accountMessage,
      }
    }
    closeAttachPanel()
    await loadCredentials()
  }
  if (!accountPanelOpen.value) clearProviderQuery()
}

async function startProvider(...args: Parameters<typeof startProviderAction>): Promise<void> {
  const authorizationUrl = await startProviderAction(...args)
  if (authorizationUrl) {
    window.open(authorizationUrl, '_self', 'noopener,noreferrer')
  }
}

async function refreshOAuthReturn(statusValue: unknown, providerValue: unknown): Promise<void> {
  await loadCredentials()
  handleOAuthReturnQuery(statusValue, providerValue)
}

useProjectScopedLoader({
  projectId,
  load,
})
onBeforeRouteUpdate((to) => {
  if (changesProjectScope(to)) return
  applySectionFromQuery(to.query.section)
  if (oauthReturnStatus(to.query.oauth_status)) {
    void refreshOAuthReturn(to.query.oauth_status, to.query.provider_key)
    return
  }
  if (typeof to.query.provider_key === 'string') openAddConnection(to.query.provider_key)
})
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Connections"
      description="Choose which reusable Accounts this project can use. Messaging profiles and webhooks stay project-bound."
      :breadcrumbs="[{ label: 'Connections' }]"
    >
      <template #actions>
        <UiButton variant="primary" size="sm" icon-left="plus" @click="openAddConnection()">
          Add connection
        </UiButton>
      </template>
    </ProjectPageHeader>

    <UiCallout v-if="error" tone="danger">
      {{ error }}
    </UiCallout>

    <UiCallout v-if="oauthReturnMessage" :tone="oauthReturnMessage.tone">
      {{ oauthReturnMessage.text }}
    </UiCallout>

    <section
      v-if="initialLoadComplete && !error"
      class="overflow-hidden rounded-lg border border-strong bg-bg-surface"
      aria-labelledby="connection-state-title"
    >
      <div class="grid gap-4 p-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <div>
          <p class="t-overline text-fg-subtle">Connection state</p>
          <h2
            id="connection-state-title"
            class="mt-1 text-xl font-semibold tracking-tight text-fg-strong"
          >
            {{
              attentionConnections.length > 0
                ? `${attentionConnections.length} ${attentionConnections.length === 1 ? 'connection needs' : 'connections need'} repair`
                : `${connectedServiceCount} ${connectedServiceCount === 1 ? 'service is' : 'services are'} ready`
            }}
          </h2>
          <p class="mt-2 max-w-3xl text-sm leading-6 text-fg-muted">
            <template v-if="attentionConnections.length > 0">
              Repair or re-test these accounts before an agent depends on them. Healthy services
              remain available.
            </template>
            <template v-else>
              Secrets remain in the local daemon. Connected agents receive only safe credential
              references.
            </template>
          </p>
        </div>
        <div v-if="attentionConnections.length > 0" class="flex flex-wrap gap-2 lg:justify-end">
          <UiButton @click="setActiveSection('services')"> Review services </UiButton>
        </div>
      </div>
      <div class="grid border-t border-border-subtle bg-bg-surface-alt sm:grid-cols-4">
        <button
          type="button"
          class="focus-ring-inset border-b border-border-subtle px-4 py-3 text-left transition hover:bg-bg-surface sm:border-b-0 sm:border-r"
          @click="setActiveSection('services')"
        >
          <span class="block text-2xs font-medium uppercase tracking-wide text-fg-subtle"
            >Services</span
          >
          <span class="mt-1 block text-sm font-semibold text-fg-strong"
            >{{ connectedServiceCount }} connected</span
          >
        </button>
        <button
          type="button"
          class="focus-ring-inset border-b border-border-subtle px-4 py-3 text-left transition hover:bg-bg-surface sm:border-b-0 sm:border-r"
          @click="setActiveSection('bots')"
        >
          <span class="block text-2xs font-medium uppercase tracking-wide text-fg-subtle"
            >Messaging identities</span
          >
          <span class="mt-1 block text-sm font-semibold text-fg-strong"
            >{{ communicationProfiles.length }} configured</span
          >
        </button>
        <button
          type="button"
          class="focus-ring-inset border-b border-border-subtle px-4 py-3 text-left transition hover:bg-bg-surface sm:border-b-0 sm:border-r"
          @click="setActiveSection('channels')"
        >
          <span class="block text-2xs font-medium uppercase tracking-wide text-fg-subtle"
            >Places</span
          >
          <span class="mt-1 block text-sm font-semibold text-fg-strong"
            >{{ communicationSurfaces.length }} visible</span
          >
        </button>
        <button
          type="button"
          class="focus-ring-inset px-4 py-3 text-left transition hover:bg-bg-surface"
          @click="setActiveSection('connectivity')"
        >
          <span class="block text-2xs font-medium uppercase tracking-wide text-fg-subtle"
            >Inbound messaging</span
          >
          <span
            class="mt-1 block text-sm font-semibold"
            :class="ingressStatus?.ready ? 'text-success-fg' : 'text-warning-fg'"
          >
            {{ ingressStatus?.ready ? 'Reachable' : 'Needs setup' }}
          </span>
        </button>
      </div>
    </section>

    <section
      v-else-if="!initialLoadComplete"
      class="grid gap-3 rounded-lg border border-strong bg-bg-surface p-5"
      aria-label="Loading connection state"
    >
      <UiSkeleton class="h-3 w-32" />
      <UiSkeleton class="h-7 w-72 max-w-full" />
      <UiSkeleton class="h-4 w-full max-w-2xl" />
    </section>

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
          aria-labelledby="cs-subnav-services"
          :hidden="activeSection !== 'services'"
        >
          <ConnectedServicesPanel
            :loading="loading"
            :error="error"
            :service-groups="serviceGroups"
            :connections-count="activeConnections.length"
            :connection-messages="connectionMessages"
            :busy-action="busyAction"
            @add-connection="openAddConnection"
            @manage-account="
              (connection) => router.push(`/accounts?account=${connection.credential_ref}`)
            "
            @detach-connection="requestDetach"
            @refresh="loadCredentials"
          />
        </div>

        <div role="tabpanel" aria-labelledby="cs-subnav-bots" :hidden="activeSection !== 'bots'">
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
            :ingress-status="ingressStatus ?? null"
            :loading="communicationSetupLoading"
            :syncing="busyAction === 'ingress:sync'"
            :message="ingressMessage ?? communicationSetupMessage"
            @refresh="loadCommunicationSetup"
            @configure="openIngressSetup"
            @sync="syncIngress"
            @confirm-manual="syncIngress"
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

    <AttachAccountPanel
      :model-value="attachPanelOpen"
      :selected-account-ref="selectedAccountRef"
      :account-options="accountOptions"
      :message="selectedAccountRef ? (connectionMessages[selectedAccountRef] ?? null) : null"
      :busy="Boolean(selectedAccountRef && busyAction === `${selectedAccountRef}:attach`)"
      @update:model-value="(open) => (open ? (attachPanelOpen = true) : closeAttachPanel())"
      @update:selected-account-ref="selectAccount(String($event ?? ''))"
      @attach="attachSelectedAccount"
      @create-account="openCreateAccount"
    />

    <AddAccountPanel
      :model-value="accountPanelOpen"
      :selected-provider="selectedProvider"
      :visible-auth-providers="visibleAuthProviders"
      :provider-options="providerOptions"
      :provider-messages="providerMessages"
      :field-errors="fieldErrors"
      :busy-action="accountBusyAction"
      :editing="editing"
      :secret-present="editingSecretPresent"
      :auth-methods="authMethods"
      :selected-method-key="selectedMethodKey"
      :selected-method="selectedMethod"
      :supports-credential="supportsCredential"
      :input-type="inputType"
      :is-secret-field="isSecretField"
      :method-fields="methodFields"
      :has-field-options="hasFieldOptions"
      :field-options="fieldOptions"
      :display-name-value="displayNameValue"
      :set-display-name-value="setDisplayNameValue"
      :field-value="fieldValue"
      :set-field-value="setFieldValue"
      @update:model-value="setAccountPanelOpen"
      @select-provider="setSelectedProvider"
      @select-method="setSelectedMethod"
      @start-provider="startProvider"
      @save-account="saveProjectAccount"
      @go-plugins="router.push(`/projects/${projectId}/plugins`)"
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

    <UiConfirmDialog
      :model-value="Boolean(pendingDetach)"
      title="Detach this Account?"
      :description="
        pendingDetach
          ? `${pendingDetach.display_name} will stop being available to this project. The Account remains available elsewhere.`
          : undefined
      "
      confirm-label="Detach Account"
      cancel-label="Keep attached"
      tone="danger"
      :loading="Boolean(pendingDetach && busyAction === `${pendingDetach.credential_ref}:detach`)"
      @update:model-value="
        (open) => {
          if (!open) pendingDetach = null
        }
      "
      @confirm="confirmDetach"
      @cancel="pendingDetach = null"
    />
  </UiPageShell>
</template>
