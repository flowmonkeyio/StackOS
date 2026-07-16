<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'

import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import SubNav from '@/components/SubNav.vue'
import { UiButton, UiCallout, UiConfirmDialog, UiPageShell, UiSkeleton } from '@/components/ui'
import AddConnectionPanel from './connections/AddConnectionPanel.vue'
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
import { providerLabel } from './connections/formatters'
import { useCommunicationTopology } from './connections/useCommunicationTopology'
import { useConnectionCredentials } from './connections/useConnectionCredentials'
import { useIngressEndpointEditor } from './connections/useIngressEndpointEditor'
import { useSlackProfileEditor } from './connections/useSlackProfileEditor'
import { useTelegramProfileEditor } from './connections/useTelegramProfileEditor'
import type { CommunicationProfile, ConnectionSection } from './connections/types'

const SECTION_KEYS: ConnectionSection[] = [
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

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const initialLoadComplete = ref(false)
const {
  authStatus,
  loading,
  error,
  addPanelOpen,
  busyAction,
  providerMessages,
  providerSetupUrls,
  connectionMessages,
  fieldErrors,
  pendingRevoke,
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
  profileValue,
  setProfileValue,
  labelValue,
  setLabelValue,
  setSelectedProvider,
  visibleAuthProviders,
  providerOptions,
  selectedProvider,
  activeConnections,
  connectedConnections,
  attentionConnections,
  serviceGroups,
  connectedServiceCount,
  load: loadCredentials,
  applyProviderSelection,
  openAddConnection,
  openEditConnection,
  saveCredential: saveCredentialAction,
  startProvider,
  testConnection,
  requestRevoke,
  confirmRevoke,
  connectionActionKey,
} = useConnectionCredentials(projectId)
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
  manualRoutes: manualIngressRoutes,
  load: loadIngressStatus,
  reset: resetIngressStatus,
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
    applyProviderSelection(route.query.provider_key)
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

function setAddPanelOpen(open: boolean): void {
  addPanelOpen.value = open
  if (!open) clearProviderQuery()
}

async function saveCredential(...args: Parameters<typeof saveCredentialAction>): Promise<void> {
  await saveCredentialAction(...args)
  if (!addPanelOpen.value) clearProviderQuery()
}

onMounted(load)
onBeforeRouteUpdate((to) => {
  const nextProjectId = Number.parseInt(String(to.params.id), 10)
  if (nextProjectId !== projectId.value) {
    addPanelOpen.value = false
    pendingRevoke.value = null
    telegramProfilePanelOpen.value = false
    slackProfilePanelOpen.value = false
    ingressSetupOpen.value = false
    resetIngressStatus()
    setTimeout(() => void load(), 0)
    return
  }
  applySectionFromQuery(to.query.section)
  applyProviderSelection(to.query.provider_key)
})
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Connections"
      description="Add, test, and repair the services connected agents can use. Messaging topology and diagnostics stay secondary."
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

    <UiCallout
      v-if="manualIngressRoutes.length > 0"
      tone="warning"
      :title="`${providerLabel(manualIngressRoutes[0].provider_key)} webhook needs manual update`"
    >
      {{ manualIngressRoutes[0].profile_key }} needs its webhook URL copied into the provider
      console.
      <template #actions>
        <UiButton variant="secondary" size="sm" @click="setActiveSection('connectivity')">
          Review connectivity
        </UiButton>
      </template>
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
          aria-labelledby="cs-subnav-services"
          :hidden="activeSection !== 'services'"
        >
          <ConnectedServicesPanel
            :loading="loading"
            :service-groups="serviceGroups"
            :connections-count="activeConnections.length"
            :connection-messages="connectionMessages"
            :busy-action="busyAction"
            @add-connection="openAddConnection"
            @edit-connection="openEditConnection"
            @test-connection="testConnection"
            @revoke-connection="requestRevoke"
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
      :model-value="addPanelOpen"
      :selected-provider="selectedProvider"
      :visible-auth-providers="visibleAuthProviders"
      :provider-options="providerOptions"
      :provider-messages="providerMessages"
      :provider-setup-urls="providerSetupUrls"
      :field-errors="fieldErrors"
      :busy-action="busyAction"
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
      :profile-value="profileValue"
      :set-profile-value="setProfileValue"
      :label-value="labelValue"
      :set-label-value="setLabelValue"
      :field-value="fieldValue"
      :set-field-value="setFieldValue"
      @update:model-value="setAddPanelOpen"
      @select-provider="setSelectedProvider"
      @select-method="setSelectedMethod"
      @start-provider="startProvider"
      @save-credential="saveCredential"
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
      :model-value="Boolean(pendingRevoke)"
      title="Revoke this connection?"
      :description="
        pendingRevoke
          ? `Agents will immediately lose access to ${providerLabel(pendingRevoke.provider_key)} (${pendingRevoke.label || pendingRevoke.profile_key}).`
          : undefined
      "
      confirm-label="Revoke connection"
      cancel-label="Keep connection"
      tone="danger"
      :loading="
        Boolean(
          pendingRevoke &&
          busyAction === connectionActionKey(pendingRevoke.credential_ref, 'revoke'),
        )
      "
      @update:model-value="
        (open) => {
          if (!open) pendingRevoke = null
        }
      "
      @confirm="confirmRevoke"
      @cancel="pendingRevoke = null"
    />
  </UiPageShell>
</template>
