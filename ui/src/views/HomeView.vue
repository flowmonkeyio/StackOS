<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { UiButton, UiConfirmDialog, UiPageHeader, UiPageShell, UiSegmentedControl } from '@/components/ui'
import { isDesktopShell } from '@/lib/desktop'
import { GETTING_STARTED_URL } from '@/lib/externalLinks'
import { useStackOsCatalogStore } from '@/stores/plugins'
import ActiveTaskList from './home/ActiveTaskList.vue'
import DeliveryCapacityControl from './home/DeliveryCapacityControl.vue'
import HomeConnectionsSection from './home/HomeConnectionsSection.vue'
import HomeSystemStatusCard from './home/HomeSystemStatusCard.vue'
import { useHomeAgentHostStatuses } from './home/useHomeAgentHostStatuses'
import { useHomeSystemStatus } from './home/useHomeSystemStatus'
import AgentActivitySection from './home/AgentActivitySection.vue'
import { useAgentStats } from './home/useAgentStats'

const isShell = isDesktopShell()
const repairOpen = ref(false)
const activeTasks = ref<InstanceType<typeof ActiveTaskList> | null>(null)
const capacityControl = ref<InstanceType<typeof DeliveryCapacityControl> | null>(null)
const catalog = useStackOsCatalogStore()
const accounts = computed(() => catalog.globalAccountsStatus?.accounts ?? [])
const agents = useAgentStats()
const usageVisibility = ref<boolean | null>(true)
function setUsageVisibility(value: string | number): void {
  usageVisibility.value = value === 'all' ? null : value === 'active'
  void agents.setVisibility(usageVisibility.value)
}
const { summary: agentSummary, loading: agentsLoading, error: agentsError, filters: agentFilters } = agents
const { hostStatuses, hostStatusSummary, loadHostStatuses, applyHostStatuses } =
  useHomeAgentHostStatuses(isShell)
const { health, systemBusy, statusTone, statusLabel, systemFacts, loadHealth, runSystemAction } =
  useHomeSystemStatus({ onDoctorResult: applyHostStatuses, onRepairComplete: loadHostStatuses })

onMounted(() => {
  void agents.load()
  void loadHealth()
  void loadHostStatuses()
  void catalog.refreshAccounts()
})

async function refresh(): Promise<void> {
  await Promise.all([activeTasks.value?.refresh(), capacityControl.value?.refresh(), catalog.refreshAccounts(), agents.load(), loadHealth(), loadHostStatuses()])
}
function confirmRepair(): void {
  repairOpen.value = false
  void runSystemAction('repair')
}
</script>

<template>
  <UiPageShell>
    <UiPageHeader
      title="Overview"
      description="Your local runtime and projects"
    >
      <template #actions>
        <DeliveryCapacityControl ref="capacityControl" />
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="refresh"
          :loading="catalog.loading || agentsLoading"
          @click="refresh"
        >
          Refresh
        </UiButton>
        <UiButton
          :href="GETTING_STARTED_URL"
          target="_blank"
          rel="noopener noreferrer"
          variant="ghost"
          size="sm"
          icon-right="external-link"
          aria-label="Open the Getting Started guide in your browser"
        >
          Getting started
        </UiButton>
      </template>
    </UiPageHeader>
    <HomeSystemStatusCard
      :health="health"
      :is-shell="isShell"
      :status-tone="statusTone"
      :status-label="statusLabel"
      :system-facts="systemFacts"
      :system-busy="systemBusy"
      :host-statuses="hostStatuses"
      :host-status-summary="hostStatusSummary"
      @restart="runSystemAction('restart')"
      @doctor="runSystemAction('doctor')"
      @repair="repairOpen = true"
      @refresh-hosts="loadHostStatuses"
    />
    <ActiveTaskList ref="activeTasks" />
    <AgentActivitySection
      :summary="agentSummary"
      :loading="agentsLoading"
      :error="agentsError"
      :filters="agentFilters"
      global
      @filters="agents.setFilters"
    >
      <template #scope>
        <div class="flex flex-col justify-end gap-1">
          <span class="text-xs text-fg-muted">Projects</span>
          <UiSegmentedControl
            :model-value="usageVisibility === null ? 'all' : usageVisibility ? 'active' : 'archived'"
            :options="[{ key: 'active', label: 'Active' }, { key: 'archived', label: 'Archived' }, { key: 'all', label: 'All' }]"
            label="Project visibility"
            @update:model-value="setUsageVisibility"
          />
        </div>
      </template>
    </AgentActivitySection>
    <HomeConnectionsSection
      :accounts="accounts"
      :loading="catalog.loading"
      :error="catalog.error"
    />
    <UiConfirmDialog
      v-model="repairOpen"
      title="Install or repair StackOS?"
      description="This reinstalls local StackOS assets and restarts the service. It's safe to run, but the service will be briefly unavailable."
      confirm-label="Install or repair"
      cancel-label="Cancel"
      tone="primary"
      @confirm="confirmRepair"
      @cancel="repairOpen = false"
    />
  </UiPageShell>
</template>
